# Picking a brain: a proposal

> **Status: the bring-your-own-key half is implemented** - items 4, 5, 6 and 7
> of the table below. The LocalDeploy hardware/fit work (items 1-3) is not:
> it means editing a different repository, which is a scope call. See
> *What landed* at the end.

The model sits in front of everything. Today choosing one is the first screen a
new user sees and the most likely place for them to give up, because every
option is a guess: four names, no idea which will work on their machine, no idea
what any of them costs.

This proposes two doors - **bring your own key** and **we'll set one up for
you** - with the second doing the hardware thinking on the user's behalf.

## The finding that shapes everything

**LocalDeploy has already solved hardware-aware model selection. YBM just can't
reach it.**

Not a plan, not a stub - a built subsystem under `localdeploy/control/`:

| Module | What it does |
|---|---|
| `hardware.py` | CPU model, RAM, GPU via `nvidia-smi`, Apple GPU, Linux sysfs |
| `fit.py` | `fit_check`, `fit_table`, `fit_batch` - does this model fit this machine? |
| `starter.py` | *"Step 15 - Starter pack - one-click curated model picks for new users"*: `_resolve_budget` → `_pick` fit-filters a catalog against VRAM budget → ranks and caps per family |
| `recommend.py` | *"Step 14 (D2) - one-click 'Tune for my GPU'"*: scores candidates on quality × speed × headroom |
| `calibration.py` | Per-GPU VRAM calibration keyed `NVIDIA:CUDA:rtx-3080-laptop\|ollama\|gemma3\|default\|4096`, learning estimated-vs-observed GB from real runs |
| `models.py` / `_ollama.py` | `install_ollama()`, `models_pull()`, `pull_stream()` - installs the runtime and pulls models **with streaming progress** |

So: it detects the hardware, estimates VRAM per model/quant/context, filters a
catalog to what fits, ranks the survivors, installs the runtime, pulls the
model, and gets more accurate over time from observed runs.

**The gap is purely reachability.** LocalDeploy's HTTP server exposes
`/health`, `/models`, `/v1/models`, `/profiles`, `/estimate`, `/chat`,
`/v1/chat/completions`, `/vision`, `/benchmark` - and nothing from
`control/`. `server.py` imports exactly one control module (`monitor`); `fit`,
`hardware`, `starter`, and `recommend` have no HTTP surface at all.

The work is not building hardware detection. It is **exposing what exists and
consuming it**.

## The other finding: Anthropic can't work today

YBM has one provider, `OpenAICompatibleProvider`. Anthropic is not
OpenAI-compatible, and Anthropic's own guidance is explicit that its API should
be reached through the official SDK rather than an OpenAI-compatible shim. So
"paste an Anthropic key" needs a **native provider**, not a `base_url` swap.

Two things would bite immediately:

- **`temperature` is rejected with a 400** on Claude Opus 5, Sonnet 5, Opus
  4.7/4.8 and Fable 5. Every YBM profile sets a temperature (`0.1`, `0.2`), and
  `OpenAICompatibleProvider` forwards it unconditionally - so a naive Anthropic
  path would 400 on **every request**.
- **Thinking config changed.** `budget_tokens` is rejected on current models;
  it's `thinking: {type: "adaptive"}` now.

Worth knowing for the pricing copy (per million tokens, in/out):

| Model | ID | Context | Price |
|---|---|---|---|
| Claude Opus 5 | `claude-opus-5` | 1M | $5 / $25 |
| Claude Sonnet 5 | `claude-sonnet-5` | 1M | $3 / $15 |
| Claude Haiku 4.5 | `claude-haiku-4-5` | 200K | $1 / $5 |

Anthropic also has a **Models API** (`GET /v1/models`) returning live context
windows and capability flags - which is how the "let the user pick another one
later" screen should populate itself rather than shipping a hardcoded list that
rots.

## The proposal

### One screen, and the fast path is one click

Replace "Pick a brain" with a screen that has already done the work:

```
  We looked at this machine: RTX 3080 Laptop, 8 GB VRAM, 32 GB RAM.

  ┌────────────────────────────────────────────────────────┐
  │  ✓  Recommended for your machine                       │
  │     Qwen3-VL 8B  ·  free  ·  runs locally  ·  ~5 GB    │
  │     Fits comfortably in 8 GB. Nothing leaves your PC.  │
  │                                        [ Set it up ]   │
  └────────────────────────────────────────────────────────┘

  Already have an API key?     Anthropic ▾  [ paste key ]  → Check
  Other local models (3 fit)                                       ▾
```

**One click for someone with no opinion. One paste for someone with a key.**
Everything else is behind a disclosure.

### Door 1 - "Set it up for me"

`[ Set it up ]` runs the whole chain with a progress bar, because every step
already exists as a function:

1. `install_ollama()` if the runtime is missing
2. `models_pull()` / `pull_stream()` for the chosen model - **stream the pull
   progress into the UI**, so a 5 GB download is a visible bar, not a hang
3. Write the profile, verify with a real completion, mark onboarding done

The user never learns what a quantisation is.

### Door 2 - "I have a key"

A provider dropdown (Anthropic / OpenAI / OpenRouter / other
OpenAI-compatible), a key field, and a **Check** button that echoes back what
it found - *"Connected. 14 models available, defaulting to Claude Sonnet 5."*
Same confirm-don't-assume pattern that fixed the Telegram step: a pasted key
should never be accepted silently.

Populate the model list from the provider's own models endpoint. Never hardcode.

### Hardware honesty

`fit.py` already knows when something won't fit. Use it: show what fits as
selectable, show what doesn't as greyed with the actual reason - *"needs ~11 GB,
you have 8 GB"* - rather than letting someone pick a model that will OOM.
Same for the container case, where loopback presets can't reach a host runtime
at all.

### Later: switching is a first-class action

Settings gets a **Change model** screen that is the same component, with the
current choice marked and the same fit information. Adding a provider becomes a
registry entry, not a UI change.

## What has to be built

| # | Work | Where | Size |
|---|---|---|---|
| 1 | Expose `hardware` + `fit` + `starter` over HTTP: `GET /hardware`, `POST /fit`, `GET /starter-pack` | LocalDeploy `server.py` - thin wrappers, the logic exists | S |
| 2 | Expose install/pull with a streaming progress endpoint | LocalDeploy - `pull_stream()` already yields progress | S–M |
| 3 | YBM client for those endpoints, degrading cleanly when LocalDeploy is absent | `backend/.../llm/` | M |
| 4 | **Native `AnthropicProvider`** using the official SDK - omit `temperature`, adaptive thinking, handle `stop_reason: "refusal"` | `backend/.../llm/providers.py` | M |
| 5 | Provider registry so a new provider is a table entry, not a code path | `backend/.../llm/` | M |
| 6 | One-screen picker + streaming setup progress | `frontend/.../onboarding/` | M |
| 7 | Settings → Change model, reusing the same component | `frontend/.../settings/` | S |

## Order

**4 first** - it's independent, it's the "bring your own key" half, and it
unblocks anyone with a key today. It's also where the sharp edge is: the
`temperature` 400 would otherwise look like a mysterious auth failure.

**Then 1 + 3** - hardware detection and fit reachable from YBM. After this the
screen can be honest about the machine even if setup is still manual.

**Then 2 + 6** - the one-click path and its progress bar. This is the payoff.

**Then 5 + 7** - switching and extensibility, once the shape is proven.

## Open questions

- **Does LocalDeploy's catalog cover non-Ollama runtimes?** `fit.py` and
  `calibration.py` key on runtime, so the structure allows it, but I only
  confirmed Ollama paths end to end.
- **Should YBM depend on LocalDeploy at all, or vendor the fit logic?** A
  dependency keeps one source of truth and inherits the calibration data; a
  copy removes the coupling. I'd take the dependency, with the whole feature
  degrading to "paste a key" when LocalDeploy isn't there.
- **Where does the model catalog live** once several providers are in play -
  LocalDeploy's for local, each provider's models endpoint for cloud?

## Not verified

- LocalDeploy's `control/` functions were read, not executed. Their signatures
  and docstrings are quoted accurately; I did not run `starter_pack()` or
  `fit_check()` against this machine.
- Anthropic model IDs, pricing, and the `temperature`/thinking constraints come
  from Anthropic's own current API reference, not from a live API call - no
  request was made and no key was used.

## What landed

**A provider catalog** (`backend/src/agent_control/llm/catalog.py`) with 13
entries - Anthropic, OpenAI, OpenRouter, Google Gemini, Groq, DeepSeek,
Mistral, xAI, Together, Ollama, LM Studio, LocalDeploy, and a generic
OpenAI-compatible entry. Adding a provider is a row, not a code path. Every
base URL was checked against the provider's own documentation rather than
recalled.

**A native `AnthropicProvider`.** The plan called this the sharp edge and it
was: `temperature` is rejected with a 400 on Claude Opus 5, Sonnet 5, Opus
4.7/4.8 and Fable 5, and every YBM profile carries one. The provider omits it
on exactly those models and keeps it on older ones, refuses to index `content[0]`
on a `stop_reason: "refusal"`, uses a forced tool call for structured output,
and phrases its 5xx errors to match what `FailoverLLMProvider` looks for so an
Anthropic outage still fails over to the local model. All of that is pinned by
tests.

**`GET /api/llm/providers`** serves the catalog, and **`POST /api/setup/llm/verify`**
proves a key by asking the provider what models it has - so the model picker is
populated from the provider itself and never from a hardcoded list that rots.

**A `ProviderPicker` component** in the wizard, which replaced a block that sent
`base_url: null` and therefore could not reach any cloud provider at all. It
opens automatically when no local runtime was detected. Verified per provider:
Anthropic and OpenAI show a key field and a "Get a key" link and no base URL;
Ollama and LocalDeploy show no key field; only the generic entry asks for a
base URL.

## Not done

- **LocalDeploy's hardware/fit endpoints (items 1-3).** `hardware.py`,
  `fit.py`, and `starter.py` still have no HTTP surface, so the one-click
  "set it up for me" path and the hardware-honest fit display are not built.
  That work is in a different repository and was left for an explicit decision.
- **No live API call was made against any provider.** Every test runs against a
  mocked endpoint; no key was used and nothing was spent.
