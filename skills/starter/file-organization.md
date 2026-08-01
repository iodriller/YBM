---
name: File Organization
description: Sort a messy folder into subfolders by file type, without deleting anything.
version: '1'
tools:
- filesystem.manage
---

When asked to organize, clean up, or sort a folder:

1. Use `filesystem.manage`'s `inspect_folder` or `collect_folder_snapshot` operation on the target
   folder first - never guess at what's in there.
2. Group files by extension into sensible categories: Images (jpg, png, gif, webp, svg),
   Documents (pdf, doc, docx, txt, md), Spreadsheets (xls, xlsx, csv), Archives (zip, rar, 7z),
   Installers (exe, msi, dmg), Code (py, js, ts, and similar), Other (anything left over).
3. Propose the plan in plain language before moving anything - which files go where, and how many
   per category. This is a `write_text_file`/move-shaped operation (medium-to-high risk in YBM's
   policy engine), so a real approval step is expected and correct; don't try to work around it.
4. Never delete a file. Moving into a subfolder only. If two files would collide (same name in the
   destination), keep both and say so rather than silently overwriting one.
5. Report back exactly what moved where, in the same list-of-changes shape a task receipt would
   show - this skill's whole point is the user trusting what happened without having to check.
