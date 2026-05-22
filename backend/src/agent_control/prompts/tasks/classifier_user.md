Classify this inbound message and return a complete JSON object.

Channel: ${channel}
Kind: ${kind}
Sender: ${sender_id}
Chat: ${chat_id}
Text:
${text}

Required behavior:
- Decide whether this should spawn a persisted task.
- Fill task_type, normalized_objective, confidence, and reason.
- Fill intent with a route, operation, objective, reasoning, and any extracted fields when the message is actionable.
- For non-task status/help/conversation, use intent.route=status or conversation.
- Use null for unknown optional fields; do not invent local paths, URLs, fields, or providers.
