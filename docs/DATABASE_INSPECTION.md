# Database Inspection

The MVP uses SQLite. That is the right storage choice for the local-first version because it is durable, simple, easy to back up, and easy to inspect without running another service.

## Quick Inspection

```powershell
.\scripts\ybm.ps1 db inspect
```

Prints row counts per table and the task status breakdown. `ybm db clean --days N` prunes
tasks (and their child records) older than N days; `ybm db reset --yes` wipes everything.

## VS Code Viewer

Recommended extension:

```text
qwtel.sqlite-viewer
```

VS Code should suggest it from `.vscode/extensions.json`. The Marketplace page describes it as a SQLite viewer that opens `.sqlite` and `.db` files directly in VS Code, with filtering and sorting support.

Source: https://marketplace.visualstudio.com/items?itemName=qwtel.sqlite-viewer

## Open The Local Database

Default path:

```text
agent_control.db
```

Open that file in VS Code after installing SQLite Viewer.

## Tables To Inspect First

- `tasks`: spawned tasks, status, metadata, and plan linkage.
- `messages`: normalized inbound Telegram messages.
- `audit_events`: important decisions, access checks, classifications, and failures.
- `plans`: persisted structured plans.
- `approvals`: pending and decided approval requests.
- `tool_invocations`: requested tools and results.

## Admin Summary

The admin UI also exposes database visibility:

```text
GET /admin/api/database/summary
```

It returns the database path, table counts, and recent activity timestamps.
