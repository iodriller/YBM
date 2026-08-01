---
name: Repo Quick Review
description: Get oriented in an unfamiliar local code repository - structure, purpose, and obvious issues.
version: '1'
tools:
- filesystem.manage
- code.interpreter
---

When asked to review, explore, or get oriented in a local code repository:

1. Use `filesystem.manage`'s `inspect_folder`/`collect_folder_snapshot` to see the real directory
   structure before saying anything about what the project is - don't infer from the folder name
   alone.
2. Read the README and any top-level config files (`package.json`, `pyproject.toml`,
   `Cargo.toml`, and similar) with `read_file` to establish what the project actually is, what
   language/framework it uses, and how it's meant to be run.
3. Summarize in this order: what the project does (from its own README, not assumed), how it's
   structured (main directories and their apparent purpose), how to run/build/test it (if stated
   anywhere), and anything that stands out as worth a second look - no test directory, no
   README, dependencies that look outdated, secrets that look like they're committed.
4. If `code.interpreter` is available and the review calls for it (checking whether tests
   actually pass, counting lines of code, listing dependencies programmatically), use it rather
   than eyeballing an estimate - but a directory listing alone doesn't need a script.
5. This is meant to be a fast orientation, not an exhaustive audit - flag what's worth a deeper
   look rather than trying to read every file.
