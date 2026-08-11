# Archive

Plans and proposals whose work is finished. Kept because the reasoning behind a
decision outlives the decision, and several are cited from code comments - but
they are **not** current guidance, and none of them should be read as
describing how YBM works today.

For that, see `docs/ARCHITECTURE.md`, `docs/HISTORY.md`, `README.md`, and
`config/config.example.yaml` - the four durable sources of truth named in
`AGENTS.md`.

| Document | What it was for | Outcome |
|---|---|---|
| `KNOWN_GAPS.md` | First gap sweep | Superseded by `docs/GAPS.md` |
| `INSTALL_UX_PLAN.md` | Making installation smooth for someone with no Python, git, or terminal | Implemented - `YBM.bat`, `install.ps1`, the one-click path |
| `PLATFORM_PROPOSAL.md` | Docker, MCP, and the surrounding platform work | Implemented |
| `FIRST_RUN_PLAN.md` | What a new user actually meets, from a containerised install | Implemented - all of P1–P5 |
| `LLM_SETUP_PROPOSAL.md` | Two doors: bring your own key, or set one up for you | Key half implemented - 13 providers, native Anthropic. Contains a correction: LocalDeploy's control endpoints already existed |
| `UI_MEASURED_FINDINGS.md` | First screenshot pass over the console | Folded into `PRODUCT_PLAN.md` |
| `UI_UX_IMPROVEMENT_PLAN.md` | Second UI pass | Superseded by `PRODUCT_PLAN.md`, which says so in its first line |
| `PUBLIC_RELEASE_CHECKLIST.md` | First pre-publication sweep | Superseded by `docs/PUBLIC_RELEASE.md` |
| `PRE_PUBLIC_REVIEW.md` | Second pre-publication review | Superseded by the current `docs/PUBLIC_RELEASE.md` and `docs/GAPS.md` |
| `PRODUCT_PLAN.md` | Setup, chat, settings, and naming proposal | Shipped portions are in code/history; remaining limitations moved to `docs/GAPS.md` |
| `VOICE_PLAN.md` | Response-style and error-language proposal | User-facing error work shipped; plausibility work remains in `docs/GAPS.md` |

Three documents that *look* like plans deliberately stayed out of here:
`UI_REWRITE_PLAN.md`, `UI_UX_AUDIT.md`, and `E2E_FINDINGS.md` are cited from
roughly 130 source files as the reason a given piece of code is the way it is.
That makes them reference material, not drafts.
