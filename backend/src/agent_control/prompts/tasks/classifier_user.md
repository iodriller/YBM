Classify this inbound message.

Channel: ${channel}
Kind: ${kind}
Sender: ${sender_id}
Chat: ${chat_id}
Text:
${text}

Return:
- is_task true only if it should spawn a persisted task.
- task_type as one of the allowed enum values.
- normalized_objective as the concise work objective when is_task is true.
- reason explaining the decision.
