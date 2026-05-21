The previous Copilot response did not produce materializable local app files.

Do not create, edit, or execute files in this retry. Return complete file contents only.

Rules:
- Return fenced code blocks with filename metadata for every required static app file.
- Include at least `index.html`.
- Include `styles.css` and `script.js` when the HTML references them.
- Do not include prose before or between file blocks.
- Do not say files were created.

Use this exact block style:

```html filename=index.html
...
```

```css filename=styles.css
...
```

```javascript filename=script.js
...
```

Original request:
${prompt}

Previous response:
${output}
