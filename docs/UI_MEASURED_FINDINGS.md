# UI: measured findings from a live run

Companion to `docs/UI_UX_AUDIT.md` (the reasoning) and `docs/UI_REWRITE_PLAN.md`
(the plan). This file holds only what was **measured** against the running
console on 2026-08-10, so the numbers can be re-checked rather than argued
about.

Method: backend started without Telegram polling, every route driven with
Playwright at 1440x900 and 390x844, DOM measured in-page. 16 page loads.

## What is already good, and should not be "fixed"

Worth recording, because two of these contradict assumptions in earlier docs:

- **Zero console errors, zero failed requests, across all 16 loads.** Nothing
  throws, nothing 404s, nothing 500s.
- **No horizontal page scroll at either width**, on any route.
- **The phone layout already works.** Bottom tab bar, stacked content,
  readable type. The console was not a desktop-only app that needed a mobile
  pass — `docs/KNOWN_GAPS.md` U3 assumed this needed auditing before changes;
  the audit says the foundation is there.
- **Every route is a fixed-height app shell** (`scrollHeight` = 900 on all
  eight) with internal scrolling, rather than a long scrolling document.

## F1 — The Tasks table is 21x wider than the window (critical)

Measured at 1440px viewport:

| Column | Cell width | Right edge |
|---|---|---|
| Objective | 3,321px | 3,625 |
| Status | 112px | 3,737 |
| Outcome | **26,618px** | 30,355 |
| Created | 170px | **30,525** |

The table headers exist — `["Objective","Status","Outcome","Created"]` — but
everything past ~1,390px is off-screen, and because the page itself does not
scroll horizontally there is **no way to reach it**. Status, outcome, duration
and timestamp are simply unavailable on desktop.

Cause: no `max-width`, no truncation, and no `table-layout: fixed`, so a cell
grows to its longest single-line content. One task's outcome text is 26,000
pixels wide on its own.

**The phone breakpoint already solves this.** At 390px the same data renders as
cards: two-line clamped objective, a status badge, the outcome line, and
`2m 18s · 6 steps · 27,003 tokens`. Everything the desktop table hides is
visible there.

**Fix:** use the card layout at all widths, or give the table
`table-layout: fixed` with per-column widths and `line-clamp` on the text
columns. The card route is less work and already designed.

## F2 — Horizontal space is largely unused

At 1440px, with a 255px sidebar leaving ~1,185px of content:

- **Agent** renders three cards and then roughly 600px of empty vertical space
  — about 65% of the viewport is blank. It is a hub whose three destinations
  (`/memory`, `/skills`, `/tools`) are already routes in their own right, so it
  costs a click and returns three links.
- **Tools** lays cards out two-across ending at ~1,265px, and any group with a
  single tool (Adapter Factory, Artifacts) leaves half a row empty.
- **Chat** starts assistant messages at x=465 in a 1440px window, leaving a
  ~200px dead gutter to the left of the content column while the bubbles
  themselves stay narrow.

**Fix:** let the content column breathe (wider max-width, or a denser grid at
>=1280px). For Agent specifically, consider promoting Memory/Skills/Tools into
the sidebar under a group heading and dropping the hub page, or making the hub
show real content rather than three links.

## F3 — Advanced configuration is always visible

The sidebar has an **Advanced mode** toggle, but Settings shows the full LLM
profile regardless: provider, base URL, API key env var, timeout, max tokens,
temperature, profile name, default profile.

For the person the installer is now aimed at — no Python, no terminal — the
useful control is the preset row at the top (`LocalDeploy Qwen3-VL 8B
(recommended)` and friends). The rest is expert surface shown by default.

**Fix:** gate the raw fields behind the Advanced toggle that already exists.
The presets and the enable switches stay.

## F4 — Chat receipts outweigh their messages

A one-line reply ("I am an AI assistant…") carries a receipt card with
`Completed`, `Receipt`, `No external transfer was recorded`, `Time: 13s`,
`Download`, and `Full trace`. The receipt is physically larger than the answer.

`No external transfer was recorded` is also phrased as an audit artefact rather
than something a person asked about.

**Fix:** collapse the receipt to a single quiet line (`13s · trace`) and expand
on click; keep the full card for tasks that actually touched a tool, sent a
file, or spent real time.

## F5 — Persistent banner

`Everything dangerous is off by default. Enable capabilities in Access.` sits
above everything on every route. It is one line on desktop and two on a phone —
roughly 55px of an 844px screen, permanently, for a message that is only news
once. It is dismissible; it should also stay dismissed, and probably should not
appear at all after the first successful task.

## F6 — Truncation with no way back

Tools cards clip their operation lists (`promote_after_ap…`,
`extract_page_stat…`). The information is gone rather than deferred — no
tooltip, no expansion.

## Suggested order

1. **F1** — data the user cannot reach at all, and the fix already exists at
   another breakpoint.
2. **F3** — smallest change, and aimed squarely at the audience the installer
   work just widened.
3. **F5**, then **F4** — both reduce permanent noise.
4. **F2** — largest visual gain, most layout work.
5. **F6** — small.

## Reproducing

Backend on 127.0.0.1:8765 without Telegram, then drive `/admin`, `/admin/tasks`,
`/admin/access`, `/admin/agent`, `/admin/memory`, `/admin/skills`,
`/admin/tools`, `/admin/settings` at both widths. Playwright is already a
`frontend` dev dependency (`@playwright/test`), and the browsers are installed;
no extra tooling is required.

The console needs an authenticated first navigation — `/admin?token=<the
AGENT_ADMIN_TOKEN from .env>` — after which `lib/api.ts` keeps the token and
strips it from the URL.
