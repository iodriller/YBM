Objective:
${objective}

Step number: ${step_number}

Observation JSON:
${observation}

Return JSON with this shape:

{
  "completed": false,
  "summary": "brief reason",
  "action": {
    "type": "wait"
  }
}

Allowed action types:
- click: requires x, y
- double_click: requires x, y
- type: requires text
- hotkey: requires keys list
- scroll: requires clicks, optional x, y
- drag: requires x, y, to_x, to_y, optional duration_seconds
- wait: optional seconds
- focus_window: requires title_contains
- open_path: requires path
- launch_app: requires app

If the objective is done, return completed=true and omit action or set action to {"type":"wait"}.
