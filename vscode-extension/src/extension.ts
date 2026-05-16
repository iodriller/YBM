import * as vscode from "vscode";

export function activate(context: vscode.ExtensionContext) {
  const disposable = vscode.commands.registerCommand("agentControl.showStatus", () => {
    vscode.window.showInformationMessage("Agent Control Bridge is installed.");
  });

  context.subscriptions.push(disposable);
}

export function deactivate() {}

