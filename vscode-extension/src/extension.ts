import * as vscode from "vscode";

export function activate(context: vscode.ExtensionContext) {
  const terminals = new Map<string, vscode.Terminal>();
  const statusCommand = vscode.commands.registerCommand("agentControl.showStatus", () => {
    vscode.window.showInformationMessage("Agent Control Bridge is installed.");
  });
  const syncCommand = vscode.commands.registerCommand("agentControl.syncState", () => syncState());
  const terminalCommand = vscode.commands.registerCommand("agentControl.createTerminal", () => {
    const terminal = vscode.window.createTerminal("Agent Control");
    terminals.set("agent-control", terminal);
    terminal.show();
  });

  const timer = setInterval(() => {
    void sendHeartbeat();
    void pollTerminalCommands(terminals);
  }, 30000);

  context.subscriptions.push(statusCommand, syncCommand, terminalCommand, {
    dispose: () => clearInterval(timer),
  });
  void sendHeartbeat();
  void pollTerminalCommands(terminals);
}

export function deactivate() {}

function bridgeUrl(): string {
  return vscode.workspace.getConfiguration("agentControl").get("bridgeUrl", "http://127.0.0.1:8765");
}

function bridgeToken(): string | undefined {
  return process.env.VSCODE_BRIDGE_TOKEN;
}

async function sendHeartbeat(): Promise<void> {
  await post("/vscode/heartbeat", await collectState());
}

async function syncState(): Promise<void> {
  await post("/vscode/state", await collectState());
}

async function collectState() {
  const workspaceFolders = vscode.workspace.workspaceFolders?.map((folder) => folder.uri.fsPath) ?? [];
  const activeFile = vscode.window.activeTextEditor?.document.uri.fsPath;
  const openFiles = vscode.workspace.textDocuments
    .filter((document) => document.uri.scheme === "file")
    .map((document) => document.uri.fsPath);
  const diagnosticsCount = vscode.languages.getDiagnostics().reduce((total, item) => total + item[1].length, 0);

  return {
    instance_id: vscode.env.machineId,
    workspace_folders: workspaceFolders,
    active_file: activeFile,
    open_files: openFiles,
    diagnostics_count: diagnosticsCount,
    metadata: { app_name: vscode.env.appName, bridge_url: bridgeUrl() },
  };
}

async function post(path: string, payload: unknown): Promise<void> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const token = bridgeToken();
  if (token) {
    headers["X-Agent-Control-Token"] = token;
  }
  await fetch(`${bridgeUrl()}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
}

async function get<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {};
  const token = bridgeToken();
  if (token) {
    headers["X-Agent-Control-Token"] = token;
  }
  const response = await fetch(`${bridgeUrl()}${path}`, { headers });
  return (await response.json()) as T;
}

type TerminalCommand = {
  id: string;
  terminal_id: string;
  command: string;
  cwd?: string;
  capture_output?: boolean;
};

async function pollTerminalCommands(terminals: Map<string, vscode.Terminal>): Promise<void> {
  const commands = await get<TerminalCommand[]>(
    `/vscode/terminal-commands?instance_id=${encodeURIComponent(vscode.env.machineId)}`,
  );
  for (const command of commands) {
    await dispatchTerminalCommand(terminals, command);
  }
}

async function dispatchTerminalCommand(
  terminals: Map<string, vscode.Terminal>,
  command: TerminalCommand,
): Promise<void> {
  let terminal = terminals.get(command.terminal_id);
  if (!terminal) {
    terminal = vscode.window.createTerminal({
      name: command.terminal_id,
      cwd: command.cwd,
    });
    terminals.set(command.terminal_id, terminal);
  }

  terminal.show();
  if (command.capture_output !== false && (await runWithShellIntegration(terminal, command))) {
    return;
  }

  terminal.sendText(command.command, true);
  await reportTerminalOutput(command, `dispatched:${command.id}\nTerminal output capture unavailable. Enable VS Code shell integration to capture command output.`, true);
}

async function runWithShellIntegration(terminal: vscode.Terminal, command: TerminalCommand): Promise<boolean> {
  const integration = await waitForShellIntegration(terminal);
  if (!integration) {
    return false;
  }

  const execution = integration.executeCommand(command.command);
  let content = "";
  const endPromise = new Promise<number | undefined>((resolve) => {
    const disposable = vscode.window.onDidEndTerminalShellExecution((event) => {
      if (event.execution === execution) {
        disposable.dispose();
        resolve(event.exitCode);
      }
    });
  });

  for await (const chunk of execution.read()) {
    content += chunk;
    if (content.length > 12000) {
      content = content.slice(content.length - 12000);
    }
  }

  const exitCode = await endPromise;
  await reportTerminalOutput(command, content || `completed:${command.id}`, true, exitCode);
  return true;
}

async function waitForShellIntegration(terminal: vscode.Terminal): Promise<vscode.TerminalShellIntegration | undefined> {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (terminal.shellIntegration) {
      return terminal.shellIntegration;
    }
    await delay(100);
  }
  return undefined;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function reportTerminalOutput(
  command: TerminalCommand,
  content: string,
  isFinal: boolean,
  exitCode?: number,
): Promise<void> {
  await post("/vscode/terminal-output", {
    instance_id: vscode.env.machineId,
    terminal_id: command.terminal_id,
    command_id: command.id,
    content,
    is_final: isFinal,
    exit_code: exitCode,
  });
}
