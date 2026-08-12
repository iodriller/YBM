# Roadmap

Three things, in priority order. Everything else is deliberately not on this
list.

The spine every item serves:

**Plan -> Approve -> Execute -> Verify -> Receipt**

And the three jobs that have to be excellent:

1. Safely organise and change files on your actual computer.
2. Do browser and desktop work, asking before anything consequential.
3. Run a long multi-step task and prove afterwards exactly what happened.

---

## 1. Ask once, not thirty times - DONE

**Was:** organising 128 files asked 128 times.

**Cause:** not the approval system. `call_tools_parallel` - the only batching
primitive the operator had - failed any call needing approval with "reissue it
alone via call_tool", pushing the model onto the one-call-one-approval path for
exactly the work where batching matters most.

**Now:** a parallel batch is pre-flighted through the policy engine before
anything runs. If any call needs approval, the user gets one approval listing
every call, and nothing executes until they answer.

Authority did not widen. Each listed call gets its own approval bound
byte-for-byte to its request; the batch decision only cascades to those
children. A call the human never saw still has to ask. Deliberately **not** a
grant, because a grant covers any later call with the same tool and capability,
which is broader than what was reviewed.

Worth remembering: a child approval's `action_payload` **is** the serialized
`ToolCallRequest` that `PolicyEngine._approval_binding` re-validates, and that
model forbids extra keys. Marking children there made every approved call deny
itself. The batch/child link lives on the parent instead.

## 2. Continuity - DONE

**Goal:** if the session is killed, the machine reboots, or the process dies,
the task continues later instead of being lost.

**The blocker is already named in the code.** `worker.py`'s
`ORPHANABLE_STATUSES` comment says a task still RUNNING when a new worker
starts cannot simply be re-claimed, because "re-running from the top could
duplicate side effects (a second Telegram send, a second file write), and
there's no checkpoint to resume from mid-flight." So `reconcile_orphaned_tasks`
currently **fails** those tasks on startup. That is the whole gap.

**The checkpoint mostly exists.** `operator_history` already records every
completed tool call and its result, and it lives in task metadata in SQLite.
Resuming means re-entering the operator loop with that history, not re-running
from the top - the operator can see what is already done.

**The real hazard is the in-flight call**: one dispatched but not yet recorded
when the process died. It may or may not have happened.

**Built:**

- `operator_in_flight` is written before a call is dispatched and cleared once
  its result is recorded, so a dead worker leaves evidence of which call was in
  the air.
- `reconcile_orphaned_tasks` now resumes instead of failing, choosing between
  three cases: nothing in flight (resume), a read in flight (resume, re-running
  it is harmless), a write in flight (ask the user).
- The ambiguous write is never silently retried and never silently skipped.
  The task moves to CLARIFYING with a question naming the tool, because
  retrying can do a thing twice and skipping can leave the job half done.

**Interrupts, already working and left alone:** pause, resume, and cancel are
checked at the top of every operator step, so a long task stops between steps
rather than at the end. Cancelled and paused tasks are not in
`ORPHANABLE_STATUSES`, so a restart never resurrects one.

## 3. Proof

Two concrete things, both about not having to take the agent's word:

- **Screenshots as evidence.** Capture the screen before and after a
  consequential browser or desktop action and attach it to the receipt. Today
  that work leaves no visual record at all, which is most of job 2.
- **Check the goal, do not infer it.** `fulfillment.py` infers success from the
  wording of the request. An approved batch already states what it intends to
  do, so check that instead: "you asked for the folder sorted; 34 files are now
  under Documents; confirmed."

---

## Not doing

- **Memory upgrades.** `remember` / `list` / `forget` with task provenance is
  enough for now. Semantic retrieval buys better recall of facts about the user,
  which none of the three jobs need, and costs a ~274MB embedding model plus a
  new dependency on a product whose selling point is that it runs on a bare
  machine. Revisit only if recall is observed failing in practice.
- **Multi-agent runtimes** (gateways, swarms, agent-to-agent). Feature
  competition; none of it makes the three jobs better. `delegate` stays a
  bounded inner loop.
- **A plugin marketplace.** If extensibility becomes a priority, the
  differentiated answer is the existing `adapter.factory`: "I do not know how to
  operate your application. I generated a connector. Here is exactly what it
  will access. Tests pass. Install it?" A hosted registry is a network-effects
  game and a supply-chain surface.

## Note on surfacing

Plans and batches are surfaced by the model's judgment - the user asked for one,
or the work is large or destructive enough to be worth a glance. **Never by
keyword matching.** `config.py` records that the previous plan path "and its
keyword-driven recovery were deleted, not just defaulted off"; matching on the
word "plan" would rebuild a mistake this repository already made once.
