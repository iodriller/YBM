# YBM Agent — Full Pipeline Flow

This document describes how a message travels from Telegram to a completed answer.

---

## High-Level Flow

```
Telegram Message
    │
    ▼
[1. Intake & Classification]
    │   ├─ Deterministic browser guard (regex: domain/URL detected → force task)
    │   └─ LLM Classifier → is_task? + intent route
    │
    ├─ is_task=false → LLM Responder → direct Telegram reply
    │
    └─ is_task=true → TaskRecord(RECEIVED) created in DB
                            │
                            ▼
[2. Planning (TaskWorker, RECEIVED → PLANNED)]
    │   └─ LLM Planner → PlanModel (ordered steps with tool_name + input)
    │       • Input: objective + full tool registry context + memory context
    │       • Validates plan against registered tools
    │       • Fallback to hardcoded factory for status/system commands
    │
    ▼
[3. Execution (PLANNED → RUNNING, step by step)]
    │   For each PlanStep:
    │   ├─ Resolve {{placeholders}} (workspace_dir, last_entry_path, etc.)
    │   ├─ PolicyEngine checks capability enabled + risk level
    │   ├─ ToolExecutor.execute() → adapter (browser, filesystem, code, etc.)
    │   └─ Result recorded in task metadata
    │
    ├─ Step failed → RetryPolicy → RecoveryPlanFactory → LLM replan (up to 2x)
    │
    └─ All steps done → Synthesize & Validate
                            │
                            ▼
[4. Synthesis & Validation (after last content-tool step)]
    │   ├─ ResponseSynthesizer: raw tool output → focused answer (LLM call)
    │   │     • Prompt: base/synthesizer_system.md
    │   │     • Returns None if content is insufficient/technical-only
    │   │
    │   ├─ AnswerValidator: focused answer + objective → valid? (LLM call)
    │   │     • Prompt: base/validator_system.md
    │   │     • Returns True on exception (non-blocking)
    │   │
    │   ├─ If answer valid → store metadata.synthesized_answer
    │   └─ If insufficient or invalid → LLM replan (up to 2x)
    │
    ▼
[5. Fulfillment Check (_transition to COMPLETED)]
    │   └─ validate_fulfillment: structural postconditions
    │       • Did browser return a page state? Did workspace get created?
    │       • If gap detected → recovery plan or retry (up to 2x)
    │
    ▼
[6. Notification (TaskStatus.COMPLETED → Telegram)]
    │   Priority order for reply text:
    │   1. metadata.synthesized_answer  ← synthesizer output (best)
    │   2. _completed_answer()           ← formatted raw tool output
    │   3. "Done."                       ← last resort
    │
    └─ Screenshot (if available) sent as separate photo
```

---

## Key Components

| Component | File | Role |
|-----------|------|------|
| Deterministic guard | `channels/telegram.py:_is_forced_browser_task` | Regex check — forces `is_task=True` for any message with a domain/URL, bypasses LLM classifier |
| LLM Classifier | `llm/classifier.py` | Routes messages to task or conversation; uses `base/classifier_system.md` |
| LLM Responder | `channels/responder.py` | Answers non-task conversational messages; uses `base/telegram_gateway_system.md` |
| LLM Planner | `llm/planner.py` | Generates ordered PlanModel from objective + tool registry; uses `base/planner_system.md` |
| Tool Registry | `tools/registry.py` | 21 registered tools; provides `context()` string injected into planner prompt |
| Task Worker | `orchestration/worker.py` | Run-forever loop; orchestrates RECEIVED→PLANNED→RUNNING→COMPLETED transitions |
| Tool Executor | `orchestration/executor.py` | Dispatches ToolCallRequest to the correct adapter; enforces policy |
| Retry/Recovery | `orchestration/worker.py` + `recovery.py` | Step failure → retry → recovery plan → LLM replan (up to 2 replans) |
| Fulfillment Validator | `orchestration/fulfillment.py` | Structural check — did required outputs (browser_state, workspace_dir, etc.) appear? |
| Response Synthesizer | `llm/synthesizer.py` | Converts raw tool output → focused natural-language answer; uses `base/synthesizer_system.md` |
| Answer Validator | `llm/validator.py` | Checks if synthesized answer addresses the objective; uses `base/validator_system.md` |
| Notifications | `channels/telegram_notifications.py` | Formats and sends final Telegram reply |
| Conversation Memory | `channels/memory.py` | Rolling summary of prior turns; injected into classifier + planner context |

---

## Prompt Files

All system prompts live in `backend/src/agent_control/prompts/`.

```
prompts/
├── base/                          ← system prompts (role definitions)
│   ├── classifier_system.md       ← LLM Classifier role
│   ├── planner_system.md          ← LLM Planner role + rules + examples
│   ├── synthesizer_system.md      ← Response Synthesizer role
│   ├── validator_system.md        ← Answer Validator role
│   ├── telegram_gateway_system.md ← LLM Responder (non-task chat)
│   ├── conversation_memory_system.md
│   ├── computer_use_system.md
│   ├── code_interpreter_system.md
│   ├── folder_image_ocr_system.md
│   └── llm_health_check_system.md
│
├── tasks/                         ← user prompt templates (${variable} substitution)
│   ├── classifier_user.md
│   ├── planner_user.md
│   ├── validator_user.md
│   ├── telegram_gateway_user.md
│   ├── conversation_memory_user.md
│   ├── structured_retry.md        ← retry template for JSON parse failures
│   └── ...
│
└── tools/                         ← tool-specific prompts (Copilot, adapter factory)
    ├── copilot_development.md
    ├── copilot_web_app.md
    └── ...
```

---

## Replan Budget

Each task has a replan budget tracked in `metadata`:

| Counter | Max | Trigger |
|---------|-----|---------|
| `replan_count` | 2 | Synthesizer says content is insufficient; validator rejects answer |
| `evaluator_repair_count` | 2 | Recovery plan factory (step-level error recovery) |
| `fulfillment_retry_count` | 2 | Structural postcondition gap at COMPLETED transition |
| `retry_count` | Configurable | Transient step failure (network, timeout) |

When all budgets are exhausted the task transitions to `FAILED` or `BLOCKED`.

---

## Content Tools (Synthesizer + Validator Apply)

Synthesis and validation only run for tools that return human-readable content:

- `browser.open`
- `browser.control`
- `code.interpreter`
- `filesystem.manage`
- `document.manage`
- `computer.use`

Tools that return structured state (schedule IDs, workspace paths, PR URLs) skip synthesis — their outputs go directly through `_completed_answer()` formatting.
