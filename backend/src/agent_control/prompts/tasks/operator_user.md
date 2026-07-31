Decide the next action for this objective. Follow the rules in the system prompt and return JSON only.

## Objective
${objective}

${memory_context}
## What has been tried so far this task
${history}

## Available tools (only use names and operations from this catalog)
${config_context}

## Reminders
- If history is empty, this is the first step - make your best first tool call toward the objective.
- Only `call_tool`, `call_tools_parallel`, `delegate`, `done`, `ask_user`, or `blocked` are valid actions.
- `done` requires `final_answer`; `ask_user` requires `question`; `blocked` requires `reason`;
  `call_tool` requires `tool_name`, `tool_input`, and `risk_level`; `call_tools_parallel` requires
  `parallel_calls` (2+ items); `delegate` requires `delegate_objective`.
