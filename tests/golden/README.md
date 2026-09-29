# Transcript tool /2 producer goldens

These JSON files are emitted by the real `transcript_tool.main` JSON stdin/stdout path through the real API and a fake HTTP session. Regenerate with:

```powershell
C:\Miniconda\python.exe -B tests/generate_transcript_goldens.py --write
C:\Miniconda\python.exe -B tests/generate_transcript_goldens.py --check
```

The fixture clock is fixed at `2026-09-29T12:00:00Z`. The provider data, FMP key, and transport are fake; these files do not prove a paid FMP account can fetch or retain transcripts. `manifest.json` records each file's SHA-256 and byte count.

## Exact wire requests

- `fmp_v2.request.json` is the stdin request for `transcript_tool.py --request-stdin --include-source-payload`; its positive result is `fmp_v2.fetched.json`. The request alone carries network intent through `download_authorized=true`; the legacy `--allow-download` flag is accepted but unnecessary.
- `motley_candidate_v2.request.json` is the stdin request for `--operation fetch-candidate --include-source-payload`. Production returns `motley_candidate_v2.disabled.json` without HTTP. `motley_candidate_v2.fetched_test_only.json` uses a private test-only provider setting and one fake candidate-page response to preserve a positive 24-key contract example. It is not a production-enabled route.
- Negative FMP results cover missing credentials, HTTP 402 entitlement, wrong quarter, and an FY-only malformed request. Every JSON protocol result exits 0; an argparse usage error exits 2.

## Result meaning and limits

`earnings-transcript-result/2` carries the exact provider response as bounded base64. `provider_payload_sha256` hashes those original response bytes. `canonical_content_sha256` and `content_bytes` describe the extracted untranslated UTF-8 text. There is no original-payload length field. The request timeout is 1–60 seconds, extracted body limit is 1–10 MiB, FMP response limit is 16 MiB, and encoded payload ceiling is 24 MiB. A failed or missing transcript does not contain body bytes.

The FMP success result has 26 exact keys, including `call_date`, `publication_date:null`, and `as_of_cutoff_verified:false`; it has no `published_date`. Its MIME is `application/json`; its safe source/effective URL has only `symbol`, `year`, and `quarter` query parameters, never the API key. The Motley success result has 24 exact keys, including `published_date`. Current company-wiki `transcript_tool_contract.py` only accepts the 24-key Motley shape, rejects JSON MIME, and rejects source URL query parameters. Thus the FMP /2 golden is an **observed producer contract**, with CWP import on hold until the cross-repository owner freezes a provider-specific consumer contract or a new version. Do not synthesize FMP publication time to force the 24-key shape.
