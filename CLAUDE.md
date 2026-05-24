# CLAUDE.md

Persistent guidance for Claude Code when working in this project.

## Working Style

- Explore first, plan briefly, then edit.
- Keep context narrow and relevant to the task.
- Define success before implementing, testing, debugging, or researching.
- Make the smallest safe change that solves the real problem.
- Verify results before reporting success.
- Do not claim success without observed evidence.

## Minimal Implementation

- Prefer minimal, focused implementations.
- Do not over-engineer, broaden scope, or refactor unless needed.
- Preserve existing behavior and architecture unless the task requires changing them.
- Suggest larger improvements separately instead of mixing them into the immediate fix.

## Holistic Solutions

- Solve the underlying pattern, not one prompt or one example.
- Avoid case-by-case logic in both code and prompts.
- Avoid long if/else chains that patch individual failures.
- Prefer general mechanisms, reusable abstractions, and clear principles.
- If repeated fixes point to a deeper design issue, call it out.

## Accuracy

- Do not hallucinate APIs, files, commands, test results, logs, or project behavior.
- Inspect before changing. Run before concluding. Log before fixing.
- Clearly separate observed facts, inferences, and unknowns.
- If something was not verified, say so.

## Local Runtime

- This project runs locally.
- Wait appropriately for local services, tests, and E2E flows to start and respond.
- Do not assume failure only because something is slow.
- Use readiness checks, logs, ports, and process output before deciding what happened.

## Testing and Debugging

- During E2E, integration, or non-trivial test runs, log intermediate values.
- Capture inputs, outputs, generated data, IDs, request/response summaries, state changes, errors, and timestamps when useful.
- Keep enough trail to debug failures without rerunning blindly.
- Use the logs to diagnose before changing code.

## Prompts

- Treat prompt changes like code changes.
- Keep prompt edits minimal, holistic, and non-branchy.
- Do not add many narrow rules for individual examples.
- If a prompt becomes repetitive or exception-heavy, suggest a structural change.

## Reporting Back

When finishing, summarize:

- What changed
- What was verified
- What was not verified
- Any remaining risk or unknown
- Any architectural suggestion worth considering separately

## Maintenance

- Keep this file concise.
- Add only stable instructions that should apply every session.
- Move long repeated procedures into a Skill or separate project document.
