# ET-FB-001 · transcript-period-attribution

- issue_key: `transcript-period-attribution`
- status: open
- owner: earnings-transcripts
- first_seen: sample-deviation-msft/attempt-1/fb-foreign-period-msft-2026q3
- evidence_class: controlled fault injection (rehearsal) — **no production
  occurrence of this deviation has been observed**

## Expected and actual

- expected: `not_attributed` — a run may attribute the requested fiscal
  period to a document only when the original provider response itself proves
  that period.
- actual: `requested_period_attributed` — the run claimed the requested
  period for a document the provider had published for another quarter.
- disposition: deviation

## Impact

If the provider adapter ever stopped comparing the provider's period with
the requested one, the tool would return another company's or another
quarter's call under the requested period's identity, with a matching
`provider_payload_sha256` and `canonical_content_sha256`. Every downstream
consumer (filing-fetch, company-wiki) stores the transcript under that
identity, so the misattribution would propagate silently: the wire would
look successful and only the content would be wrong. The repair for such a
defect belongs in the provider adapter; the detection belongs to the
projection that this card adds.

**This card is a rehearsal of the detection, not a report of a live defect.**
The occurrences below come from a controlled fault injection that makes the
adapter accept any period (see the reproduction). The provider adapter in
this repository does compare the period today, and the legal counterpart
below shows the refusal it produces.

## Occurrences

<!-- occurrences:begin -->
| sample-deviation-aapl | attempt-1 | fb-foreign-period-aapl-2025q2 | exact-period-ownership | not_attributed | requested_period_attributed | deviation | et-case:fmp-foreign-period-fault-alt |
| sample-deviation-msft | attempt-1 | fb-foreign-period-msft-2026q3 | exact-period-ownership | not_attributed | requested_period_attributed | deviation | et-case:fmp-foreign-period-fault |
<!-- occurrences:end -->

Two occurrences, two companies, two periods, one cause: the same
`issue_key` keeps them on one card.

## Reproduction

The named fixtures of this repository, none of which contacts a provider:

```
python tools/replay_feedback_case.py --case fmp-foreign-period-fault --out sidecar.jsonl
python tools/replay_feedback_case.py --case fmp-foreign-period-fault-alt --out sidecar.jsonl
```

The legal counterpart — the same provider answer, without the injected
fault — is the refusal the product really produces:

```
python tools/replay_feedback_case.py --case fmp-wrong-quarter --out sidecar.jsonl
# status provenance_rejected / provider_identity_or_host, disposition expected
```

and it matches the existing producer golden
`tests/golden/fmp_v2.wrong_quarter.json` byte for byte.

The regression that pins the detection:

```
python -B -m pytest -q tests/test_feedback_observation.py::test_a_fetch_claiming_a_foreign_period_is_a_deviation
```

## Ownership and root cause

- responsible layer: **this repository**, the provider adapter that maps a
  provider document onto the requested period
  (`transcript_api._fetch_fmp_transcript`), and the projection that judges
  the attribution.
- proven: the adapter refuses a foreign period today, and the refusal
  carries the named reason `provider_identity_or_host`.
- proven: the projection re-derives the provider's period from the original
  response bytes it returns, independently of the business result, and
  reports a deviation when the two disagree.
- unproven: that the adapter can actually be made to misattribute in a
  production run. The only reproduction available offline is the injected
  fault; no live provider answer has produced this deviation.
- not this repository's fact: whether a downstream consumer would accept a
  misattributed transcript without noticing. That belongs to the consumer
  owners (filing-fetch, company-wiki).

## Fix

- commit: the detection ships with `59fc207` (the ownership check of the
  projection); the adapter itself needed no change, because it is correct
  today.
- regression:
  `tests/test_feedback_observation.py::test_a_fetch_claiming_a_foreign_period_is_a_deviation`
  and the legal counterpart
  `tests/test_feedback_observation.py::test_a_legal_provider_shape_is_projected_as_expected`
- contract effect: none. The projection is off by default and writes only its
  own sidecar.

## Consumer re-verification

- state: pending
- who: ROOT reviews the adjudication — that a legal refusal and a real
  misattribution are told apart by the observation — on version `59fc207`.
  A live occurrence, if one is ever observed, needs a new attempt and a new
  evidence reference; it does not reuse this card's rehearsal evidence.
