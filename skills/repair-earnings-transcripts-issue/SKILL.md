---
name: repair-earnings-transcripts-issue
description: Repair one adjudicated earnings-transcripts (ET) issue from a repository issue card or a given run reference. Use when a feedback observation names an ET-owned cause, when a transcript fetch, period attribution, usage receipt or content reference deviates from its contract, or when a transcript-tool deviation must be reproduced, attributed, fixed with a RED-first regression and re-verified offline.
---

# Repair an earnings-transcripts issue

One skill invocation handles **one** root cause. A cause is the
`issue_key` of an adjudicated deviation: the same `issue_key` on different
companies, periods or runs is the same cause and stays on one card.

This skill repairs only what this repository owns. It consumes only this
repository's controlled replay entry and the non-executable evidence
references of an issue card. It never contacts a provider, never spends
provider quota, never translates, and never writes outside this repository.

## What this repository owns

| Owned here | Not owned here |
|---|---|
| The transcript tool boundary, its result schemas, the provider adapters and their period/identity checks, the native usage receipt, the local originals | Deciding the company pool, calling ET from filing-fetch, storing the transcript in company-wiki, the research judgement of a summary |

A deviation whose responsible layer is another repository is not repaired
here. It is reported to ROOT with its public input, its public output and its
evidence reference.

## Inputs

Either of:

- an issue card in `docs/plans/feedback-loop-2026-11-17/issues/`; or
- a run reference: a `feedback-observation/1` record with its `evidence_ref`.

Read the card's sections first. They hold the expected and actual result
words, the impact, the original occurrence and its version, the runnable
minimal input, the ownership, the proven and unproven root cause, the fix and
its regression case, and the consumer re-verification state.

## Procedure

### 1. Fix the facts before touching code

State, in the card's own terms, and verify each one:

1. **Expected** — the contract word the check compares against.
2. **Actual** — the result word this run produced.
3. **Version** — the code and configuration version of the observed run.
   The observation's `version` field names the projection version; the
   reviewed source version belongs to the handoff, not to the observation.
4. **Real input source** — where the input genuinely came from: a live
   provider response, a recorded original, a fixed fixture, or a fault
   injection. A fixture is not a live fact and must never be reported as one.

If any of the four cannot be established, the issue is **not yet
actionable**: record what is missing and stop. Do not guess a cause.

### 2. Reproduce from the public entry

Reproduce through the public entry, not through an internal helper:

```
python tools/replay_feedback_case.py --list
python tools/replay_feedback_case.py --case <named fixture> --out <sidecar>.jsonl
```

The replay never downloads: the HTTP transport is the in-process fake session
or the private worker launcher seam. The report states the native facts, the
comparison with the existing golden (read, never rewritten) and the new
observations.

Find the **first** place where the run departs from the contract, not the
first place where the symptom appears.

### 3. Look for sibling entries and legal counterparts

- Sibling entries: every other place in this repository that performs the
  same step (a second CLI path, a second provider adapter, a second
  publication site). A fix that patches one path and leaves its siblings
  inconsistent is not a fix.
- Legal counterparts: the runs that look similar but are correct (a refusal
  that is right, a limit that was reached, an unknown that is honestly
  unknown). Use them to keep the repair from turning a legal outcome into a
  defect, and to detect over-fitting.

### 4. Attribute the cause

Name exactly one responsible layer and say why the others are not:

| Layer | Meaning |
|---|---|
| Business error | The product logic is wrong. Fix it here. |
| Test fixture | The fixture, clock or injected transport is wrong. Fix the fixture; never bend the product to fit it. |
| Deployment environment | The host, permissions, clock or path broke it. Record the recovery condition; the product stays as it is. |
| External condition | The provider, account or network refused. Record it as a fact of the environment; do not repair the product for it. |

### 5. Write the RED first

From the old error and the legal counterpart, write the failing test before
the fix. The RED must:

- run through the public entry;
- fail for the recorded reason, not for an import or a setup error;
- pass unchanged after the fix.

Record the RED output as a receipt. A repair without a recorded RED is not
accepted.

### 6. Fix the common responsible layer

Repair the layer that owns the cause, not the symptom's first appearance.
Keep the public contract: the result schema, the stdout/stderr split, the
exit code, the native receipt's shape and the retry behaviour stay as they
are unless the card explicitly says the contract itself is wrong.

Record the fix and its test in the canonical plan of this repository
(`task_plan.md` at the root); `progress.md` and `findings.md` are history and
are not status entries.

### 7. Targeted GREEN, then re-verify the original scenario

- Run the new test alone, then the focused combination, then the wider
  regression.
- Re-run the original scenario through the controlled replay entry and
  confirm that the observation of that cause is now `expected`.
- A repair is only complete when the original scenario no longer deviates.

### 8. Close the loop

Record in the card:

- the fix version (the reviewed source SHA, not the SHA of the receipt that
  mentions it);
- the regression case (the exact command and its result);
- the consumer re-verification state: `pending` for the consumer that found
  the cause, `verified` once that consumer re-ran the original scenario on
  the fixed version;
- the remaining items that belong to another repository.

Re-import the observations into the card:

```
python tools/feedback_issue_cards.py --import <sidecar>.jsonl
```

Importing the same observation again adds nothing.

## Stop conditions

- Same version, same input, same failure → do **not** run the loop again.
  Report that the repair attempt did not move the failure.
- The cause is not adjudicated (`disposition` is `expected` or `unknown`) →
  this is not an issue to repair. A legal refusal and an honestly unknown
  usage are both normal outcomes.
- The reproduction needs a live provider, a paid account or a credential →
  out of scope for this offline skill. Record what is missing and the
  recovery condition.
- The responsible layer is another repository → report to ROOT with the
  public input, the public output and the evidence reference; do not edit the
  other repository.

## Boundaries

- The public wire, the result schema, the native receipt shape, the exit
  code and the "no retries, no translation, no file writes" promise of the
  JSON boundary are frozen. A repair states its effect on them explicitly.
- Originals and formal run artefacts stay with their owner. The projection
  writes only its own sidecar and its own details file.
- A sidecar write that fails never changes the business outcome. Report the
  incomplete diagnostic; keep the exit code, the result, the cost and the
  retry behaviour.
