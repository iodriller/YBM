# Bugs and gaps

From tracing the voice report you filed, then looking for the same shapes
elsewhere. Fixed items are marked; the rest are ordered by how likely they are
to bite a real user.

---

## The voice bug — fixed

**What you saw:** you sent a voice note to Telegram and it did not work.

**What was happening.** The pipeline was fully implemented — Telegram
normalizes voice into `MessageKind.VOICE`, downloads the file, and transcribes
it. Two things went wrong on top of that:

1. **Speech-to-text is off by default** (`STTAdapterConfig.enabled = False`),
   so `DisabledSTTAdapter.transcribe` raised.
2. **The reply was the exception.** You got
   `Voice transcription failed: RuntimeError: STT adapter is disabled` —
   a class name and an internal component, calling a switched-off feature a
   failure, with nothing to do about it.

**Now:** *"I can't listen to voice messages yet — speech-to-text is turned off
in this setup. Send it as text and I'll get straight on it, or turn on voice
under Settings."* The diagnostic stays in the audit trail.

### The same leak, two more places — fixed

- `telegram.py` replied `Screenshot capture failed: {exc}`.
- `telegram_notifications.py` sent `Error: {str(exc)}` when a screenshot could
  not be delivered.

Both now go through `explain_for_user()`, which maps known failure shapes —
401, 429, 5xx, connection refused, timeout, empty allowlist, missing base URL,
refusal — to a sentence on what happened and a sentence on what to do.

---

## Open gaps

### G1 — Voice in web chat does not exist

Confirmed: no microphone, no recorder, nothing in `ChatPage.tsx`. Voice works
on Telegram only, which is backwards — the console is where someone would try
it first.

The backend half is done: `tools/stt.py` has the adapter protocol and
`/api/chat/attachments` already accepts uploads. What is missing is a record
button that captures audio and posts it to a transcription endpoint.

**Worth saying:** this is a *speech-to-text* feature, not a model capability.
Local models served through LocalDeploy do not take audio; transcription is a
separate step that turns a recording into text before the model ever sees it.
So the honest message when it is off is *"speech-to-text is turned off in this
setup"* — not *"the model can't hear you"*.

### G2 — Turning voice on is not possible from the console

`STTAdapterConfig` is `enabled: False` and there is no UI for it, so the reply
above tells the user to "turn on voice under Settings" — where there is nothing
to turn on. Either add the toggle or change the wording; the current pair is a
promise the console cannot keep.

### G3 — The `voice` extra is optional and silent about it

`faster-whisper` lives under `[voice]` in `pyproject.toml`. Enabling STT
without installing the extra fails at first use rather than at startup, and
nothing checks. `doctor` should say so.

### G4 — Nothing tells the user a task failed, in general

`explain_for_user` now exists but is only wired into the three places above.
The general worker-failure path still surfaces `last_worker_error`, which is a
`describe_exception` string. That is the largest remaining source of
computer-speak.

### G5 — Results that look wrong are reported as if fine

From `docs/VOICE_PLAN.md`, repeated because it is the highest-value item here:
a recorded run reported *"a total of 0"* for a file of expenses without
comment. The Auditor already checks whether the objective was met; it should
also ask whether the answer is plausible.

### G6 — The starter prompts have never been run

The three chat suggestions ship untested. A suggestion that fails on click is
worse than no suggestion. They need a configured model to verify.

### G7 — Nothing below 1280px has been checked

Carried over from the UI plan. "Message YBM from your phone" is a headline
feature and the console's own phone behaviour is unverified.

---

## Order

| | Item | Cost |
|---|---|---|
| 1 | **G4** — route worker failures through `explain_for_user` | Small; the function exists |
| 2 | **G2** — a voice toggle in Settings, or honest wording | Small |
| 3 | **G1** — record button in web chat | Medium |
| 4 | **G5** — plausibility in the Auditor | Medium, and needs a re-record |
| 5 | **G3** — `doctor` check for the voice extra | Small |
| 6 | **G6, G7** — verification passes | Needs a model / a phone |

## Not verified

- The voice fix is covered by tests against a simulated STT failure. I have not
  sent a real voice note through a real bot — that needs your token.
- G4–G7 are diagnosed from code, not reproduced at runtime.
