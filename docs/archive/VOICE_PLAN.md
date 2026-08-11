# How YBM talks: a proposal

You said the responses read too much like a computer. They do. Below is what
YBM actually said - pulled from the recorded scenario fixtures, which are real
model output from real runs, not invented examples - then the patterns behind
it, and a fix.

---

## 1. What YBM says today

Verbatim, with the problem named:

> **"The expenses have been recorded in 'expenses.csv', and the total has been
> calculated and saved in 'expense-summary.json' with a total of 0. The task is
> complete."**

Passive throughout. Announces its own internal state at the end. And it reports
**a total of 0** without noticing that a sum of zero over a file of expenses is
almost certainly wrong - the one thing a person would want flagged.

> **"The file 'agent-control-sample.pdf' has been successfully delivered to the
> Telegram chat scenario_test_chat."**

"has been successfully delivered" is three words doing one word's work, and
`scenario_test_chat` is an internal identifier the user never chose and cannot
read.

> **"The contents of the .txt file located at
> `<scratch>/file_find_and_read/resume-notes.txt` are: …"**

Twelve words of preamble reciting a path before the answer starts. The person
asked what was in the file.

> **"Fixed the path escaping issue by using raw strings and ensuring proper
> file writing without Unicode errors."**

This is YBM narrating its own debugging. The user never saw the bug, never
asked about escaping, and has no use for the fix.

> **"Created a file named expenses.csv with specified content."**

"with specified content" is filler - it says nothing the user did not just ask
for.

And, to be fair, this one is already right:

> **"The 20th Fibonacci number is 6765."**

Answer first, nothing else. That is the target for everything.

### Errors are worse

`error_text.py::describe_exception` returns `TypeName: message`, so what
reaches a person is:

```
operator_decide_failed: HTTPStatusError: Server error '502 Bad Gateway'
```

That is a correct log line. As something a user reads, it names a Python class,
an internal stage, and an HTTP status, and offers nothing to do about any of
them.

---

## 2. The patterns

| | Pattern | Tell |
|---|---|---|
| **P1** | Status narration | "The task is complete", "successfully" |
| **P2** | Passive voice | "has been recorded", "have been calculated" |
| **P3** | Path recital | Full absolute path before the answer begins |
| **P4** | Process narration | Describing its own retries, fixes, and code changes |
| **P5** | Filler | "with specified content", "with the following content" |
| **P6** | Unflagged nonsense | Reporting a total of 0 as if it were fine |
| **P7** | Leaked identifiers | `scenario_test_chat`, scratch roots, profile names |
| **P8** | Exception classes as prose | `HTTPStatusError: …` shown to a person |

P6 is the serious one. The others are style; that one is **a correctness
signal being thrown away**. An assistant that reports an implausible number
without comment is worse than one that phrases things awkwardly.

---

## 3. The voice

One rule above the others: **say what happened to the user's request, not what
happened inside the program.**

Six rules, in priority order:

1. **Answer first.** The first sentence is the result. Path, method, and
   caveats come after, if at all.
2. **Active voice, and YBM is the subject.** "I saved the total to
   `expense-summary.json`" - not "the total has been saved".
3. **Never narrate the machinery.** Retries, fixed bugs, chosen approaches, and
   internal stage names are invisible unless the user is blocked by them.
4. **Flag results that look wrong.** A zero total, an empty list, a file with
   no matches: say so plainly. *"That comes to 0, which looks off - the amount
   column may not be where I expected."*
5. **Name things the way the user named them.** Their filename, their folder,
   their words. If YBM had to resolve a path, mention it once at the end.
6. **No status theatre.** "The task is complete" is what the UI's status field
   is for.

### The rewrites

| Now | Instead |
|---|---|
| "The expenses have been recorded in 'expenses.csv', and the total has been calculated and saved in 'expense-summary.json' with a total of 0. The task is complete." | "The total comes to **0**, which looks wrong for a file of expenses - the amounts may be under a different column name. I saved it to `expense-summary.json` anyway so you can check." |
| "The file 'agent-control-sample.pdf' has been successfully delivered to the Telegram chat scenario_test_chat." | "Sent `agent-control-sample.pdf` to your Telegram." |
| "The contents of the .txt file located at `<path>` are: …" | "Your resume notes say: …" |
| "Created a file named expenses.csv with specified content." | "Created `expenses.csv`." |
| "Fixed the path escaping issue by using raw strings…" | *(say nothing - the user never saw the bug)* |

### Errors: what happened, then what to do

The [standard shape](https://www.nngroup.com/articles/error-message-guidelines/)
is one plain sentence on the problem and one on the way forward, with no
jargon and no blame. Concretely:

| Now | Instead |
|---|---|
| `operator_decide_failed: HTTPStatusError: Server error '502'` | "The model server stopped responding partway through. It's usually back within a minute - try again, or switch models in Settings." |
| `ValueError: base_url is required for OpenAI-compatible LLM provider` | "That provider needs a base URL. Add one in Settings → Model." |
| `allowlist_empty` | "I don't recognise this Telegram account, so I ignored the message. Add yourself under Settings → Telegram." |
| `Anthropic request failed with HTTP 401` | "Anthropic rejected the API key. Paste it again in Settings → Model." |

Same rule as the replies: **name what the person can do.** If there is nothing
they can do, say that too - *"this one's on my side"* beats a stack of nouns.

The diagnostic string does not disappear. It moves to where it belongs: the
task trace and the log, one click away, unchanged.

---

## 4. Where the fix goes

Four places, in the order I would do them.

**4a. A shared voice section in the prompts.** One `base/voice.md` included by
`concierge_system.md`, `operator_system.md`, and `auditor_system.md`, carrying
the six rules and two before/after pairs. Positive examples of the wanted shape
work better than a list of prohibitions.

**4b. A user-facing error layer.** `error_text.py` keeps `describe_exception`
for logs and gains `explain_for_user(exc) -> str`: a table from known failure
shapes (HTTP 401/429/5xx, connection refused, timeout, empty allowlist, missing
base URL, unknown model) to a sentence and a next step, with a plain fallback
for anything unmatched. Every channel and the console read that one.

**4c. Make the Auditor own plausibility.** It already checks whether the
objective was met. Extend it to ask *does this result look right?* - a zero
sum, an empty result set, a file that turned out to be 0 bytes - and require
the final answer to say so when the answer is no. This is where P6 gets fixed,
and it is worth more than all the style work.

**4d. A style test.** A test over the recorded fixtures that fails on the known
tells - `"The task is complete"`, `"has been successfully"`, `"with specified
content"`, a bare exception class name in a user-facing field. Cheap, and it
stops the patterns coming back the next time a prompt changes.

---

## 5. The cost, stated plainly

**4a changes prompt content, which invalidates the recorded fixtures.** This is
the one kind of change `_reindex` cannot absorb - it recomputes keys from
stored prompts, so a *normalizer* change is free, but changing the prompt text
itself means the recordings no longer match.

That is a real bill: re-recording is live LLM spend, and you have asked me to
keep that to one or two fixtures at a time rather than all sixteen.

So I would sequence it to spend as little as possible:

1. **4b and 4d first - they cost nothing.** The error layer is pure Python and
   the style test runs against fixtures as they are. Together they fix the
   worst-reading surface (errors) with zero re-recording.
2. **4a next**, re-recording **two** fixtures to confirm the voice section
   lands, and leaving the rest until they are touched for other reasons.
3. **4c last**, since it changes Auditor behaviour and deserves its own
   recording pass.

## 6. What I would want from you

- **Does the voice sound right?** The rewrites in section 3 are the actual
  proposal; everything else is plumbing to enforce them.
- **How much personality?** The rewrites above are plain and warm but not
  chatty. YBM could be drier or friendlier, and that is a product decision, not
  a technical one.
- **Which two fixtures** should carry the first re-record when 4a lands.

## Not verified

- The bad examples are real recorded output, but from scenario fixtures rather
  than a live session - I have not sent a message through a configured model.
- No prompt has been changed yet; this is a proposal.
