# First run: what actually happens, and the plan to fix it

> **Status: all of P1-P5 implemented.** Each section below keeps the evidence
> that motivated it; the fix is noted inline.

Written after installing YBM the way a new user would - `docker build`,
`docker run`, open the console - and screenshotting every step. Everything
below was observed, not inferred.

Companion to `docs/INSTALL_UX_PLAN.md` (getting the software onto the machine).
This one starts where that ends: the first thing a person sees.

## Four things were broken before a plan was possible

Running the container found these; none were visible from reading the code.

| | What happened | Fixed |
|---|---|---|
| The container had no UI at all | `/admin` served "No admin console build was found yet. Run `ybm ui-build`" - an instruction impossible to follow inside a container with no `frontend/` and no node. `.dockerignore` excluded `frontend/`, and nothing ever built the console. | A `frontend` build stage now compiles the console into the image |
| The container killed itself | It started, reported healthy, then exited. `telegram_polling` raises `Telegram token not found` on a fresh install, and `run_foreground` treated any dead service as fatal | Telegram is gated on being enabled *and* having a token, exactly as WhatsApp already was; only a **required** service is now fatal |
| `ybm start --foreground` crashed | `TypeError: build_service_specs() got an unexpected keyword argument 'open_browser'` | `open_browser` is consumed by `run_foreground` rather than forwarded |
| A dead optional service took the stack down | Same root cause as the second row | An optional service exiting now logs a warning and the rest keep running |

The image builds and runs: healthy in 4 seconds, four services, console reachable.

## What a new user meets, and what is wrong with it

### The first screen

> **Welcome to YBM Control**
> A few quick choices - skippable at every step, re-runnable later from Settings.
> **1. Pick a brain**
> `LocalDeploy Qwen3-VL 8B (recommended)` · `LocalDeploy Gemma 3 12B` ·
> `LocalDeploy Gemma 3 4B` · `OpenAI GPT-4.1`
> ▸ Or paste an API key
> `Skip`

**F1 - Three of the four options are named after an internal project.**
"LocalDeploy" means nothing to someone who just installed this. Nothing says
which options are free, which need a paid key, which need a download, or how
big that download is.

**F2 - In a container, the recommended default is broken.** All three
LocalDeploy presets point at `http://127.0.0.1:8000/v1`, which inside a
container is the container itself. The one option that *would* work - an API
key - is collapsed behind a disclosure triangle. The recommended path is the
one that cannot work.

**F3 - No detection feedback.** The wizard already calls `/api/setup/detect`.
It does not say "no local model server found" or "Ollama is running with no
models". It lists four choices with equal confidence and lets the user discover
the failure later.

**F4 - "1." implies steps but there is no progress.** No "step 1 of 3", no
indication that there are exactly two questions.

**F5 - `Skip`'s consequence is unstated.** Skipping the brain leaves a console
that cannot answer anything, and nothing says so.

### The second screen - and the trap

> **2. Pick a face**
> Web chat works with zero setup - you're using it right now.
> ☐ Also enable Telegram → `Bot token` (a bare password field)

**F6 - Enabling Telegram here produces a bot that ignores you, silently.**
This is the most serious finding.

`channels/telegram.py::_authorization_decision` fails closed:

```python
if not has_user_allowlist and not has_chat_allowlist:
    return False, "allowlist_empty"
```

The wizard collects a **token only**. It never collects, and never sends,
`allowed_user_ids` or `allowed_chat_ids` - verified in the payload it submits.
So the documented happy path produces a bot that is running, connected, and
refuses every message the user sends it, with the reason recorded in an audit
event they have no reason to look at.

The security posture is right. The onboarding is what is wrong.

**F7 - No guidance for the one thing that needs it.** The field says "Bot
token" and nothing else. Getting one means knowing to message `@BotFather`,
send `/newbot`, name it, and copy a string. Finding your own numeric user id
means knowing about `@userinfobot` or similar. A person who has never made a
Telegram bot cannot complete this step from what is on screen.

## The plan

### P1 - Make Telegram setup hand-held - DONE

Replace the token field with a short guided sequence. Concretely:

1. **"Open Telegram and message @BotFather"** - as a tappable `https://t.me/BotFather`
   link, with the exact text to send (`/newbot`) shown as copyable.
2. **"Paste the token he gives you"** - validate on paste by calling
   `getMe`, and show the bot's own name back: *"Connected to @your_bot."*
   That single echo turns a blind paste into a confirmed step.
3. **"Now message your bot"** - and have the backend watch for it. The first
   inbound message supplies the user id automatically; fill the allowlist from
   it and show *"Linked to @yourhandle."* No number ever has to be found or
   typed.
4. Only then mark Telegram configured.

Step 3 is what closes F6, and it removes the hardest instruction rather than
documenting it. Fall back to a manual id field for anyone who wants it.

**Backend needed:** a `verify` call for the token (`getMe`), and a bounded
"wait for the first message" endpoint that returns the sender id.

### P2 - Say what the choices mean - DONE

- Rename presets in user terms: *"Qwen3-VL 8B - free, runs on this machine
  (~5 GB download)"*, *"OpenAI GPT-4.1 - needs a paid API key"*. Keep the
  profile name as secondary text for anyone who cares.
- Report detection plainly above the list: *"No local model server found on
  this machine"* / *"Ollama is running - 3 models installed"*.
- Disable, with a reason, what cannot work: in a container, the loopback
  presets should read *"not reachable from inside the container - use
  host.docker.internal or an API key"* rather than fail after selection.
- Promote "paste an API key" out of the disclosure when nothing local is
  reachable.

### P3 - Make the container's default actually work - DONE

The compose file already sets `host.docker.internal:host-gateway`. Add a preset
that uses it, and select that one when `YBM_HEADLESS=1` and a model server
answers there. A containerised install should reach a host Ollama without the
user knowing what a gateway is.

### P4 - Progress and consequences - DONE

"Step 1 of 2" instead of "1.". On `Skip`, one line of what it costs: *"You can
add this later in Settings - until you pick a model, YBM cannot answer
anything."*

### P5 - End on something that works - DONE

The final screen should not be "You're set." It should be a first message
already typed and ready to send, so the user's first act is a working round
trip rather than an empty chat box.

## Order

P1 first - it is the only finding that produces a silently broken install, and
it is the one you asked about. P2 is mostly copy and one endpoint's output. P3
is small once P2 exists. P4 and P5 are polish.

## Reproducing

```
docker build -t ybm-control:test .
docker run -d --name ybm-test -p 127.0.0.1:8899:8765 \
  -e AGENT_ADMIN_TOKEN=<anything> ybm-control:test
# then open http://127.0.0.1:8899/admin?token=<the same>
```

Screenshots for each step were taken with Playwright, already a `frontend` dev
dependency.

## Not verified

- `docker compose up` was not run; only `docker build` and `docker run`. The
  compose file's volumes and the optional Ollama profile are still unexercised.
- No message was actually sent through a real Telegram bot. F6 is read from
  `_authorization_decision` and from the payload the wizard submits, both of
  which are unambiguous, but the end-to-end silence was not observed.

## What landed

- `POST /api/setup/telegram/verify` calls `getMe` and names the bot back, so a
  pasted token is confirmed instead of accepted blindly.
- `POST /api/setup/telegram/await-first-message` long-polls for the user's own
  first message and returns the sender id, which fills the allowlist. Nobody
  has to find a numeric id, and **Continue stays disabled until the link is
  confirmed** - enabling Telegram can no longer produce a bot that ignores its
  owner.
- Presets read as "Qwen3-VL 8B - free, runs on this machine" rather than
  "LocalDeploy Qwen3-VL 8B", and a `host.docker.internal` preset exists for
  containerised installs, where every loopback preset is unreachable.
- The wizard states what it detected ("No local model server found on this
  machine..."), shows "Step 1 of 2", says what skipping costs, and ends with a
  suggested first message instead of an empty chat box.

Verified by rebuilding the image and walking the wizard again: 919 backend
tests, ruff clean, tsc clean, container healthy in 4s, and the guided Telegram
sequence renders with Continue correctly disabled until linked.

Still not verified: `docker compose up`, and no message has been sent through a
real bot - the two new endpoints are covered by tests against a mocked Telegram
API, not against Telegram itself.
