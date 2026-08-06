# YBM Threat Model

## Scope and assumptions

YBM is a local, single-operator agent-control system. The intended deployment
has one trusted operator, loopback-bound services, and an operating-system
account that protects local configuration and runtime data. Internet exposure,
untrusted local users, and multi-tenant use are outside the supported security
model.

This document describes the current implementation. It is not a claim that
model output or external content can be made trustworthy.

## Assets

- API keys, Telegram credentials, bridge/admin tokens, and vault keys.
- Local files, source repositories, terminal and desktop authority.
- Browser sessions and authenticated web content.
- MCP server configuration and credentials.
- Task history, conversation memory, audit records, screenshots, and artifacts.
- Integrity of tool definitions, capability policy, approvals, and generated
  adapter code.

## Trust boundaries

The operator and local configuration are trusted. The following are untrusted
data even when they look like instructions:

- LLM responses;
- web pages, HTTP responses, MCP results, files, documents, and tool output;
- Telegram messages from any identity not explicitly allowlisted;
- generated code and generated adapter proposals; and
- conversation memory derived from any of the sources above.

An enabled external tool is not automatically a trusted tool. Its result may be
malicious, stale, malformed, or controlled by another party.

## Security invariants

YBM's enforcement layer is deterministic:

1. The runtime tool definition owns each tool's capability and minimum
   operation risk. A model cannot substitute a lower-risk capability or
   understate the operation's risk.
2. A capability must be enabled, within its configured scope and patterns, and
   at or below its maximum risk.
3. The global approval floor remains authoritative even when a capability's
   `requires_approval` setting is false.
4. Persistent and critical operations can require approval independently of an
   access-mode preset. This includes MCP server installation, generated-adapter
   promotion, active desktop/browser control, generated or unsandboxed code
   execution, and HTTP requests that transmit vault secrets.
5. An approval is bound to one task, tool, capability, validated input, scope,
   risk, timeout, and approval requirement. It expires and is atomically
   consumed before one dispatch; changing parameters or replaying it fails
   closed.
6. Access modes configure capability availability and scope. They do not grant
   or fabricate human approvals.

The example configuration additionally keeps terminal, filesystem writes,
browser/desktop control, dependency installation, and Git push disabled by
default. Network requests require explicit host allowlisting. Audit logging
redacts configured secret-shaped fields, but redaction is not a substitute for
keeping secrets out of prompts and tool output.

## Principal threats and controls

### Indirect prompt injection and goal hijacking

Untrusted content can attempt to redirect an LLM. Runtime-owned capabilities,
risk levels, scopes, and approvals limit what a successful injection can do.
They do not make the injected content safe or guarantee that the model will
ignore it.

Keep high-impact capabilities disabled unless needed, prefer narrow scopes and
allowlists, and review the exact approval payload. Do not treat “Full Access”
as a safe mode for browsing or processing untrusted content.

### Memory poisoning

Tool-derived summaries may persist in conversation context. Treat recalled
content as untrusted data, avoid storing secrets in tasks, and clear local
conversation state after processing known-malicious content. Persistent memory
provenance and automated poisoning detection remain open hardening work.

### Excessive agency and unsafe tool use

Capability policy, operation risk, bounded retries/steps, exact approvals, and
the kill switch reduce unintended actions. The operator remains responsible
for the configured scopes and for approving the exact operation shown.

### Generated or unsandboxed code

Docker execution, when enabled and available, is the preferred boundary for
untrusted/generated Python. Local-subprocess fallback executes with the YBM
process account's authority and therefore requires approval. Docker isolation
is defense in depth, not a security boundary equivalent to a separate host or
virtual machine.

### MCP and generated adapters

Installing an MCP server changes persistent configuration, and promoting an
adapter adds executable code to the live registry. Both require an exact,
one-shot approval. Review the package/source, command, environment variables,
declared tool risks, and generated files before approval. Use a separate OS
account or VM for untrusted servers and adapters.

### Secret disclosure

Secrets should enter tools through environment variables or vault references,
not task text. HTTP requests containing secret references require approval.
Local `.env`, configuration, databases, logs, screenshots, and `.agent_control/`
must not be published. CI scans the full Git history with Gitleaks; findings
still require credential revocation and history cleanup, not merely deleting
the current file.

### Local control-plane exposure

Default service binds are loopback-only. Binding to a non-loopback interface
without strong authentication and a network boundary can expose terminal,
filesystem, browser, desktop, task, and secret-adjacent functionality. YBM is
not hardened as a public multi-user API.

### Supply-chain compromise

GitHub Actions are pinned to immutable commit SHAs, lockfiles are committed,
and dependency audit jobs run in CI. Dependency updates are reviewed and tested
manually, with breaking security-sensitive upgrades handled as focused changes.

## Public-release checklist

Before changing repository visibility:

- choose and add an explicit open-source license;
- run the complete deterministic test and quality suites;
- run a full-history secret scan and revoke/rewrite any finding;
- review tracked files and release artifacts for private data;
- resolve failing dependency-update pull requests;
- enable private vulnerability reporting;
- protect `main` with pull-request review and required CI checks; and
- verify that branch deletion and force pushes are restricted.

Repository visibility must not be changed as part of this checklist without an
explicit publication decision by the owner.

## References

- [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
- [GitHub: Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
