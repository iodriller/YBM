# UI Rewrite Plan — React + FastAPI

Status: **planned, not started.** Written 2026-07-31.

Replaces the single-page Streamlit console (`backend/src/agent_control/admin_streamlit.py`,
1,776 lines, ~25 render functions stacked onto one page) with a React single-page app served
by the existing FastAPI backend.

This document is the end-to-end plan: the decision and its cost, the serving/auth architecture,
the stack, phased delivery, testing, and cutover. It folds in the three UX workstreams that
were previously scoped separately — first-run experience, console restructure, and approval UX.

---

## 1. Decision, and its honest cost

**Decision:** rewrite the admin console as a React SPA. Retire Streamlit.

**The cost is real and should not be understated.** Streamlit's whole value here was speed —
comparable dashboard work is roughly 1–3 days in Streamlit versus 2–6 weeks for a custom
React front end. Streamlit 1.60 also already ships `st.navigation`/`st.Page`, so the
multi-page restructure alone could have been done inside Streamlit in ~2 days. Choosing React
buys control and a materially better product surface, and costs weeks. That trade is the
premise of this document, not something it re-argues.

**What makes it much smaller than a typical rewrite:** the backend does not change. The admin
API already exists and is complete — **24 JSON endpoints** under `/admin/api/*` (plus the
`/admin` HTML route the SPA will replace) covering summary, tasks, traces, approvals, audit,
every config surface (LLM, Telegram, VS Code, workspace, computer-use, access modes), secrets,
chat, and an LLM connectivity test. Streamlit is already a *pure client* of that API; it holds
no business logic. So this is a **client swap**, not a system rewrite.

**What we gain beyond aesthetics:**

- Real client-side routing and state, instead of a full-script rerun on every interaction.
- One fewer service and one fewer port — Streamlit (`:8501`) disappears, and `ybm start`
  drops a process. `admin_ui` leaves `supervisor.py`'s service list and `ybm.ps1`'s.
- The ability to make approvals a first-class, always-visible surface, which the
  single-page Streamlit layout structurally cannot do.
- A dependency drop: `streamlit` (and its transitive tree) leaves `pyproject.toml`.

---

## 2. Architecture — serving and auth

**This section is the load-bearing one. Get it wrong and you weaken the security model that
is the project's main differentiator.**

Today `admin.py`'s `require_admin()` does two independent things:

1. **Same-origin enforcement** — `_origin_is_trusted(request)` rejects any request whose
   `Origin` header does not match `Host`. There is deliberately **no `CORSMiddleware`** on the
   app; the code comments say so explicitly, so Starlette emits no
   `Access-Control-Allow-Origin` and a malicious page's JS cannot read admin responses.
2. **Token check** — `X-Agent-Control-Admin-Token` header (or `?token=`), compared against
   `AGENT_ADMIN_TOKEN`. When no token is set *and* the host is loopback-only, it allows the
   request; if the host is not loopback and no token is set, it refuses with 503.

### The constraint this creates

A React dev server on `:5173` calling the backend on `:8765` is **cross-origin** and will be
**refused by design**. There are two ways to resolve that, and only one is acceptable:

| Option | Verdict |
|---|---|
| Add `CORSMiddleware` with permissive origins | ❌ **Never.** Deletes control #1 above, which exists specifically to stop a malicious local page from driving the agent. |
| Make the browser see one origin | ✅ Correct in both dev and prod. |

### Production: serve the SPA from FastAPI

Build the SPA to static assets and mount them on the existing app, replacing the current
`/admin` pointer route (`admin_page()` returning `_ADMIN_HTML`):

```
GET /admin              -> index.html   (SPA shell)
GET /admin/assets/*     -> hashed JS/CSS
GET /admin/api/*        -> unchanged, existing router
```

Same scheme, host, and port for app and API. `Origin` matches `Host`. The same-origin check
passes untouched, **no CORS relaxation anywhere**, and the token flow is unchanged. This is
strictly better than today's two-origin Streamlit setup.

### Development: Vite proxy

`vite.config.ts` proxies `/admin/api` to `http://127.0.0.1:8765`, so the browser only ever
talks to the Vite origin and the proxied request arrives with matching `Origin`/`Host`.
No backend changes, no dev-only auth bypass, no CORS.

```ts
server: { proxy: { "/admin/api": { target: "http://127.0.0.1:8765", changeOrigin: true } } }
```

> `changeOrigin: true` is what rewrites the forwarded `Origin` to the target. Verify against
> `_origin_is_trusted()` early in Phase 0 — this is the single highest-risk assumption in the
> plan, so it gets proven before any UI is built.

### Auth in the client

- Token read from the same `AGENT_ADMIN_TOKEN` the backend expects, injected at page load
  (see Phase 0.3) and sent as `X-Agent-Control-Admin-Token` on every request.
- **Never** put the token in a URL query string in the browser — it lands in history and
  logs. The header is the only client path; `?token=` stays for curl/scripts.
- Keep it in memory (module-scope), not `localStorage`, to limit XSS blast radius.

---

## 3. Stack

| Concern | Choice | Why |
|---|---|---|
| Build | **Vite + React + TypeScript** | Fast, standard, first-class proxy support. Repo already has a Node 22 toolchain and TS config for `vscode-extension/`. |
| Server state | **TanStack Query** | The console is ~90% polled server state. Gives caching, `refetchInterval`, request dedup, and stale/error states for free — replaces Streamlit's 3s whole-page rerun (`LIVE_REFRESH_SECONDS`) with per-query intervals. |
| Routing | **React Router** | Four top-level pages, deep links into task traces. |
| UI kit | **Tailwind + shadcn/ui** | Accessible primitives (dialog, alert, table, toast) copied into the repo rather than pinned as a dependency; no design system to invent. |
| Response validation | **Zod** | Parse `/admin/api/*` payloads at the boundary. The API is untyped Python dicts; this stops a backend shape change from silently rendering `undefined`. |
| Tests | **Vitest + Testing Library**, **Playwright** for E2E | Playwright is already a backend test dependency for UI diagnosis; reuse it. |

**Location:** `frontend/` at repo root, sibling to `backend/` and `vscode-extension/` —
matching the existing top-level layout.

---

## 4. Phase 0 — Backend readiness (~1 day)

No UI yet. De-risk the assumptions first.

- **0.1 Prove the Vite proxy satisfies `_origin_is_trusted()`.** Spike a throwaway Vite app,
  call `/admin/api/summary`, confirm 200 not 403. *If this fails, the whole dev story changes* —
  find out now, not in week three. Fallback if it fails: run Vite in middleware mode behind
  FastAPI, or add an explicit dev-only trusted-origin allowlist gated on a loopback host check
  (never a blanket CORS policy).
- **0.2 Static mount.** Add a `StaticFiles` mount for the built SPA at `/admin`, with an
  SPA-fallback so client-side routes (`/admin/tasks`, ...) return `index.html` instead of 404.
  Keep `/admin/api/*` registered *before* the catch-all so it is never shadowed.
- **0.3 Bootstrap endpoint.** `GET /admin/api/bootstrap` returning what the shell needs on
  first paint: whether a token is required, whether onboarding is complete, whether an LLM
  profile is reachable, and the app version. Lets the SPA decide between wizard and console
  without a waterfall of calls.
- **0.4 Decide live-update transport.** Start with **TanStack Query polling** (2–3s on active
  views) — it matches today's behavior, needs zero backend work, and is trivially correct.
  Only add SSE (`/admin/api/events`) if polling proves visibly laggy for approvals. *Do not
  build SSE speculatively.*
- **0.5 Frontend build wiring.** `frontend/` npm project; `ybm package-ui` (or a build step in
  `ybm setup`) producing `backend/src/agent_control/static/admin/`. Ship the built assets in
  the Python package so `pip install` users get a working console with no Node toolchain.

**Exit criteria:** a hello-world React page served at `http://127.0.0.1:8765/admin`, calling
`/admin/api/summary` successfully in both dev (proxy) and prod (static mount).

---

## 5. Phase 1 — Shell + Chat *(first value)* (~3 days)

Implements the **console restructure** and the **value-before-configuration** principle.

**App shell:** persistent left nav with four destinations, a connection/health indicator, and
a **global approval banner** (Phase 3) rendered above the router outlet so it is visible from
every page.

| Route | Page | Replaces |
|---|---|---|
| `/admin` | **Chat** *(landing)* | `_render_local_chat` (currently an expander below the header) |
| `/admin/tasks` | **Tasks** | `_render_live_activity`, `_render_tasks`, `_render_task_trace`, `_render_operations` |
| `/admin/access` | **Access** | `_render_access_config`, `_render_kill_switch`, `_render_computer_use_config`, `_render_secrets_config` |
| `/admin/settings` | **Settings** | `_render_llm_config`, `_render_telegram_config`, `_render_vscode_config`, `_render_workspace_config`, `_render_diagnostics`, `_render_audit` |

**Chat is the landing page.** It is the front door; a first-time user should be able to type
something and get an answer without visiting any other screen.

- Transcript from `GET /admin/api/chat/messages`, send via `POST`.
- Streaming-style status: a task moves `received → running → completed`; poll and update the
  assistant bubble in place, reusing the status→text mapping logic from `_chat_answer_text`.
- **Starter prompts** on empty state — three clickable examples, one of which deliberately
  triggers an approval, so a new user meets the approval gate in their first minute rather
  than discovering it later.
- **Real empty states** everywhere, replacing today's empty tables.

**Exit criteria:** send a message, see it become a task, see the answer, with no other page
implemented.

---

## 6. Phase 2 — Approvals *(the differentiator)* (~2 days)

The product's core claim is "it asks before it acts." This surface should be the best in the
app, and it is the main thing the single-page Streamlit layout could not do well.

- **2.1 Global banner.** Persistent, unmissable, on every route. Count + one-click open.
  Driven by `GET /admin/api/approvals` on a short interval.
- **2.2 Approval detail.** Show, in this order: the plain-English **reason** (now populated by
  `ToolDefinition.approval_reasons`), the **tool + operation**, the **exact parameters** in a
  readable diff-style block, and **capability + risk level**. Never ask a human to approve
  something they cannot inspect.
- **2.3 Expiry countdown.** Approvals are one-shot and expiring (`decide_pending()` fails
  closed past `expires_at`). Show a live countdown; grey the buttons out and say so on expiry,
  rather than letting a click fail confusingly.
- **2.4 Decide.** Approve / Deny via `POST /admin/api/approvals/{id}/decide`, with an
  optimistic update and rollback on error.
- **2.5 Deep link** to the requesting task's trace.

**Exit criteria:** trigger a `code.interpreter generate_and_run` from Chat, see the banner
appear on every page, read *why* it needs approval, approve it, watch the task resume.

---

## 7. Phase 3 — Tasks (~3 days)

- Task list: status filter, search, live-updating rows.
- **Trace view** — the highest-value debugging surface. Operator step timeline (tool, status,
  input, output summary, error), tool invocations, audit events, evidence (files/URLs/commands).
  Port from `_render_task_trace` / `_render_operator_history` / `_render_evidence`.
- **Token/cost** panel per task (`metadata.token_usage`: total, call count, `by_source`
  breakdown, `last_model`). Already computed; currently underexposed.
- Task actions: cancel/pause signals via `POST /admin/api/tasks/{id}/signals`; clear via
  `DELETE /admin/api/tasks`.

---

## 8. Phase 4 — Access (~2 days)

The security control room, and a place the product should feel confident.

- Capability matrix: enabled, requires-approval, max risk level, scopes — per capability.
- **Kill switch** and access-mode presets (`POST /admin/api/config/access-modes`).
  Carry over the `bool(access_modes) and all(...)` empty-dict fix from the Streamlit version.
- Make it explicit in the UI that presets — **including Full Access** — do not bypass
  `approval_required_operations`. This is true, it is unusual, and it should be visible.
- Secret vault: list `service.key` (**never values**), add, delete. Preserve the invariant that
  values never appear in any response.
- Destructive toggles get a confirm dialog naming the concrete consequence.

---

## 9. Phase 5 — Settings + first-run wizard (~3 days)

- LLM config + preset selector + **"Test connection"** (`POST /admin/api/llm/test`).
- Telegram, VS Code, workspace, computer-use config forms.
- Audit log viewer with filters; diagnostics/health panel.

**First-run wizard (web, not CLI).** If `bootstrap` reports onboarding incomplete, the SPA
renders a centered card instead of the console:

1. **Pick a brain** — auto-detected local Ollama / paste an API key / skip.
2. **Pick a face** — web chat (default, zero setup) / also enable Telegram.
3. Done → land on Chat with starter prompts.

Skippable at every step; re-runnable from Settings. This complements the existing CLI
`ybm onboard` (which stays for headless installs) rather than replacing it — a non-technical
user should never need a terminal prompt.

**One-time safety tour:** a dismissible banner — *"Everything dangerous is off by default.
Enable capabilities in Access."* — tying the security story to the UI immediately.

---

## 10. Phase 6 — Cutover (~1 day)

Only after the React app reaches parity on the surfaces that matter.

Every touchpoint below was verified against the current tree:

1. Remove the `admin_ui` spec from `supervisor.py`'s `build_service_specs()` (~L136–148) and
   the equivalent in `ybm.ps1` (~L262); delete `scripts/services/run_admin_ui.ps1`.
2. Update the "Admin UI" URL banners now pointing at 8501 — `supervisor.py` (~L268),
   `ybm.ps1` (~L288), and the health-check entry in `ybm.ps1` (~L339) — to
   `http://127.0.0.1:8765/admin`.
3. Update `_check_ports()` in `bootstrap.py` (~L121): 8501 is no longer expected.
4. Drop `"streamlit"` from `REQUIRED_MODULES` in `bootstrap.py` (~L28) — `ybm doctor` checks
   it — and from `backend/pyproject.toml` dependencies.
5. Delete `admin_streamlit.py` (1,776 lines) and `backend/tests/test_admin_streamlit.py`.
6. Update `README.md`, `docs/LOCAL_SETUP.md`, `docs/ARCHITECTURE.md`, `docs/CAPABILITIES.md`:
   the admin console is now `http://127.0.0.1:8765/admin`.
7. Add a `docs/HISTORY.md` entry recording what changed and why, per the repo's conventions.

**Do not delete Streamlit until the React app is genuinely at parity.** Keeping both briefly
costs one dependency; cutting over early costs the ability to operate the system.

---

## 11. Testing

| Layer | Tool | Scope |
|---|---|---|
| Unit | Vitest + Testing Library | Status mapping, countdown/expiry logic, form validation, Zod schemas |
| Contract | Vitest | Zod schemas parsed against **recorded real** `/admin/api/*` responses — same philosophy as the backend's recorded scenario fixtures. Catches backend shape drift. |
| E2E | Playwright | Send a chat message → task appears; trigger approval → banner → approve → task resumes; toggle a capability → persists |
| Backend | existing pytest | Unchanged. Add tests for the static mount, SPA fallback, and `/admin/api/bootstrap` |

Add a `frontend` job to `.github/workflows/ci.yml` (Node 22, matching the existing
`vscode-extension` job): `npm ci`, `npm run lint`, `npm run test`, `npm run build`,
`npm audit --audit-level=high`.

---

## 12. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Vite proxy fails `_origin_is_trusted()` | **High** | Proven in Phase 0.1 before any UI work. Documented fallbacks. |
| Scope creep turns weeks into months | **High** | Phases 1–2 (Chat + Approvals) are the value. Ship those before Tasks/Access/Settings polish. |
| Long parity gap where neither UI is good | Medium | Keep Streamlit fully working until Phase 6. Never delete ahead of parity. |
| Built assets not shipped to pip users | Medium | Phase 0.5 — assets committed/packaged; CI asserts the built bundle exists. |
| Token handling regression | Medium | Header-only in browser; never query string; never `localStorage`. Covered by a Playwright assertion. |
| Node toolchain becomes a hard install prereq | Low | Ship prebuilt assets; Node needed only for frontend development. |

---

## 13. Effort

| Phase | Estimate |
|---|---|
| 0 — Backend readiness | ~1 day |
| 1 — Shell + Chat | ~3 days |
| 2 — Approvals | ~2 days |
| 3 — Tasks | ~3 days |
| 4 — Access | ~2 days |
| 5 — Settings + wizard | ~3 days |
| 6 — Cutover | ~1 day |
| **Total** | **~15 working days** |

Consistent with the 2–6 week industry range for a custom front end, at the low end because the
API layer already exists and needs no work.

**Minimum shippable slice:** Phases 0 + 1 + 2 (~6 days) delivers a React console that can chat
and approve — the two things that define the product — while Streamlit continues to serve
everything else until its phase lands.

---

## 14. Open questions

- **Dark mode** — worth it, but not before parity.
- **Mobile/responsive** — the Telegram channel already covers phone use. Desktop-first is
  defensible; revisit after launch.
- **SSE vs polling** — deferred by design (Phase 0.4). Decide with evidence from real use.
- **Bundled vs CDN fonts/icons** — bundle. The console must work fully offline and on an
  air-gapped machine.
