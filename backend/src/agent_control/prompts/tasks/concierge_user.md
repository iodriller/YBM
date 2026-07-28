Classify this inbound message and, if it isn't a task, reply to it. Follow
the rules in the system prompt and return JSON only.

Channel: ${channel}
Kind: ${kind}
Sender: ${sender_id}
Chat: ${chat_id}

Runtime context (capabilities, recent tasks, conversation memory - may be
sparse on first message):
${context}

Text:
${text}
