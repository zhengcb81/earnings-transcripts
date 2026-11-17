# ET-FB-002 · transcript-usage-receipt

- issue_key: `transcript-usage-receipt`
- status: verified
- owner: earnings-transcripts
- first_seen: 6883623 (base commit of the FB-ET card)
- evidence_class: confirmed defect, reproduced by execution at the base commit

## Expected and actual

- expected: `usage_known` — every terminal path of a retrieval operation
  publishes exactly one native final usage receipt when `--report-usage` is
  given, so a consumer can reconcile the provider cost of the run.
- actual (before the fix): `usage_missing` — three of the terminal paths
  that reject a request before any provider call published no receipt at all:
  the unparsable request, the oversized request and the discovery request
  that asks for a source payload.
- disposition: deviation (repaired in this card; the repaired code reports
  `usage_known` on the same paths)

## Impact

The business result, the exit code and the provider behaviour were never
affected: those paths make zero provider requests, and the run still exits 0
with one protocol result on stdout. What was missing was the cost fact. A
consumer that reconciles provider spend from the receipt (the filing-fetch
owner reads `earnings-retrieval-usage/1`) saw no receipt on those three
paths and could not distinguish "this run called no provider" from "the tool
stopped before it could report". The structurally identical preflight
rejection path did publish a receipt, so the flag behaved inconsistently
inside one product.

## Occurrences

<!-- occurrences:begin -->
| sample-deviation-usage-receipt | attempt-1 | test-request-001 | usage-accounting | usage_known | usage_missing | deviation | et-case:usage-receipt-suppressed |
<!-- occurrences:end -->

The occurrence above is the **pre-fix publication behaviour re-created** by
suppressing the publication, so the deviation itself is observed by the
projection. The defect itself was first observed by executing the tool at the
base commit, before the projection existed (see the reproduction below).

## Reproduction

Three ways, none of which contacts a provider:

1. The defect at the base commit (the receipt is absent on all three
   sibling terminal paths; the structurally identical preflight control
   does publish one):

   ```
   echo "not json" | python -B transcript_tool.py --request-stdin --report-usage
   # stderr: empty at 6883623 — one receipt after the repair
   ```

   The full transcript of this comparison is
   `docs/plans/feedback-loop-2026-11-17/receipts/red_missing_usage_receipt.txt`.

2. The named fixture of this repository, which re-creates the pre-fix
   publication behaviour so the projection observes the deviation:

   ```
   python tools/replay_feedback_case.py --case usage-receipt-suppressed --out sidecar.jsonl
   ```

3. The regression test, which pins the repaired contract on every terminal
   path:

   ```
   python -B -m pytest -q tests/test_feedback_observation.py::test_report_usage_publishes_a_receipt_on_every_terminal_path
   ```

## Ownership and root cause

- responsible layer: **this repository**, the receipt boundary of the JSON
  entry `transcript_tool.py`. The provider adapter, the supervisor, the
  worker protocol and the consumer are not responsible.
- proven: the receipt was published from exactly three sites — the
  supervisor result, the preflight rejection and the credential error —
  while three sibling terminal paths returned before any of them: the
  oversized request, the unparsable request and the discovery request that
  asks for a source payload. All of them make zero provider requests, so
  the honest receipt is the zero-usage one the preflight path already
  published.
- proven: the repair is in the common responsible layer, not in one path.
  Every terminal path now builds the native receipt and publishes it through
  one site.
- unproven (not this repository's fact): whether any downstream consumer has
  already been affected by the missing receipt, and how it treated a missing
  receipt line. That is a consumer-side fact owned by filing-fetch and ROOT.

## Fix

- commit: `59fc207` (the reviewed source version that carries the repair)
- change: every terminal path builds the native final usage receipt
  (`_zero_usage_receipt`) and publishes it through one site
  (`_publish_usage_receipt`); the supervisor result keeps its own receipt.
- regression:
  `tests/test_feedback_observation.py::test_report_usage_publishes_a_receipt_on_every_terminal_path`
  plus
  `tests/test_feedback_observation.py::test_a_published_receipt_on_an_early_rejection_is_projected_as_known`
- contract effect: none. The result schema, the stdout/stderr split, the
  exit code, the receipt's shape and the "no retries" promise are unchanged;
  the only difference is that a receipt now also appears on the three
  paths that previously published none.

## Consumer re-verification

- state: pending
- who: the consumer that found the cause (ROOT / filing-fetch) re-runs the
  original scenario on version `59fc207` and confirms that the receipt line
  is present on every terminal path. This repository cannot close that step
  on the consumer's behalf; the cross-repository check belongs to ROOT.
