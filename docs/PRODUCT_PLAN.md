# Product plan: setup honesty, chat power, and naming

Supersedes `docs/archive/UI_UX_IMPROVEMENT_PLAN.md`, which is folded in below as
section 6. Ordered by your priorities, not by size.

Evidence is `docs/screenshots/first-run/`, captured from a containerised
install with nothing pre-configured.

---

## 0. Yes — web chat is a channel

Confirming the mental model, because it is already how the code is shaped:
`channels/catalog.py` lists `web` alongside `telegram` and `whatsapp`, and the
first-run grid renders it as **Connected** because it needs no setup. Every
statement below assumes that framing: **chat is one connection among several,
not a separate thing**. It is simply the connection that is always available.

That has a consequence worth naming: whatever we add to the web composer
(tools, modes, research) should have a story for Telegram too, even if it is
"not there yet". A capability that only exists in one channel is a capability
that half the product cannot use.

---

## 1. Stop claiming things about the user's machine that we did not check

**This is the most important item, because it is a correctness bug, not a
polish one.** The recommended preset says *"free, runs on this machine"*, and
we never looked at the machine. On a device with 4 GB of VRAM that sentence is
simply false, and the user finds out by watching it fail.

**Correction:** an earlier version of this plan (and of
`docs/archive/LLM_SETUP_PROPOSAL.md`) said LocalDeploy's control subsystem had **no
HTTP surface**. That was wrong. Every module under `localdeploy/control/`
defines an `APIRouter`, `control/__init__.py` aggregates them, and
`server.py:1851` includes it as `web_router` whenever `ENABLE_WEB_UI` is on —
which is the default. The mistake came from grepping `@app.` decorators and a
top-level `from .control import`, both of which miss a function-scoped import.

So these already exist and are live:

| Endpoint | What it gives |
|---|---|
| `GET /system/hardware` | Per-GPU name, vendor, backend, **total and free** VRAM, driver, utilization, multi-GPU grouping |
| `POST /system/fit-check` · `/system/fit-table` · `/system/fit-batch` | Does this model fit? |
| `POST /system/recommend` · `/system/recommend/stream` | Tune for my GPU |
| `POST /registry/starter-pack` · `/registry/recommend` | Curated picks for a new user |
| `POST /system/install-ollama`, `GET /system/ollama-status` | Runtime install |

**1a — done, and nothing needed writing.** YBM now calls `GET /system/hardware`
first and falls back to its own detection only when LocalDeploy is not
running.

**1b.** YBM asks, and the answer drives the screen:
- Fits → *"Recommended for your machine — RTX 3080, 8 GB VRAM"*
- Does not fit → **disabled**, with the real reason: *"needs ~11 GB, you have 8 GB"*
- Cannot tell → say so: *"We could not detect your GPU"* and recommend nothing.

**1c.** Never print "runs on this machine" until 1b answers yes. Until 1a
exists, the honest label is **"free, runs locally"** with no machine claim.
This one is free and should land immediately.

---

## 2. Say what a choice will actually do before it does it

*"Qwen3-VL 8B on the host — free (use this in Docker)"* is unreadable unless
you already know what a container gateway is. That was my wording and it is
bad.

**2a.** Rewrite in terms of consequences, not topology:
> **Qwen3-VL 8B** — free, private, runs on your own hardware
> Downloads about 5 GB the first time. Nothing you type leaves this machine.

**2b.** Add a disclosure — tooltip or expandable line — on every local preset
stating plainly: *"YBM will install LocalDeploy if it is missing, download the
model (~5 GB), and run it on this computer. It stays on your machine and
costs nothing to use."* Downloading multiple gigabytes is not something to
discover after clicking.

**2c.** The container case becomes a **detected state, not a preset**. If we
are containerised and a host runtime answers on the gateway, say
*"Found a model server on your computer"* and use it. The user should never
read the words `host.docker.internal`.

---

## 3. Remove the duplication you spotted

You are right that it is duplication. The screen currently offers
*"OpenAI GPT-4.1 — needs a paid API key"* as a preset **and** OpenAI inside
the provider picker below. Two paths, same destination, different quality —
the preset saves without verifying, the picker verifies and now requires a
real completion.

**3a.** Delete cloud presets from the preset row. The preset row becomes
**local options only**; anything needing a key goes through the picker, which
verifies.

**3b.** Two clearly-labelled routes and nothing else:
> **Run a model on this computer** — free, private
> **Use an API key** — Anthropic, OpenAI, and 11 others

That is the whole decision, stated once.

---

## 4. Chat should decide its own tools — and offer modes when you want to force one

**4a. Stop instructing the user to instruct the model.** The suggestion
*"Use the local code interpreter to compute the 20th Fibonacci number"* teaches
exactly the wrong lesson: it implies YBM cannot work out that arithmetic needs
code. If the model genuinely needs to be told, that is a routing bug to fix,
not a prompt to ship. New suggestions describe **outcomes**:

- *"Summarize the PDFs on my desktop"* (your suggestion — kept, it is the best one)
- *"What changed in this folder since yesterday?"*
- *"Find the cheapest flight to Lisbon next month"*

**4b. "What's the current status?" goes.** With no tasks it answers nothing;
it reads like a demo of the app talking about itself.

**4c. Every suggestion must be verified to work** before it ships. A
suggestion chip is a promise, and a chip that fails on click is worse than no
chip.

**4d. A tools menu in the composer.** Following the pattern you pointed at:
a single **`+`** next to attach, opening web search / deep research / code /
browser, each inserting a **removable chip that shows scope before send** —
so the user sees what will run *before* committing. Modes carry equal visual
weight and honest runtimes: **deep research announces that it will take
minutes**, because a mode that silently runs long feels broken.

The capabilities already exist — `browser.search`, `browser.research`,
`browser.research_pages` are real operations in `tools/browser.py`. The chips
are a way to *force* one, not a way to make one possible. Default stays
automatic: the model chooses.

**4e. Chips must respect policy.** A chip for a disabled capability shows
disabled with the same "enable it in Access" pointer the Tools page uses. The
composer must not become a way around the policy engine.

---

## 5. Renaming Concierge / Operator / Auditor

They are real jargon, and the Settings page says them straight to a
first-time user.

**My recommendation: change the words the user reads; leave the code alone.**
`OperatorDecision`, `OperatorAction` and friends are woven through schemas
that the recorded scenario fixtures depend on — renaming the classes would
invalidate all sixteen and buy nothing a label cannot.

Three options for the user-facing trio:

| | Concierge | Operator | Auditor |
|---|---|---|---|
| **A — verbs (recommended)** | **Understands** | **Does the work** | **Checks the result** |
| B — roles | Intake | Runner | Reviewer |
| C — plain nouns | Interpreter | Executor | Verifier |

**A** is my pick: it needs no glossary, and it describes what a user cares
about — what happens to their request — rather than the architecture. Settings
would read *"The model used to understand requests, do the work, and check the
result"* instead of naming three internal components.

If you want one word each for diagrams, take **B**.

---

## 6. Settings

The recurring theme: **Settings has fallen behind the wizard.**

**6a. "Change the model" is the whole job.** Today it is five preset pills and
no way to reach the thirteen providers the wizard offers. Someone who
configured Anthropic during onboarding cannot change it without editing YAML.
Mount `ProviderPicker` — the component already exists — under a heading that
says what the section is for.

**6b. Show what is running now.** Nothing on the page answers "what model am I
using". The active profile should be marked, with its provider and model.

**6c. Adding a remote API is the same picker.** Your question — *how does a
user add a remote API here?* — has no answer today. With 6a it becomes: pick
the provider, paste the key, press Test, save.

**6d. Telegram in Settings still asks for numeric IDs.** "Allowed user IDs" and
"Allowed chat IDs" are exactly what the guided flow removed. Someone who
skipped Telegram at onboarding gets the hard version. Reuse the guided panel.

**6e. Stale copy.** The Setup wizard card still says *"pick a brain, pick a
face"*; both names are gone.

---

## 7. Layout and the rest (from the previous plan)

- **7a.** Two stacked banners eat ~130px above the fold on every route.
- **7b.** Cards use ~490px of a ~1600px content area; go 3-up when wide.
- **7c.** **Nothing below 1280px has ever been checked.** "Message YBM from
  your phone" is a headline feature; the console's own phone behaviour is
  unverified.
- **7d.** No Back button in the wizard.
- **7e.** Chat and Tools are good and need nothing. Chat has a real empty
  state and spells out `Enter to send`; Tools has search, risk badges,
  "5 of 22 enabled", and tells you exactly which capability to enable.

---

## Order of work

| Wave | Items | Why here |
|---|---|---|
| **1** | 1c, 2a, 3a/3b, 4a/4b/4c, 6a/6b/6c, 6e | No new infrastructure. Kills the false machine claim, the duplication, the bad suggestions, and the Settings gap |
| **2** | 4d/4e, 2b, 5 | Composer tools menu, download disclosure, the rename |
| **3** | 6d, 7a, 7d | Guided Telegram in Settings, banner collapse, Back |
| **4** | 1a/1b, 2c | Real hardware fit — needs the LocalDeploy repo decision |
| **5** | 7b, 7c | Responsive audit |

## Open question for you

**Wave 4 edits a second repository.** Exposing `hardware.py`/`fit.py` over HTTP
in LocalDeploy is small and the logic exists, but it is not this repo. Say the
word and it is a short job; until then item 1c keeps us honest by not making
the claim at all.
