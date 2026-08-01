---
name: Document Summary
description: Read a PDF, Word doc, or text file and produce a short, accurate summary.
version: '1'
tools:
- document.manage
- filesystem.manage
---

When asked to summarize, explain, or extract key points from a document:

1. Locate the file - if a path was given, use it directly; if only a name or description was
   given, search first rather than assuming a location.
2. Use `document.manage` to extract the actual text content. Never summarize from a filename or
   file extension alone - if extraction fails or comes back empty, say so plainly instead of
   guessing at content that wasn't actually read.
3. Produce a summary with this shape, not a single undifferentiated paragraph:
   - One-sentence description of what the document is.
   - 3-6 bullet points of the substantive content (facts, decisions, numbers - not filler).
   - Anything that looks like an action item, deadline, or number worth double-checking, called
     out explicitly.
4. Quote short exact phrases for anything specific (a total, a date, a name) rather than
   paraphrasing numbers - paraphrased numbers are exactly the kind of thing that quietly drifts
   wrong.
5. If the document is long, say how much of it you actually processed (page count is on the
   `document.manage` output) rather than implying full coverage of something only partially read.
