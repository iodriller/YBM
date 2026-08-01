---
name: Meeting Notes Cleanup
description: Turn raw, messy meeting notes into a structured summary with clear action items.
version: '1'
tools:
- document.manage
- memory.manage
---

When given raw meeting notes (pasted text, a rough transcript, or a notes file) to clean up:

1. If the notes are in a file rather than pasted directly, read it with `document.manage` first -
   don't work from a filename or a partial paste when the full source is available.
2. Restructure into exactly these sections, skipping any that genuinely have nothing in them
   rather than padding with "N/A":
   - **Summary** - two or three sentences on what the meeting was actually about.
   - **Decisions** - anything that was actually decided, stated as a fact, not a suggestion.
   - **Action items** - who, what, and by when if a deadline was mentioned. If no owner was
     stated, say "owner not specified" rather than assigning one that wasn't actually said.
   - **Open questions** - things raised but not resolved.
3. Don't invent action items, owners, or deadlines that weren't in the source notes - cleaning up
   structure is the job here, not filling gaps with plausible-sounding guesses.
4. If something in the notes is ambiguous or contradictory, flag it in the summary rather than
   silently picking one interpretation.
5. If asked to remember a specific recurring decision or preference from the notes for future
   tasks (a standing policy, a project's regular attendees), use `memory.manage` to save it rather
   than letting it only exist in this one cleaned-up document.
