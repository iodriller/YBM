# YBM Prompt Audit & Gap Analysis

This document walks through every LLM call in the pipeline, shows the **full
raw system and user prompts**, lists what context is injected at each stage,
and calls out gaps + recommended improvements.

---

## GPU Constraint (Important Pre-Read)

`ollama ps` reports:

```
NAME            ID              SIZE     PROCESSOR    CONTEXT
qwen2.5vl:7b    5ced39dfa4ba    13 GB    100% CPU     8192
```

The RTX 3080 Laptop has **8 GB VRAM**. Several test models do not fit:

| Model | Approx size | Fits on RTX 3080? |
|-------|-------------|-------------------|
| qwen2.5vl:3b           | ~2.4 GB | ✓ Yes |
| qwen3-vl:4b-instruct   | ~3.4 GB | ✓ Yes |
| qwen3-vl:8b-instruct   | ~5.7 GB | ✓ Yes (tight, depends on context) |
| qwen2.5vl:7b           | ~6.3 GB quantized / 13 GB raw | ⚠ Maybe with quantization |
| gemma3:12b             | ~8.4 GB | ✗ No |

To force NVIDIA-only on Ollama, set BEFORE `ollama serve` starts:

```powershell
$env:CUDA_VISIBLE_DEVICES = "0"
$env:OLLAMA_NUM_GPU = "999"        # force all layers to GPU when it fits
$env:OLLAMA_KEEP_ALIVE = "30m"     # keep models loaded between calls
```

If the model still falls back to CPU, the model is too big — drop to a smaller
variant or a quantized (q4_K_M) build.

---

## The 7 LLM Call Sites (in execution order)

```
1. Conversation Memory Summarizer     (channels/memory.py)
2. Message Classifier                 (llm/classifier.py)
3. Telegram Gateway Responder         (channels/responder.py)      — non-task only
4. Plan Generator                     (llm/planner.py)
5. Inner-Tool LLMs (during execution):
   5a. Code Interpreter Script Gen   (tools/code_interpreter adapter)
   5b. Computer-Use Decisions        (tools/computer_use adapter)
   5c. Browser summarize_page        (tools/browser adapter)
   5d. Folder image OCR description  (tools/filesystem adapter)
6. Response Synthesizer               (llm/synthesizer.py)
7. Answer Validator                   (llm/validator.py)
```

---

## 1. Conversation Memory Summarizer

**Trigger:** every Telegram turn before classification.

**System prompt** (`prompts/base/conversation_memory_system.md`):
```
Maintain concise memory for a Telegram LLM gateway.
Return only a compact plain-text memory summary, no JSON and no preamble.
Keep durable facts, user preferences, project goals, decisions, constraints,
and unresolved follow-ups.
Drop greetings, duplicate wording, and transient chatter.
Do not invent details.
Maximum length: ${max_summary_chars} characters.
```

**User prompt** (`prompts/tasks/conversation_memory_user.md`):
```
Existing memory:
${existing_summary}

Recent turns:
${recent_turns}

Update the memory summary.
```

**Context injected:** the prior summary, last 10 user/assistant/task turns.

### Gaps
- **No domain priors.** Memory has no concept of the user's role, recurring
  targets (dizibox.com), or commonly used capabilities. Each summary starts
  fresh from "recent turns" with no anchor.
- **Updates can drift.** No rule like "preserve any explicit user preferences
  (language, model choice, allowed websites) verbatim."
- **No section structure.** Free-form blob. A `## Preferences / ## Goals /
  ## Recent decisions` schema would make extraction easier downstream.
- **Char budget unenforced inside the LLM.** We pass `max_summary_chars` but a
  local model often ignores it. Need post-trim.

### Recommendations
1. Restructure into labeled sections so downstream prompts can quote specific
   blocks: `Preferences: ...`, `Recurring sites: ...`, `Open follow-ups: ...`.
2. Add to the system prompt: "Preserve any explicit user preference (language,
   tone, allowed apps, blocked apps) verbatim across updates."
3. Always post-trim to `max_summary_chars` in Python regardless of LLM output.
4. Skip the summarizer when only a single new turn is "ok" / "thanks" / etc.

---

## 2. Message Classifier (the routing decision)

**Trigger:** every Telegram message that has text.

**System prompt** (`prompts/base/classifier_system.md`, ~2 KB):
```
You are the structured intake router for a local Windows agent-control
system. Return only JSON matching the requested schema.

Decide whether the user message should spawn a persisted task.
- is_task=false only for greetings, thanks, normal chat, capability
  questions, and status questions.
- is_task=true when the user asks to inspect, open, control, create,
  organize, send, schedule, code, browse, manage files, or run a workflow.
- CRITICAL: If the message mentions a website, domain (e.g. dizibox.com),
  or URL, ALWAYS set is_task=true regardless of conversation history or
  memory context. Do NOT use memory to answer live web requests.
- normalized_objective is a concise actionable objective preserving
  constraints.
- confidence reflects route certainty.

Routes:
- conversation | status | desktop.observe | computer.use | browser.open |
- browser.control | filesystem.manage | document.manage | artifact.deliver |
- code.interpreter | coding.agent | schedule.manage | adapter.factory |
- workspace.manage | configuration | unknown

Routing rules:
- Do not select coding.agent unless the user explicitly names Codex...
- ... (28 lines of detailed routing rules)
```

**User prompt** (`prompts/tasks/classifier_user.md`):
```
Classify this inbound message and return a complete JSON object.

Channel: ${channel}
Kind: ${kind}
Sender: ${sender_id}
Chat: ${chat_id}
Concise conversation/task context:
${context}

Text:
${text}

Required behavior:
- Decide whether this should spawn a persisted task.
- Fill task_type, normalized_objective, confidence, and reason.
- Fill intent with a route, operation, objective, reasoning, and any
  extracted fields when the message is actionable.
- For non-task status/help/conversation, use intent.route=status or
  conversation.
- Use null for unknown optional fields; do not invent local paths, URLs,
  fields, or providers.
```

**Context injected:**
- channel/kind/sender/chat — IDs from the inbound message
- `${context}` — `memory_context(memory_record, recent_turns=3, max_chars=900)`
  i.e. the durable summary + last 3 turns

### Gaps
- **No tool registry context.** Classifier sees route names but not which tools
  actually exist or what they can do. It can pick a route the planner can't
  satisfy. Example: classifier returns `route=computer.use` for a request that
  only needs `browser.open`.
- **`normalized_objective` is over-aggressive.** Classifier rewrites the user's
  request ("go to dizibox.com..." → "Retrieve and list the first 5 new
  episodes..."), losing the explicit URL. The planner then has to re-infer
  the URL.
- **Memory in 900 chars but classifier prompt says "Do NOT use memory to
  answer live web requests" — but the planner DOES see memory.** The split is
  fine but the classifier rule could be misread as "memory should not be used
  at all" which is wrong for follow-up questions.
- **No language detection.** A Turkish message gets classified the same as
  English, but `normalized_objective` is always rewritten in English. That
  makes the planner objective drift from the user's words and the synthesizer
  may answer in the wrong language.
- **Confidence is unused downstream.** No threshold gating, no fallback.

### Recommendations
1. Add a "Preserve URLs verbatim" rule: if the user message contains an
   explicit URL or domain, keep it in `normalized_objective` exactly as
   written.
2. Inject a **short** tool registry summary (5 tools max, one line each) so
   classifier route choices align with available adapters.
3. Detect message language and pass it through; have synthesizer/validator
   respect it.
4. Use `confidence < 0.5` to short-circuit to a "please clarify" reply
   instead of guessing a route.
5. Drop the "normalize" rewriting unless the user message is ambiguous —
   preserving the original wording almost always helps the planner.

---

## 3. Telegram Gateway Responder (non-task only)

**Trigger:** `is_task=false` from the classifier.

**System prompt** (`prompts/base/telegram_gateway_system.md`):
```
You are the Telegram gateway for a local agent-control system.
Answer direct questions concisely.
Use the provided runtime context for current capabilities and task state.
Do not claim a capability is enabled unless the context says it is enabled.
If the user asks for work that should be executed by tools, say it should
be sent as a task and mention the relevant enabled route.
```

**User prompt** (`prompts/tasks/telegram_gateway_user.md`):
```
Runtime context:
${context}

Telegram message:
${message_text}

Reply in plain text suitable for Telegram.
```

**Context injected** (built in `channels/responder.py:_gateway_context`):
- LLM profile name
- For each capability: enabled/disabled + approval-free/gated
- Workspace root, adapter factory root
- Conversation memory summary
- Recent tasks (last 5) + active count

### Gaps
- **This is where most of the "synthesizer didn't fire" complaints come from.**
  When the classifier mistakenly routes a real task to "conversation" (it
  happened in the bovykbwcv test — replied "Retrieving and displaying..."
  without spawning a task), the responder confidently hallucinates that work
  is in progress. There is no rule like "if the message clearly asks for a
  live web action, do NOT answer — say it should be sent as a task."
- **System prompt is only 5 lines.** Doesn't constrain hallucination beyond
  the capability-enabled check. The responder will happily make up content
  about a task.
- **No guardrail against impersonating task progress.** Strings like "I'm
  retrieving..." / "let me check..." / "fetching now..." should be forbidden
  for the non-task responder — it has no tools, it cannot do those things.

### Recommendations
1. Add hard rules to the system prompt:
   - "You have NO tools. You cannot fetch web pages, read files, or run code."
   - "If the user message requests fetching, reading, browsing, or any active
     work, DO NOT pretend to do it. Reply: 'That needs a task — let me kick
     one off' and stop."
   - "Never use phrases like 'I'm retrieving', 'fetching', 'displaying',
     'showing now', or any other claim of in-progress work."
2. Limit the runtime context to capability flags only; do NOT pass the
   recent-task list (it tempts the LLM to reference completed work as if
   ongoing).

---

## 4. Plan Generator (the planner — biggest LLM step)

**Trigger:** `is_task=true` → task moves to INTERPRETING.

**System prompt** (`prompts/base/planner_system.md`, ~1.5 KB):
```
You are the planning layer for a local agentic control system controlled
via Telegram.
Return only structured JSON matching the requested schema. Do not include
any text outside the JSON.

## Your role
Generate a concrete, ordered execution plan for the user's objective using
the available tools.

## Multi-step planning rules
1. Find + Read = 2 steps
2. Browser interaction = chain steps: open → extract_page_state → ...
3. URL detection → use browser.open with operation "open"
4. Unknown task → use code.interpreter with generate_and_run
5. Delivery → add artifact.deliver step
6. Browser fallback → if Chrome unavailable, use code.interpreter +
   urllib.request with browser User-Agent.

## Example plans
[3 worked examples: dizibox, find+read resume.pdf, code interpreter excel]

## Constraints
- Only use capabilities listed as enabled in the configuration context
- Set requires_approval: false for low-risk read operations
- timeout_seconds: 60 for simple operations, 120-180 for browser/complex
- All Python imports are allowed in code.interpreter
```

**User prompt** (`prompts/tasks/planner_user.md`):
```
Create an execution plan for this objective:

${objective}

${memory_context}
Configuration and available tools (only use capabilities listed as enabled):
${config_context}

Requirements:
- Include concrete tool steps with specific operations and inputs
- For browser tasks with an explicit URL or domain name, always use
  browser.open with operation "open" and the url field set
- For "find and read" file requests, create two steps: search then read_file
- For complex tasks, chain multiple steps — do not try to do everything in
  one step
- Set risk_level appropriately: low for reads, high for writes/browser
  control, critical for desktop control
- Include assumptions, required_capabilities, ordered steps with tool_name
  and tool_input, success_criteria, and postconditions
```

**Context injected** (the heavyweight one, built by
`cli.py:_worker_config_context`):
- `registry.context()` — every tool + every operation + every input field +
  capability + risk level. **~4–5 KB of dense text for 21 tools.**
- `registry.vault_summary()` — which adapters are wired up.
- Trailing line: "Prefer conservative plans. Use registered tool names
  exactly..."

Plus on replan attempts:
- `enriched_objective` containing `[Previous attempt failed: ...]` in the
  objective slot
- The system-prompt + retry prompt with the schema validation error

### Gaps
- **Tool registry context is enormous and unranked.** 4–5 KB of tool docs +
  every operation = the LLM frequently confuses operation names across tools
  (uses `browser.control extract_page_state` when `browser.open
  summarize_page` would work, because both appear in the context).
- **No "common cases first" cheat sheet.** Half the user's requests hit 3–4
  recipes (open URL + summarize, find file + read, generate Python). Those
  should be at the TOP of the context, not buried inside the full registry
  dump.
- **The Capability enum values are not visible in the system prompt.** They
  show up in retry errors. Local models keep hallucinating capability names
  on the first try. (This was the failure that caused the dizibox 5-episode
  task to break — 19 validation errors all about invalid capability strings.)
- **Risk-level guidance is conflicting.** Constraint says "high for browser
  control" but `browser.open` capability is configured high too. Not clear
  to the LLM when "low" applies.
- **`memory_context` is empty most of the time.** It's pulled from the
  rolling summary but the planner sees no info about recent tasks (whether
  the user just succeeded at this domain, whether a similar plan worked).
- **No "anti-pattern" rules.** No "DO NOT add success_criteria to individual
  steps, only at the plan level" — which is exactly what Qwen3-VL 8B kept
  doing.
- **Replan error context is short.** `error_context[:400]` is added to the
  config_context, but the actual validation error (which can be 2 KB of
  Pydantic field paths) is what the LLM needs to fix the JSON.
- **Memory_context section header is included even when memory is empty**
  (`"## Conversation context\n\n\n"`) — wastes tokens with nothing.

### Recommendations (highest impact for chain reliability)
1. **Add an "Allowed enum values" block to the system prompt** with the
   exact Capability enum strings. Local models DRAMATICALLY improve when
   enum values are repeated multiple times.
2. **Add an explicit "DO NOT add these fields" block:**
   - Do not add `success_criteria` to a step. It belongs only at plan level.
   - Do not add `validation`, `notes`, `comments`, or `expected_result` to
     any step.
3. **Restructure `config_context` with priority ordering:**
   ```
   ## Most common recipes
   Open URL + summarize: browser.open(open) → browser.open(summarize_page)
   Find + read file: filesystem.manage(search) → filesystem.manage(read_file)
   Generate code: code.interpreter(generate_and_run)
   ...

   ## All tools (full reference)
   <existing dump>
   ```
4. **Stop normalizing the objective in the classifier.** Pass the raw
   message text alongside the normalized one and use the raw text for
   planning.
5. **Pass the full validation error (not truncated to 400 chars) on retry.**
   The LLM needs the field paths to fix them.
6. **On the 2nd retry, inject a known-good example plan structure** for
   that route. e.g. for browser tasks, show a minimal valid PlanModel JSON
   as a few-shot anchor.

---

## 5. Inner-Tool LLM Calls (during execution)

These run INSIDE adapters as part of executing a plan step. They are
invisible to the planner/synthesizer/validator chain but affect quality.

### 5a. Code Interpreter Script Generator
`prompts/base/code_interpreter_system.md`:
```
You generate small Python scripts for a local, policy-gated code
interpreter.

Rules:
- Return structured JSON only.
- Keep the script focused on the requested local task.
- Use only Python standard-library modules that are safe for local data
  processing.
- Do not use shell commands, network calls, package installation,
  subprocesses, OS mutation APIs, or absolute paths outside the workspace.
- Write any generated files inside the provided workspace.
- Print a concise final summary of what the script did.
```

**Gap:** The rule says "stdlib only" but the planner system prompt says
"All Python imports are allowed in code.interpreter — pandas, openpyxl,
requests, pathlib, etc." These directly contradict each other. The planner
generates a step assuming pandas, the inner LLM refuses to import it.

**Fix:** Reconcile. If pandas/openpyxl are actually installed in the venv,
update the inner prompt to say "stdlib + pandas, openpyxl, requests,
pathlib, numpy are allowed."

### 5b. Computer-Use Decision
`prompts/base/computer_use_system.md`:
```
You are a local Windows computer-use controller.
Use only the current screenshot and UI summary.
Return concise JSON only when asked for the next action.
Prefer safe, reversible actions. Stop when the visible state satisfies
the objective.
Do not invent hidden screen content.
```

User prompt (`computer_use_decision.md`) is well-structured (lists allowed
action types + JSON shape).

**Gap:** No "stop after N steps" reminder — the LLM may loop. The wrapper
enforces it (config: `max_steps: 8`) but the LLM doesn't know that and
sometimes proposes long action sequences.

### 5c. Browser summarize_page
This uses an inline prompt inside the browser adapter (not an .md). The
adapter passes page text → LLM → summary. The "summary" can be quite long
(the whole page) which is what later confuses the response synthesizer.

**Gap:** No `objective` parameter is passed through to the inner LLM in
some code paths, so it generates a generic page summary instead of an
objective-focused extraction.

**Fix:** Always pass the original task objective into the page-summary
prompt; instruct the LLM to extract ONLY content relevant to that
objective.

### 5d. Folder Image OCR
`prompts/base/folder_image_ocr_system.md`:
```
You inspect a single local image for a folder-description task.
Return a concise factual description of visible text and relevant visual
content.
If the image is unreadable or text is not visible, say that clearly.
```

This one is fine. Small and focused.

---

## 6. Response Synthesizer

**Trigger:** last plan step succeeded and its tool is a content tool
(browser.*, code.interpreter, filesystem.manage, document.manage,
computer.use).

**System prompt** (`prompts/base/synthesizer_system.md`):
```
You are a response synthesizer for a personal assistant.

Your task: given a user's question and raw tool output, extract and return
a direct, focused answer.

Rules:
- Answer only what was asked. Be concise and specific.
- If the raw content clearly contains the answer, state it directly in
  natural language.
- If the content is related but does not have the exact answer, provide
  the most relevant information available and note what is missing.
- Only respond with the single word INSUFFICIENT if the content is
  completely empty, is technical state data (form elements, DOM tree,
  page source) with no readable content, or is totally unrelated to the
  question.
- Do not include metadata like page titles, URLs, "Visited X pages", or
  tool names.
- Respond in the same language the user used in their question.
```

**User prompt** (inline string in `llm/synthesizer.py`):
```
Question: ${objective}

Raw content:
${raw_content[:6000]}
```

**Context injected:**
- `task.objective` — the normalized objective (re-rewritten English version
  from the classifier).
- Raw text extracted by `_tool_output_text(result)` — terminal_output blocks,
  or `final_summary`/`summary`/`text`/`message`/`content` from the tool
  output (first 6000 chars).

### Gaps
- **The synthesizer sees the NORMALIZED objective, not the user's original
  message.** This is why answers sometimes drift from what the user actually
  asked. ("first 5 new episodes" normalized to "Retrieve and list the first
  5 new episodes" — the LLM doesn't see "Yeni Eklenen Bölümler" exactly as
  the user typed, so it can answer with wrong section content.)
- **6000-char truncation is mid-sentence.** No structure-aware trimming.
- **No multi-shot examples.** Local models do much better with one
  exemplar of (objective → raw text → focused answer).
- **No language pin.** "Respond in the same language the user used" works
  only if the user message is in the prompt. The objective often is in
  English (post-normalization) but the user typed in Turkish.

### Recommendations
1. Pass `task.metadata.original_message_text` AND `task.objective` so the
   synthesizer can see both the user's exact wording and the normalized
   form.
2. Add a worked example to the system prompt:
   ```
   Example:
   Question: tell me the first 3 episodes from dizibox.com
   Raw: "Yeni Eklenen Bolumler MY ROYAL NEMESIS 1.SEZON 6.BOLUM 23 MAY..."
   Answer:
   1. My Royal Nemesis 1.Sezon 6.Bölüm
   2. We Are All Trying Here 1.Sezon 11.Bölüm
   3. Sheriff Country 1.Sezon 20.Bölüm
   ```
3. Smarter trimming: cut at paragraph boundaries, prefer the first half of
   long page summaries.

---

## 7. Answer Validator

**Trigger:** synthesizer produced a non-empty answer.

**System prompt** (`prompts/base/validator_system.md`):
```
You are an answer quality validator.

Given an objective and a proposed answer, determine if the answer
actually addresses what was asked.

Rules:
- Return only "YES" if the answer provides the specific information
  requested in the objective (names, numbers, items, facts).
- Return only "NO: <brief reason>" if the answer is off-topic, does not
  contain the key requested information, or is a generic/empty response.
- Be lenient about formatting; what matters is whether the core requested
  information is present.
- A partial answer that contains at least some of the requested items
  counts as YES.
```

**User prompt** (`prompts/tasks/validator_user.md`):
```
Objective: ${objective}

Proposed answer:
${answer}

Does this answer address the objective?
```

### Gaps
- **Validator only sees objective + answer, no raw tool output.** It can't
  catch hallucinations — if the synthesizer made up the 3 episode names,
  the validator can't know.
- **Same normalized-objective drift problem** as the synthesizer.
- **"Partial answer counts as YES" is dangerous for the 5-episode case.**
  If the synthesizer returns only 3 of the requested 5, validator says YES
  and the user is told "done" with a partial answer.
- **No structured output.** Just YES/NO string — harder to gate
  downstream. A `{valid: bool, missing: [...], reason: ...}` would feed
  better into replan.
- **No "count check"** for numeric requests. The user asked for 5, the
  validator doesn't count items in the answer.

### Recommendations
1. Give the validator both the objective AND a snippet of the raw tool
   output (first 1500 chars). Validator can spot fabrications by comparing.
2. Tighten the leniency rule: "If the user requested a specific COUNT of
   items (3 episodes, top 5, first 10), the answer MUST contain that
   count. A short-count answer is NOT acceptable — return NO with the
   actual count vs requested count."
3. Switch to structured output:
   ```json
   {"valid": true|false, "reason": "...", "missing_items": [...]}
   ```
4. Pass the user's original message text in addition to the normalized
   objective — same fix as synthesizer.

---

## Cross-cutting gaps

### A. The "normalized objective" pipeline poison
The classifier rewrites the user message, and **every downstream LLM**
(planner, synthesizer, validator) sees only the rewrite. Three layers
later, what the synthesizer answers no longer matches what the user typed.

**Fix:** Store the raw `original_message_text` in `task.metadata` at task
creation time. Pass BOTH to every downstream prompt. Add a rule:
"Quote-match the user's terminology and language."

### B. No `original_message_text` is preserved past the planner
Currently `task.objective` IS the normalized objective. The original
message is in `inbound.text` at intake time but discarded once the task
is persisted. This needs a schema field.

**Fix:** Add `original_message_text` to `TaskRecord.metadata` at creation.

### C. Language consistency
A Turkish user gets:
1. Turkish message → 2. English-normalized objective → 3. English plan →
4. Tool returns page text (Turkish content) → 5. Synthesizer reads
English objective + Turkish content → confused output.

**Fix:** Detect language in the classifier, pass `user_language` through
the task, and instruct every downstream prompt to respond in that
language.

### D. Capability enum is the #1 retry cause
The Capability enum has 18+ values. Local LLMs invent neighbors
(`browser.read`, `web.open`, `file.read`). The enum is only seen by the
LLM in error messages. Bake the list into the planner system prompt.

### E. Memory context is on the wrong side of the planner
Today: classifier sees memory (and is told to ignore it for web tasks);
planner sees memory verbatim. But planner mostly doesn't need memory —
it needs the **last similar task's plan + outcome**. Better: store
"recent successful plans by route" and feed those as few-shot examples
when planning.

### F. No cost / token tracking
None of the LLM call sites record token usage in `task.metadata`. Hard
to know which model + which call site is the cost driver.

**Fix:** Have `OpenAICompatibleProvider._chat` capture `usage` from each
response and the worker stamp it onto the task per stage:
`metadata.tokens.planner`, `.synthesizer`, `.validator`, etc.

### G. Retry counters are split and uncoordinated
- `replan_count` (max 2) for synth/validator failures
- `evaluator_repair_count` (max 2) for tool failures
- `fulfillment_retry_count` (max 2) for structural gaps
- `retry_count` for transient errors

A task can quietly burn through 6+ replans across counters and still
fail. Need one "intelligent retry budget" with a single ceiling and a
log of every attempt + reason.

---

## Prioritized Recommendations (do these first)

| # | Change | Impact | Effort |
|---|--------|--------|--------|
| 1 | Stop normalizing the objective in the classifier (preserve original message) | HIGH — fixes 5-episode bug class | Low |
| 2 | Bake Capability enum values into planner system prompt | HIGH — fixes most planner JSON failures | Low |
| 3 | Add "DO NOT add success_criteria/validation/notes to step" rule | HIGH — second-most planner failure | Low |
| 4 | Pass `user_language` through pipeline; pin in synth + validator | MEDIUM — Turkish/Turkish drift | Low |
| 5 | Lock down the non-task responder (no "I'm fetching..." hallucinations) | HIGH — false "task done" replies | Low |
| 6 | Add one few-shot example to synthesizer + validator | MEDIUM — local model quality | Low |
| 7 | Validator sees raw output snippet; can detect hallucinations | MEDIUM — answer fidelity | Medium |
| 8 | Validator counts items for "first N" style requests | MEDIUM — common case | Low |
| 9 | Pass full validation error (not truncated) to retry prompt | LOW — already 3 retries | Low |
| 10 | Unify retry counters into one budget | MEDIUM — debuggability | Medium |
| 11 | Reconcile code.interpreter stdlib-only vs planner "pandas allowed" | LOW — edge case | Low |
| 12 | Tool registry context: priority recipes block first | MEDIUM — planner quality | Medium |
