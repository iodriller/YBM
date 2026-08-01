---
name: Receipt & Invoice Extraction
description: Pull vendor, date, line items, and total out of a receipt or invoice PDF.
version: '1'
tools:
- document.manage
- code.interpreter
---

When asked to extract data from a receipt, invoice, or bill:

1. Read the source document with `document.manage` - don't infer values that aren't actually
   present in the extracted text.
2. Extract, in this order of importance, and mark anything not found as "not found" rather than
   guessing:
   - Vendor / merchant name
   - Date of the transaction
   - Total amount (and currency, if not obviously the user's default)
   - Line items, if individually itemized (description, quantity, unit price)
   - Tax amount, if broken out separately
   - Invoice/receipt number, if present
3. If the user wants this in a structured format (CSV, JSON, a spreadsheet row), use
   `code.interpreter` to actually build that output file rather than describing the structure in
   prose - a real file is what makes this useful for bookkeeping.
4. Numbers matter more than prose here. Double-check that a total you report actually matches a
   number that appears in the source text, character for character - don't let arithmetic errors
   or OCR noise turn into a wrong total in the output.
5. If the total doesn't obviously reconcile with the sum of line items plus tax, say so - that's
   useful information, not a reason to silently pick one number over the other.
