Objective (normalized): ${objective}

User's original message (preserve language and exact terms): ${original_message}

Remembered response requirements and conversation context:
${response_context}

The raw output must still ground every factual claim. When it is sufficient,
apply explicit remembered response-format preferences unless the user's current
message overrides them; those preferences are answer requirements, not facts
to summarize.

Raw tool output to audit:
${raw_output}

Apply the sufficiency check first (count, topic, section). If sufficient, return
the focused answer in the required format. If not, return INSUFFICIENT: <reason>.
