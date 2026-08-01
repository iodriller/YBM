---
name: Weekly Status Report
description: Turn recent completed tasks and remembered facts into a short status report.
version: '1'
tools:
- task.status
- memory.manage
- document.manage
---

When asked for a weekly report, status update, or summary of recent work:

1. Use `task.status` to pull recent completed (and failed) tasks - this is the real record of
   what happened, not a guess from conversation memory alone.
2. Check `memory.manage` (operation `list`) for any pinned facts relevant to ongoing projects, so
   the report reflects known context, not just this week's raw task list.
3. Group the output by outcome, not chronologically: what shipped/completed, what's still
   in progress or blocked, what failed and why. A report that hides failures is worse than no
   report.
4. Keep it short - a status report nobody reads because it's too long has failed at its one job.
   Aim for a page a person can read in under a minute: a few bullets per section, not a
   restatement of every task's full trace.
5. If asked to produce an actual document (not just chat text), use `document.manage` to generate
   a real file rather than only describing what the report would contain.
