# Prompt Templates

Prompt text lives here instead of Python modules.

## Categories

- `base/`: reusable system prompts and small fixed prompts.
- `tasks/`: user-message templates that combine runtime data with a base prompt.
- `tools/`: prompts sent to external tools such as Copilot or local coding assistants.

Python code should load these files through `agent_control.prompts.render_prompt()` or
`agent_control.prompts.prompt_text()` and keep only routing, validation, and variable
binding logic in code.
