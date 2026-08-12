# Roadmap

Status: proposal. Nothing here is built yet.

Written against a comparison with [OpenClaw](https://docs.openclaw.ai/concepts/memory),
which is genuinely ahead on memory, plugin ecosystem, multi-agent, and
automation. The comparison is useful and the gaps are real. The response to it
should not be feature parity.

## The frame

A feature-by-feature score ("memory 3/10 vs 10/10") measures the wrong thing for
this product. OpenClaw is a larger project optimising for breadth: more plugins,
more agents, more memory engines. YBM cannot win that race and does not need to,
because it is answering a different question.

YBM's question is: **can I let something touch my actual computer?**

Everything YBM already has that OpenClaw does not emphasise - policy before every
tool call, approvals with a blast radius, a receipt naming every file touched,
a full trace - is an answer to that question. That is the asset. The roadmap
below spends effort on gaps *that block that answer*, and declines gaps that are
only competitive.

So the spine is:

**Plan -> Approve -> Execute -> Verify -> Receipt**

Every item below is judged by whether it makes one of those five stages
stronger, or makes one of the three target jobs actually work.

## The three jobs

These are the only things that have to be excellent. Everything else is
secondary until these are.

1. **Safely organise and change files on my actual computer.**
2. **Do browser and desktop work for me, asking before anything consequential.**
3. **Run a long multi-step computer task and prove afterwards exactly what
   happened.**

Where the spine is currently weak for each:

| Job | Plan | Approve | Execute | Verify | Receipt |
|---|---|---|---|---|---|
| 1. Files | **weak** - no previewable plan before a write | good | good | partial | good |
| 2. Browser/desktop | **weak** | good | good | **weak** - no visual evidence | partial |
| 3. Long tasks | **weak** | good | partial - no durable resume | partial | good |

The pattern is clear: **Plan is the weakest stage across all three**, and
Verify is second. Neither is a memory problem. That ordering drives the
sequencing at the end.

---

## 1. Plan: make the plan a reviewable artifact

The single highest-value change, and it is not on the comparison list.

Today the Operator decides one step at a time and the user sees the result
after the fact. Approvals gate individual tool calls, so a 30-file
reorganisation is either one coarse approval or thirty interruptions. Neither
is "I saw what it was going to do and said yes".

**Build:** a `plan` artifact for multi-step or multi-file work - an ordered list
of intended actions with their blast radius, rendered in chat before execution,
approvable as a unit.

- `filesystem.manage` already has `organize_plan` and `rename_plan` producing a
  manifest before `apply_manifest`. Generalise that shape rather than invent one.
- One approval for a reviewed plan, with the policy engine still gating each
  call at execution time. A plan approval is not a capability grant.
- Deviation from an approved plan re-prompts. This is the safety property that
  makes a single approval acceptable.

**Success:** "Organise my Downloads folder" shows the 128 moves grouped by
destination, one Approve, then it runs - and if step 40 wants to do something
that was not in the plan, it stops and asks.

Serves job 1 and 3 directly, job 2 partially.

## 2. Verify: evidence a human can check

The Auditor already grounds the final answer in tool output, and receipts list
what was touched. Two gaps:

- **Browser and desktop work leaves no visual record.** Screenshots exist as a
  capability but are not automatically attached as evidence at consequential
  steps. For job 2 this is the whole proof.
- **Postconditions are inferred, not asserted.** `fulfillment.py` infers intent
  from wording. A plan (item 1) can instead carry explicit, checkable
  postconditions: "34 files now under Downloads/Documents".

**Build:** before/after evidence capture on approved consequential steps, and
postcondition checks derived from the plan rather than from keyword matching.

**Success:** the receipt for a browser task shows what the screen looked like at
the moment of the consequential click, and a file task states the postcondition
it checked and whether it held.

## 3. Long-running tasks: durable resume

Job 3 says "long multi-step task". Today a task is bounded by
`task_budget_seconds` and a step budget, and a crashed worker releases the claim
rather than resuming mid-plan.

**Build:** checkpointed task state so a multi-hour task survives a restart, with
the trace showing the resume rather than hiding it. `scheduler` and the
supervisor already exist; this is persistence of the operator loop's position,
not a new orchestrator.

**Non-goal:** OpenClaw's Task Flow / standing orders / heartbeat monitoring as a
category. Build only the durability that job 3 needs.

---

## 4. Memory: fix retrieval, not the feature count

The assessment is fair. `memory.manage` is `remember` / `list` / `forget` over a
`memory_facts` table, and `knowledge.search` is deliberately keyword-overlap.
There is no semantic retrieval, no consolidation, and no automatic capture.

What OpenClaw has, per its docs: `USER.md`, `MEMORY.md`, dated daily notes,
hybrid semantic+keyword `memory_search`, and "dreaming" - background promotion
of short-term memories into durable ones behind thresholded, **taint-gated**
review, written to a `DREAMS.md` a human can inspect.

That last property is the one worth copying, and YBM is well placed for it:
**memories are already tagged with the task that produced them.** Provenance is
the hard part and it already exists.

### What to build

**a. Hybrid retrieval, local-only.** Keyword matching alone misses "what did I
decide about the invoice folder" when the note says "billing directory".

- Storage: **`sqlite-vec`** in the existing `.agent_control/agent_control.db`.
  It is a SQLite extension with prebuilt wheels for Windows, macOS, and Linux,
  so it adds no separate datastore, no service, and no daemon - matching how
  every other piece of YBM state is stored.
- Embeddings, in priority order, all local:
  1. **Ollama** (`nomic-embed-text`, ~274MB) when an Ollama endpoint is already
     configured - which it often is, since it is a supported model provider.
  2. **A bundled ONNX embedder** for machines with no Ollama.
  3. **Keyword-only**, the current behaviour, as the floor.
- The floor matters: memory must never *require* an embedding provider. Degrade,
  do not fail. Same principle as `is_headless_runtime`.
- Ship behind an optional extra so the default install does not grow.

**b. Automatic capture with provenance.** Requiring the user to say "remember
this" means the memory is empty exactly when it would be useful. Capture
candidates automatically at task completion, tagged with the task id, and keep
them in a short-term tier that is searchable but not injected into prompts.

**c. Consolidation, gated by the provenance that already exists.** Promote
short-term candidates to durable only when they clear thresholds, and **never**
promote anything derived from untrusted input - a web page, an inbound message,
a file YBM did not write. This is prompt-injection defence, not tidiness: a
memory promoted from a malicious web page is a persistent instruction the user
never gave.

Write promotions to a reviewable log, and surface it in the console's existing
Memory page. That is OpenClaw's `DREAMS.md` idea expressed as something the
console can already render.

### What not to build

- **A Memory Wiki.** Interesting, and nothing to do with the three jobs.
- **Multiple memory engines / pluggable backends.** One good local path beats
  five configurable ones for a product whose promise is that it works on your
  machine without a decision tree.
- **Cloud embedding providers.** They would put the user's notes on someone
  else's server, which contradicts the positioning. Local only.

**Success:** ask "where do I keep invoices" after a month of use and get the
right answer, with a citation to the task that learned it, having never run a
`remember` command by hand.

---

## 5. Ecosystem: safe self-extension, not a plugin store

The right call is already identified. `adapter.factory` can `assess`,
`scaffold`, `sandbox_execute_once`, `test_connector`, and
`promote_after_approval`. That is a genuinely different answer to extensibility
than "install a plugin", and it is the same spine as everything else:

> I do not know how to operate your application. I generated a connector.
> Here is exactly what it will access. The tests pass. Install it?

**Build:** make that flow first-class rather than a tool the Operator may
happen to call.

- A visible path in the console: proposed connector, its declared capabilities,
  its blast radius, its sandbox test result, and Approve/Reject.
- Generated adapters carry a manifest of what they may touch, enforced by the
  existing policy engine. A generated connector must not be able to grant
  itself capabilities.
- A local, inspectable directory of connectors the user has accepted - not a
  hosted registry.

**Non-goal:** ClawHub. A distribution network is a network-effects game YBM will
not win, and a curated remote registry is a supply-chain surface that
contradicts the positioning. Self-extension is the differentiated answer.

**Success:** a user with an obscure internal tool gets a working, policy-bound
connector without writing code, and can read exactly what it does before saying
yes.

---

## 6. Multi-agent: deliberately deferred

`delegate` is a bounded isolated inner loop, and that is the right primitive for
now. Gateways, swarms, agent-to-agent coordination, and per-agent channel
bindings are feature competition against a larger project, and none of them
makes the three jobs better.

Revisit only if a real job needs it - for example, one long task genuinely
needing two different workspaces at once.

---

## Sequencing

Ordered by "how much does this unblock the three jobs", not by gap size.

| # | Item | Serves | Size |
|---|---|---|---|
| 1 | Plan as a reviewable, approvable artifact | Jobs 1, 3 | L |
| 2 | Positioning: README around the five stages | All | S |
| 3 | Visual evidence on consequential steps | Job 2 | M |
| 4 | Postconditions from the plan, not keywords | Jobs 1, 3 | M |
| 5 | Hybrid local memory retrieval (sqlite-vec + Ollama) | All | L |
| 6 | Automatic capture with provenance | All | M |
| 7 | Taint-gated consolidation + reviewable log | All | M |
| 8 | Durable resume for long tasks | Job 3 | L |
| 9 | Self-extension as a first-class console flow | Breadth | L |

Items 1-4 are the spine. Items 5-7 are memory, sequenced after the spine because
a memory that recalls perfectly does not help a user who cannot see what is about
to happen to their files. Item 8 completes job 3. Item 9 is the differentiated
answer to ecosystem.

## Honest notes

- **This is a big list for a single maintainer.** Items 1 and 2 alone would
  change how the product reads. Nothing here needs to happen at once.
- **Memory ranks fifth, not first.** That is a deliberate disagreement with the
  input assessment. The gap is real, but Plan is weaker and it is upstream of
  everything: an approved plan is what postconditions, evidence, and receipts
  are all checked against.
- **`sqlite-vec` and embeddings add dependencies.** They must be optional
  extras with a keyword-only floor, or the "works on a bare machine" property
  that was just verified is lost.
- **Nothing here is verified by running code.** These are design proposals from
  reading the current implementation.
