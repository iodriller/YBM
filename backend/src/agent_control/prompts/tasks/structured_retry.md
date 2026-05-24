${original_prompt}

---

PREVIOUS ATTEMPT FAILED with the following validation errors:
${error}

You MUST fix every error above. Pay attention to the EXACT allowed values listed in the errors (e.g. "Input should be 'browser.open', 'browser.control', ..." means you must use one of those exact strings).

Common mistakes to avoid:
- Do not invent capability names. Only use values from the enum list shown in the error.
- Do not use snake_case for capability values; use the exact format shown in the errors.
- Each step's required_capabilities is a list of strings from the allowed enum.

Return ONLY the corrected JSON object. No explanation, no markdown, no code fence.