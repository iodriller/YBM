# UI/UX improvement plan

From a second pass over a containerised first run, screenshotted in
`docs/screenshots/first-run/`. Findings are numbered so they can be argued with
individually. Everything here was observed on screen, not inferred from code.

Two things from the previous pass already landed and are not repeated:
the wizard is now escapable, and channels are a catalog rather than one
hardcoded toggle.

## What is already good

Worth saying, because the plan below is all criticism otherwise.

- **The chat page is the strongest screen.** Real empty state, three concrete
  suggestion chips, a "Policy protected" badge, attachment affordances, and
  `Enter to send · Shift + Enter for a new line` spelled out. Nothing to fix.
- **Tools is well built.** Search, grouping by domain, risk badges, a
  "5 of 22 enabled" counter, and every disabled card says exactly which
  capability to turn on and where. It respects the "inventory, not switchboard"
  split instead of blurring it.
- **The sidebar is calm** — five items, an active-task count, appearance and
  advanced-mode controls parked at the bottom where they belong.

## Findings

### A. Settings is now behind the wizard

**A1 — Settings cannot reach the providers the wizard can.** The wizard offers
13 providers with key verification; Settings offers the same five preset pills
and nothing else. A user who configured Anthropic during onboarding cannot
change their model afterwards without editing YAML. `ProviderPicker` is already
a standalone component — this is mostly a matter of mounting it.

**A2 — Settings still asks for numeric Telegram IDs.** "Allowed user IDs" and
"Allowed chat IDs" are raw fields, which is exactly the problem the guided
wizard flow removed. Someone who skipped Telegram during onboarding and comes
back later gets the hard version. The guided sequence should be the Settings
path too.

**A3 — The active preset is not marked.** Five pills, no indication which one
is in use. Nothing on the page answers "what am I running right now".

**A4 — Stale copy.** The Setup wizard card still reads *"Re-run the first-run
wizard (pick a brain, pick a face)"* — both of those names are gone.

**A5 — Internal role jargon.** *"The model the Concierge, Operator, and Auditor
all currently share"* means nothing to a new user. That is architecture, not
user-facing language.

### B. Layout

**B1 — Two stacked banners eat the top of every page.** The amber setup banner
plus the blue safety banner consume roughly 130px above the fold, on every
route, before any content. They should collapse into one region, or the safety
one should become a one-time toast.

**B2 — Cards do not use the width they have.** On Tools, cards are ~490px in a
~1600px content area, so a group of one renders as a lonely card beside a large
void. A responsive 3-up grid at wide viewports would fix it.

**B3 — Nothing has been checked below 1280px.** Every screenshot in this pass
is desktop. The sidebar has a `md:` breakpoint and a mobile header exists, so
the intent is there, but it is unverified — and "message YBM from your phone"
is a headline feature, which makes the console's own phone behaviour worth
proving.

### C. Onboarding, remaining

**C1 — Local presets are offered where they cannot work.** In a container the
three loopback presets are unreachable. The copy now explains this, but they
are still clickable and will still fail after selection. They should be
disabled with the reason, which is what `fit.py` would also give us for
hardware.

**C2 — The wizard is still two questions with no way back.** There is no
"Back", so a wrong turn in step 2 cannot be undone without a reload.

**C3 — Success is invisible after the fact.** Once configured, nothing on the
console says which model is running. A small footer or header indicator would
answer it everywhere.

## Plan, in order

**1. Mount `ProviderPicker` in Settings, and mark the active profile.**
Closes A1 and A3, and is the single biggest capability gap. Small: the
component exists.

**2. Reuse the guided Telegram flow in Settings.** Closes A2. Also small, same
reason — extract the panel from the wizard step.

**3. Copy pass on Settings.** A4 and A5. Minutes, not hours.

**4. Collapse the banner region.** B1. Make the safety banner a one-time
dismissal and let the setup banner own the strip.

**5. Disable presets that cannot work, with the reason inline.** C1. This is
the same shape as the eventual hardware-fit work, so doing it now sets the
pattern.

**6. Responsive audit at 390px and 768px.** B3, then B2. Screenshot both, fix
what breaks.

**7. Back button and a current-model indicator.** C2 and C3. Polish.

## What landed this pass

**Setup now requires a real completion.** Listing models proves a key is valid;
it does not prove the chosen model answers, that provider routing is right, or
that the response parses — and each of those fails later, somewhere the user
will not connect back to the setup screen. `POST /api/setup/llm/test` makes one
real round trip through the same `build_provider_for_profile` path a live
request uses, and the picker will not save until it succeeds. The reply is
shown, so "it works" is something the user sees rather than something the UI
asserts.

That last detail is deliberate: a test that bypassed the real routing would
prove nothing about the provider that actually serves traffic. A test asserts
this by checking that an Anthropic test call omits `temperature` — the exact
behaviour that only the native provider has.

It costs a token or two of the user's own quota, on an explicit click. The
alternative is discovering the failure on their first real message.

## Not verified

- No live provider call was made — all tests run against mocked endpoints, no
  key was used, nothing was spent.
- Mobile and tablet viewports are unexercised (B3 is the finding, not a fix).
