Create an execution plan for this objective:

${objective}

Configuration/capability context (only use capabilities that are listed as enabled):
${config_context}

Requirements:
- Include concrete tool steps with specific operations and inputs
- For browser tasks with an explicit URL or domain name, always use browser.open with operation "open" and the url field set
- For "find and read" file requests, create two steps: search then read_file
- For complex tasks, chain multiple steps — do not try to do everything in one step
- Set risk_level appropriately: low for reads, high for writes/browser control, critical for desktop control
- Include assumptions, required_capabilities, ordered steps with tool_name and tool_input, success_criteria, and postconditions
