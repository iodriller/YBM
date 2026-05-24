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
- Fill intent with a route, operation, objective, reasoning, and any extracted fields when the message is actionable.
- For non-task status/help/conversation, use intent.route=status or conversation.
- Use null for unknown optional fields; do not invent local paths, URLs, fields, or providers.

CRITICAL — do NOT fabricate filesystem paths:
- When the user says "on my desktop" / "in Documents" / "from Downloads", do NOT
  construct a path like `C:\Users\me\Desktop\foo.pdf`. You do NOT know the user's
  actual username. Set `folder_path` to the alias `"desktop"` / `"documents"` /
  `"downloads"` and `file_path` to just the filename (e.g. `"resume.pdf"`).
- Never put placeholder usernames like `me`, `user`, `username`, `<user>`, `{user}`
  in any path field. If you don't know the absolute path, leave the field null.
- The downstream filesystem adapter understands these aliases and resolves them to
  the real user's home — your job is to faithfully extract the user's intent, not
  to invent literal absolute paths.

CRITICAL — task_type MUST be one of these exact strings (no others allowed):
- "development"        (writing/building code, scripts, web apps)
- "configuration"      (changing settings, profiles, configs)
- "admin_control"      (administrative bot/system control)
- "desktop_observation"(screenshots, viewing the screen)
- "question"           (chat, capability questions, generic Q&A)
- "status_request"     (asking about running/scheduled tasks)
- "other"              (anything else — USE THIS FOR BROWSER, FILESYSTEM, AUTOMATION, SCRAPING)

Do NOT invent values like "web_scraping", "web_task", "browser", "data_extraction", etc.
For browser/web/file/desktop-control tasks, use task_type="other" and put the specific
route in intent.route (e.g. "browser.open", "filesystem.manage", "computer.use").
