# Pre-public review

Full pass over the repo as a stranger would meet it. Secrets were audited
earlier this session — gitleaks over all 175 commits, clean, and zero tracked
files carrying the account name — so this covers everything else.

**Verdict: two blockers, both cheap. The code is in good shape; the
*presentation* is not.**

---

## P0 — blocks publishing

### 1. CI is red, and it is not the code

Every job on the last run failed in ~3 seconds:

> *The job was not started because recent account payments have failed or your
> spending limit needs to be increased.*

Nothing was executed. A public repo whose badge shows a failing build reads as
abandoned, and it is the first thing a visitor sees. Worth knowing: **public
repositories get free Actions minutes**, so publishing may resolve this by
itself — but confirm the billing state first rather than hoping.

### 2. The README is out of date in a way that becomes false on publish

Line 59 says *"This repository is private, so `curl … | sh` returns 404"*. The
moment you flip visibility that sentence is wrong, and it sits in the install
section where a newcomer starts.

More significant: **the README never mentions Anthropic, or that YBM now speaks
to thirteen providers.** That is the headline capability added this week and it
is invisible to anyone deciding whether to try this.

`config/config.example.yaml` has the same gap — no Anthropic profile, despite
`AGENTS.md` requiring that "documentation, schemas, and configuration examples
[stay] aligned with the code that exists".

---

## P1 — the docs directory is the weakest part of the repo

**22 files. Eleven of them are planning documents I wrote this session, ~1,900
lines, several explicitly superseding each other.**

| Doc | Status |
|---|---|
| `KNOWN_GAPS.md` | superseded by `GAPS.md` |
| `UI_UX_IMPROVEMENT_PLAN.md` | says so itself — superseded by `PRODUCT_PLAN.md` |
| `UI_MEASURED_FINDINGS.md` | folded into `PRODUCT_PLAN.md` |
| `FIRST_RUN_PLAN.md` | all items marked done |
| `INSTALL_UX_PLAN.md` | mostly implemented |
| `LLM_SETUP_PROPOSAL.md` | implemented, plus a correction |
| `PLATFORM_PROPOSAL.md` | implemented |
| `PUBLIC_RELEASE_CHECKLIST.md` | overlaps this document |
| `PRODUCT_PLAN.md` | wave 1 done, rest live |
| `VOICE_PLAN.md` | live |
| `GAPS.md` | live |

`AGENTS.md` names four durable sources of truth — `ARCHITECTURE.md`,
`HISTORY.md`, `config.example.yaml`, `README.md`. The other eighteen files are
working notes, and a stranger cannot tell which is which. The signal that YBM
is a serious project is buried under drafts of how it got here.

**Proposal:** move finished plans to `docs/archive/` with a one-line index,
keep `GAPS.md` and `PRODUCT_PLAN.md` as the live ones, and delete the three
that are strictly superseded. Roughly 22 files becomes 11.

### `ARCHITECTURE.md` has fallen behind

No mention of `llm/hardware.py`, `error_text.py`, `llm/catalog.py`, or
`channels/catalog.py` — the four modules most likely to be extended by someone
new. It is one of the four documents `AGENTS.md` says to trust.

---

## P2 — worth doing, not blocking

- **G5: implausible results are reported as fine.** A recorded run said *"a
  total of 0"* for a file of expenses without comment. Highest-value item left,
  and needs live model spend to re-record.
- **G6: the three starter prompts have never been run.** A suggestion chip that
  fails on click is worse than no chip. Also needs a model.
- **No real recording has ever been transcribed.** faster-whisper is in the
  image, but no audio has gone through it end to end.
- **Repository has no topics.** Free discoverability; the description is fine.
- **No screenshots in the README.** The console looks good — the first-run and
  chat captures in `docs/screenshots/first-run/` would do more for adoption
  than another paragraph.

---

## What is already right

Worth stating, because the list above is all criticism.

- `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` all present.
- **Zero** `TODO`/`FIXME`/`XXX` in shipped code.
- Secrets clean across the entire history, with a gitleaks job wired into CI.
- The test suite is substantial and green locally; ruff and tsc clean.
- The container builds and runs healthy in four seconds.
- Chat and Tools are genuinely well-designed; the console works at 390px.
- Dangerous capabilities are off by default and the policy gate is real.

---

## Order

| | Item | Effort |
|---|---|---|
| 1 | Sort the GitHub billing so CI is green | Yours — minutes |
| 2 | README: drop the private-repo note, add providers/voice, add a screenshot | ~30 min |
| 3 | `config.example.yaml`: add an Anthropic profile | ~10 min |
| 4 | Consolidate `docs/` into live + `archive/` | ~30 min |
| 5 | Refresh `ARCHITECTURE.md` with the four new modules | ~20 min |
| 6 | Repository topics | 2 min |
| 7 | G5 + G6 | Needs live model spend |

Items 2–6 are one focused pass and would leave the repo genuinely ready. Item 1
is the only one I cannot do.

## Not verified

- I have not confirmed the billing state itself, only read the CI annotation.
- G5, G6, and end-to-end voice remain unexercised for want of a configured
  model and a real bot token.
