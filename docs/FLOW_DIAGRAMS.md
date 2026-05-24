# YBM Agent — Flow Diagrams & Context Maps

---

## 1. End-to-End Pipeline

```
USER (Telegram)
    │
    │ "go to dizibox.com and tell me the 3 new episodes"
    ▼
╔══════════════════════════════════════════════════════════════╗
║  TELEGRAM INTAKE  (channels/telegram.py)                     ║
║                                                              ║
║  Step A — Deterministic Guard                                ║
║    _is_forced_browser_task(text)                             ║
║    → regex: \b[\w-]+\.(com|net|org|io|tv|...)\b              ║
║    → "dizibox.com" matches → SKIP LLM classifier            ║
║    → forge classification: is_task=True, route=BROWSER_OPEN  ║
║                                                              ║
║  Step B — (skipped) LLM Classifier                           ║
║    → Only runs if guard did NOT match                         ║
║                                                              ║
║  Step C — Create TaskRecord                                  ║
║    status: RECEIVED                                          ║
║    objective: "go to dizibox.com..."                         ║
║    metadata.source_chat_id: 8407838729                       ║
║    metadata.memory_context: <prior conversation summary>     ║
╚══════════════════════════════════════════════════════════════╝
    │
    │ reply: "Got your message, figuring out what to do 🤔"
    ▼
╔══════════════════════════════════════════════════════════════╗
║  LLM PLANNER  (llm/planner.py)                               ║
║                                                              ║
║  Input — SYSTEM PROMPT (base/planner_system.md):            ║
║    "You are the planning layer for a local agentic system.   ║
║     Return only structured JSON. Use ONLY tools listed in    ║
║     the configuration context..."                            ║
║                                                              ║
║  Input — USER PROMPT (tasks/planner_user.md):               ║
║    "Objective: go to dizibox.com and tell me..."             ║
║    "Configuration context:                                   ║
║      [FULL TOOL REGISTRY — 21 tools with operations]         ║
║      [VAULT SUMMARY — adapter availability]"                 ║
║    "Memory context: (prior turns if any)"                    ║
║                                                              ║
║  Output — PlanModel JSON:                                    ║
║    steps: [                                                  ║
║      {tool_name: "browser.open", operation: "open",          ║
║       url: "https://dizibox.com"},                           ║
║      {tool_name: "browser.open", operation: "summarize_page",║
║       objective: "list first 3 new episodes..."}             ║
║    ]                                                         ║
║                                                              ║
║  task status → PLANNED                                       ║
╚══════════════════════════════════════════════════════════════╝
    │
    │ reply: "On it 🚀 I'll send the result here when done."
    ▼
╔══════════════════════════════════════════════════════════════╗
║  TOOL EXECUTOR  (orchestration/executor.py)                  ║
║                                                              ║
║  Step 1: browser.open {operation: "open", url: "..."}        ║
║    PolicyEngine: capability browser.open enabled? ✓          ║
║    → BrowserAdapter.open("https://dizibox.com")              ║
║    → Chrome DevTools Protocol                                ║
║    result: {browser_url, page_title, screenshot_path}        ║
║                                                              ║
║  Step 2: browser.open {operation: "summarize_page",          ║
║           objective: "list first 3 new episodes..."}         ║
║    → BrowserAdapter.summarize_page(objective)                ║
║    → Reads DOM, extracts text                                ║
║    result: {summary: "MY ROYAL NEMESIS 1.SEZON 6.BÖLÜM...    ║
║             WE ARE ALL TRYING...  SHERIFF COUNTRY..."}       ║
║                                                              ║
║  task status → RUNNING                                       ║
╚══════════════════════════════════════════════════════════════╝
    │
    ▼
╔══════════════════════════════════════════════════════════════╗
║  RESPONSE SYNTHESIZER  (llm/synthesizer.py)                  ║
║                                                              ║
║  Triggered because: last tool "browser.open" ∈ CONTENT_TOOLS ║
║                                                              ║
║  Input — SYSTEM PROMPT (base/synthesizer_system.md):        ║
║    "You are a response synthesizer. Extract a direct,        ║
║     focused answer. Return INSUFFICIENT only if content      ║
║     is empty or totally unrelated."                          ║
║                                                              ║
║  Input — USER PROMPT:                                        ║
║    "Question: go to dizibox.com and tell me the 3 new        ║
║     episodes listed under Yeni Eklenen Bölümler              ║
║                                                              ║
║     Raw content:                                             ║
║     MY ROYAL NEMESIS 1.SEZON 6.BÖLÜM 23 MAY                 ║
║     WE ARE ALL TRYING HERE 1.SEZON 11.BÖLÜM 23 MAY          ║
║     SHERIFF COUNTRY 1.SEZON 20.BÖLÜM 23 MAY                 ║
║     ... (up to 6000 chars)"                                  ║
║                                                              ║
║  Output — synthesized_answer:                                ║
║    "1. My Royal Nemesis 1.Sezon 6.Bölüm                      ║
║     2. We Are All Trying Here 1.Sezon 11.Bölüm               ║
║     3. Sheriff Country 1.Sezon 20.Bölüm"                     ║
╚══════════════════════════════════════════════════════════════╝
    │
    ▼
╔══════════════════════════════════════════════════════════════╗
║  ANSWER VALIDATOR  (llm/validator.py)                        ║
║                                                              ║
║  Input — SYSTEM PROMPT (base/validator_system.md):          ║
║    "Given an objective and proposed answer, determine if     ║
║     the answer addresses what was asked.                     ║
║     Return YES or NO: <reason>"                              ║
║                                                              ║
║  Input — USER PROMPT (tasks/validator_user.md):             ║
║    "Objective: go to dizibox.com and tell me the 3 new       ║
║     episodes listed under Yeni Eklenen Bölümler              ║
║                                                              ║
║     Proposed answer:                                         ║
║     1. My Royal Nemesis 1.Sezon 6.Bölüm                      ║
║     2. We Are All Trying Here 1.Sezon 11.Bölüm               ║
║     3. Sheriff Country 1.Sezon 20.Bölüm                      ║
║                                                              ║
║     Does this answer address the objective?"                 ║
║                                                              ║
║  Output: "YES"                                               ║
║  → store metadata.synthesized_answer                         ║
║  → proceed to COMPLETED                                      ║
╚══════════════════════════════════════════════════════════════╝
    │
    ▼
╔══════════════════════════════════════════════════════════════╗
║  FULFILLMENT CHECK  (orchestration/fulfillment.py)           ║
║                                                              ║
║  Structural postconditions for this task:                    ║
║    BROWSER_STATE expected? YES (plan had browser.open)       ║
║    browser_url in metadata? YES ✓                            ║
║    → validation OK → COMPLETED                               ║
╚══════════════════════════════════════════════════════════════╝
    │
    ▼
╔══════════════════════════════════════════════════════════════╗
║  TELEGRAM NOTIFICATION  (channels/telegram_notifications.py) ║
║                                                              ║
║  Priority lookup:                                            ║
║    1. metadata.synthesized_answer? → YES → send it           ║
║                                                              ║
║  Message sent to user:                                       ║
║    "1. My Royal Nemesis 1.Sezon 6.Bölüm                      ║
║     2. We Are All Trying Here 1.Sezon 11.Bölüm               ║
║     3. Sheriff Country 1.Sezon 20.Bölüm"                     ║
╚══════════════════════════════════════════════════════════════╝
    │
    ▼
USER receives focused answer ✓
```

---

## 2. Context Passed Between Each Stage

```
INTAKE → PLANNER
─────────────────────────────────────────────────────────────
TaskRecord.objective:    "go to dizibox.com and tell me..."
TaskRecord.metadata:
  source_chat_id:       8407838729
  memory_context:       "No durable conversation memory yet."
                        (or summary of prior task results)

config_context (from _worker_config_context in cli.py):
  ├─ Full tool registry (21 tools, ~4000 chars):
  │    browser.open: open | summarize_page | screenshot | ...
  │    browser.control: navigate | click | fill_form | ...
  │    filesystem.manage: inspect_folder | search | read_file | ...
  │    code.interpreter: generate_and_run
  │    computer.use: run_goal | observe
  │    schedule.manage: create | list | delete | run_now
  │    ... (21 total)
  ├─ Vault summary (adapter status):
  │    browser: available (Chrome DevTools at port 9222)
  │    vscode: available
  │    workspace: available
  └─ Planning guidance:
       "Prefer conservative plans. Use exact tool names..."


PLANNER → EXECUTOR
─────────────────────────────────────────────────────────────
PlanModel.steps[]:
  step 1:
    tool_name:           "browser.open"
    tool_input:          {operation: "open", url: "https://dizibox.com"}
    required_capabilities: ["browser.open"]
    risk_level:          low
    requires_approval:   false
    timeout_seconds:     120

  step 2:
    tool_name:           "browser.open"
    tool_input:          {operation: "summarize_page",
                          objective: "list first 3 new episodes...",
                          timeout_seconds: 120}
    required_capabilities: ["browser.open"]


EXECUTOR → SYNTHESIZER
─────────────────────────────────────────────────────────────
ToolCallResult (from last step):
  status:   SUCCEEDED
  output:
    operation:     "summarize_page"
    summary:       "MY ROYAL NEMESIS 1.SEZON 6.BÖLÜM 23 MAY\n
                   WE ARE ALL TRYING HERE 1.SEZON 11.BÖLÜM...\n
                   SHERIFF COUNTRY 1.SEZON 20.BÖLÜM...\n
                   [full page text, up to 6000 chars used]"
    browser_url:   "https://dizibox.com"
    page_title:    "DiziBox - Dizi İzle"
    visited_urls:  ["https://dizibox.com"]

+ task.objective: "go to dizibox.com and tell me..."


SYNTHESIZER → VALIDATOR
─────────────────────────────────────────────────────────────
task.objective:    "go to dizibox.com and tell me..."
answer:            "1. My Royal Nemesis 1.Sezon 6.Bölüm
                    2. We Are All Trying Here 1.Sezon 11.Bölüm
                    3. Sheriff Country 1.Sezon 20.Bölüm"


VALIDATOR → WORKER (decision)
─────────────────────────────────────────────────────────────
valid: True   → store synthesized_answer, go to COMPLETED
valid: False  → _replan_with_error (up to 2 attempts)


WORKER → NOTIFICATION
─────────────────────────────────────────────────────────────
TaskRecord.status:    COMPLETED
TaskRecord.metadata:
  synthesized_answer: "1. My Royal Nemesis..."
  browser_url:        "https://dizibox.com"
  page_title:         "DiziBox - Dizi İzle"
  last_tool_name:     "browser.open"
  source_chat_id:     8407838729
```

---

## 3. All Available Tools & Adapters (21 Tools)

```
┌─────────────────────────────────────────────────────────────────┐
│  TOOL NAME          │  KEY OPERATIONS                           │
├─────────────────────┼───────────────────────────────────────────┤
│ browser.open        │ open, summarize_page, screenshot,         │
│                     │ research_pages, chain                     │
│ browser.control     │ navigate, click, fill_form, submit,       │
│                     │ extract_page_state, wait                  │
│ filesystem.manage   │ inspect_folder, search, read_file,        │
│                     │ write_text_file, organize, rename         │
│ code.interpreter    │ generate_and_run, run_python              │
│ computer.use        │ run_goal (multi-step UI), observe         │
│ desktop.screenshot  │ capture                                   │
│ vscode.read_state   │ get_open_files, get_selection, list_tabs  │
│ vscode.write_files  │ apply_edits, create_file, delete_file     │
│ document.manage     │ summarize_pdf, extract_text,              │
│                     │ create_presentation, update_presentation  │
│ artifact.deliver    │ send_file, send_latest, send_screenshot   │
│ workspace.manage    │ prepare, write_files, web_app_preview,    │
│                     │ materialize_static_app                    │
│ schedule.manage     │ create, list, pause, resume, delete,      │
│                     │ run_now                                   │
│ adapter.factory     │ scaffold, list, describe                  │
│ coding.agent        │ run_goal, run_step, status, limits        │
│ github.read         │ get_pr, list_prs, get_file               │
│ github.push         │ create_pr, push_branch                   │
│ terminal.run        │ run_command, run_script                   │
│ task.status         │ status, list_recent                       │
│ stt.transcribe      │ transcribe (voice→text)                   │
│ llm.generate        │ generate_text, generate_structured        │
└─────────────────────┴───────────────────────────────────────────┘

Capabilities required per tool (enforced by PolicyEngine):
  browser.open        → Capability.BROWSER_OPEN       (high risk)
  browser.control     → Capability.BROWSER_CONTROL    (high risk)
  filesystem.manage   → Capability.FILESYSTEM_READ /  (low/high)
                         FILESYSTEM_WRITE
  code.interpreter    → Capability.TERMINAL_RUN       (high risk)
  computer.use        → Capability.DESKTOP_CONTROL    (critical)
  desktop.screenshot  → Capability.DESKTOP_SCREENSHOT (low risk)
  vscode.*            → Capability.VSCODE_READ_STATE /
                         VSCODE_WRITE_FILES
  schedule.manage     → Capability.SCHEDULE_MANAGE    (medium)
  github.*            → Capability.GITHUB_READ /
                         GITHUB_PUSH                  (critical)
```

---

## 4. Failure Path: When Synthesizer Says INSUFFICIENT

```
SCENARIO: Plan was browser.control extract_page_state
          → returned form/DOM data, no readable content

EXECUTOR → SYNTHESIZER
  raw: "Detected 4 form(s) on https://dizibox.live/."

SYNTHESIZER
  LLM call with raw content
  → LLM: "INSUFFICIENT"  (no episode data in form list)
  → synthesize() returns None

WORKER._synthesize_and_validate
  answer is None → _replan_with_error(
    "The retrieved content did not contain a clear answer..."
  )

WORKER._replan_with_error
  replan_count: 0 → 1
  enriched_objective:
    "go to dizibox.com...
     [Previous attempt failed: content did not contain answer.
      Try a different approach or different tool]"

  LLM PLANNER called again with error context:
    "Error context: The retrieved content did not contain..."
    → New plan:
      step 1: browser.open {operation: "open", url: "..."}
      step 2: browser.open {operation: "summarize_page", ...}
    (switches from extract_page_state to summarize_page)

  task status → RECEIVED → PLANNED → RUNNING (second attempt)

  ─────────────────────────────────────────
  If replan_count reaches 2 and still fails:
    _replan_with_error returns None
    task → COMPLETED without synthesized_answer
    notification falls back to _completed_answer() → raw browser output
  ─────────────────────────────────────────
```

---

## 5. Failure Path: When Validator Rejects the Answer

```
SYNTHESIZER produces answer:
  "The page has many episodes available."  ← too vague

VALIDATOR
  system: "Return YES if answer provides the specific
           information requested..."
  user:   "Objective: tell me the first 3 new episodes...
           Proposed answer: The page has many episodes available."
  → LLM: "NO: does not list the specific 3 episode names"
  → validate() returns False

WORKER._synthesize_and_validate
  valid=False → audit: answer_rejected
              → _replan_with_error(
                  "The answer did not address the question.
                   Answer was: The page has many episodes...
                   Try a different approach."
                )

  LLM PLANNER creates new plan with more specific objective
  e.g. adds explicit scroll/target instructions to find section
```

---

## 6. Replan Budget Tracker

```
Each task carries these counters in metadata:

  replan_count          (max 2)  — triggered by:
    • synthesizer returns None (content insufficient)
    • validator returns False (answer rejected)
    • tool failure with no retry budget left

  evaluator_repair_count (max 2) — triggered by:
    • tool execution fails with ADAPTER_FAILED or VALIDATION_FAILED

  fulfillment_retry_count (max 2) — triggered by:
    • structural postcondition gap at COMPLETED transition
      (e.g. plan said browser.open but browser_url not in metadata)

  retry_count           (configurable) — triggered by:
    • transient tool failure (network timeout, temp error)

When ALL budgets exhausted → task → FAILED or BLOCKED
```

---

## 7. Full Prompt Text: Each Stage (What the LLM Actually Sees)

### Stage: LLM Planner

**System prompt** (`base/planner_system.md`, ~900 chars):
```
You are the planning layer for a local agentic control system
controlled via Telegram.
Return only structured JSON matching the requested schema.
Do not include any text outside the JSON.

## Your role
Generate a concrete, ordered execution plan for the user's
objective using the available tools.

## Multi-step planning rules
1. Find + Read = 2 steps
2. Browser interaction = chain steps: open → extract_page_state
3. URL detection → use browser.open
4. Unknown task → use code.interpreter with generate_and_run
...
```

**User prompt** (`tasks/planner_user.md`, rendered with values):
```
Objective: go to dizibox.com and tell me the 3 new episodes

Configuration context:
  browser.open:
    open: Open a URL in Chrome. Input: {url, wait_seconds}
    summarize_page: Summarize visible page content. Input: {objective}
    screenshot: Capture page screenshot.
    ... (all 21 tools with all operations, ~4000 chars)

  Vault summary:
    browser adapter: Chrome at localhost:9222 — available
    vscode adapter: bridge at localhost:8766 — available
    ...

Memory context:
  No durable conversation memory yet.
```

---

### Stage: Response Synthesizer

**System prompt** (`base/synthesizer_system.md`, ~300 chars):
```
You are a response synthesizer for a personal assistant.
Given a user's question and raw tool output, extract and
return a direct, focused answer.

Rules:
- Answer only what was asked. Be concise and specific.
- Return INSUFFICIENT only if content is completely empty,
  is technical state data with no readable content, or
  is totally unrelated to the question.
- Do not include metadata like page titles or URLs.
- Respond in the same language the user used.
```

**User prompt** (inline in synthesizer.py):
```
Question: go to dizibox.com and tell me the 3 new episodes

Raw content:
MY ROYAL NEMESIS 1.SEZON 6.BÖLÜM 23 MAY
WE ARE ALL TRYING HERE 1.SEZON 11.BÖLÜM 23 MAY
SHERIFF COUNTRY 1.SEZON 20.BÖLÜM 23 MAY
Kahraman 1.Sezon 25.Bölüm...
[up to 6000 chars of page text]
```

---

### Stage: Answer Validator

**System prompt** (`base/validator_system.md`, ~250 chars):
```
You are an answer quality validator.
Given an objective and a proposed answer, determine if the
answer actually addresses what was asked.

Return only "YES" if the answer provides the specific
information requested (names, numbers, items, facts).
Return only "NO: <brief reason>" if off-topic or missing
key requested information.
Be lenient about formatting.
```

**User prompt** (`tasks/validator_user.md`, rendered):
```
Objective: go to dizibox.com and tell me the 3 new episodes
listed under Yeni Eklenen Bölümler

Proposed answer:
1. My Royal Nemesis 1.Sezon 6.Bölüm
2. We Are All Trying Here 1.Sezon 11.Bölüm
3. Sheriff Country 1.Sezon 20.Bölüm

Does this answer address the objective?
```

---

### Stage: LLM Classifier (when deterministic guard doesn't fire)

**System prompt** (`base/classifier_system.md`, ~2000 chars):
```
You are the structured intake router for a local Windows
agent-control system. Return only JSON matching the
requested schema.

Decide whether the user message should spawn a persisted task.
- is_task=false only for greetings, thanks, normal chat,
  capability questions, and status questions.
- is_task=true when the user asks to inspect, open, control,
  create, organize, send, schedule, code, browse...
- CRITICAL: If the message mentions a website, domain, or URL,
  ALWAYS set is_task=true regardless of conversation history.

Routes: conversation | status | desktop.observe | computer.use |
  browser.open | browser.control | filesystem.manage |
  document.manage | artifact.deliver | code.interpreter |
  coding.agent | schedule.manage | adapter.factory |
  workspace.manage | configuration | unknown
```

**User prompt** (`tasks/classifier_user.md`, rendered with context):
```
Message: go to dizibox.com and tell me the 3 new episodes

Context:
  LLM profile: localdeploy_qwen3vl_8b
  Telegram receive/send: enabled
  VS Code route: enabled (approval-free)
  Local workspaces: enabled; root=.agent_control/workspaces
  Terminal command route: enabled
  Desktop screenshots: enabled
  Conversation memory: No durable conversation memory yet.
  Recent tasks: 3
  Active tasks: 0
  Recent task list:
  - task_4c057a3...: completed - go to dizibox.com...
```
