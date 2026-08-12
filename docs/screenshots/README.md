# Screenshots

`demo.gif` is the README's opening demo.

## Regenerating demo.gif

The demo is recorded against **mocked API responses**, not a live backend, so it
costs nothing to re-record, needs no model, and produces the same frames on any
machine:

```
cd frontend && YBM_RECORD_DEMO=1 npx playwright test demo.spec.ts
cd .. && backend/.venv/Scripts/python scripts/make_demo_gif.py
```

- [`frontend/e2e/demo.spec.ts`](../../frontend/e2e/demo.spec.ts) owns *what the
  console shows*: the story beats and every response behind them.
- [`scripts/make_demo_gif.py`](../../scripts/make_demo_gif.py) owns *how long
  each beat is held*. Re-timing the demo does not mean re-recording it.

Frames land in `frontend/.demo-frames/` (gitignored); only the assembled GIF is
committed.

## Why it is scripted

The previous GIF was captured from a live session, which made it
unreproducible and pinned the story to whatever that run happened to do - it
ended up being the same trivia question asked several times, which sells YBM as
a chat window rather than an agent that works on your computer.

The current beats are chosen to say the opposite, in order:

| Beat | What it has to land |
|---|---|
| Empty chat | Every suggestion is work on this machine, not a question to answer |
| Typing, sending | A person asks in their own words |
| Working | It is doing something real, and says what |
| **Approval** | It stops and asks *before* touching files - the longest hold in the GIF |
| Done | A receipt naming every file it moved, and that nothing was deleted |
| Tasks | A history of varied real work, so the range is visible |

If you change the demo, keep the approval beat. It is the difference between
"an agent ran a command" and "an agent asked first".
