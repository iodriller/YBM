Create an execution plan for this objective. Follow the rules in the system prompt and return JSON only.

## Objective
${objective}

${memory_context}
## Available tools (only use names and operations from this catalog)
${config_context}

## Reminders
- Risk: `low` for reads, `high` for writes / browser control, `critical` for desktop UI control.
- For URLs/domains the user explicitly named: use `browser.open` with `operation: "open"` and the URL.
- For "find then read" requests: two steps — `search` then `read_file` with `path: "{{last_entry_path}}"`.
- For files on Desktop/Documents/Downloads: pass the alias (`"desktop"`, etc.) — never invent a literal path.
- Multi-step when needed; one step when sufficient.
