"""Pure public wire contracts for the transcript companion API.

These values are the published request/result schema names and the documented
default limits. They are stdlib-only so a supervisor, a protocol validator, or a
capability reporter can agree on the wire without importing provider HTTP or
parser code. ``transcript_api`` re-exports every name here, so existing
consumers keep importing from the API module and receive the same objects.
"""

from __future__ import annotations

REQUEST_SCHEMA = "earnings-transcript-request/1"
RESULT_SCHEMA = "earnings-transcript-result/1"
RESULT_SCHEMA_WITH_SOURCE_PAYLOAD = "earnings-transcript-result/2"
DISCOVERY_RESULT_SCHEMA = "earnings-transcript-discovery-result/1"
CANDIDATE_FETCH_REQUEST_SCHEMA = "earnings-transcript-candidate-fetch-request/1"
EXTRACTION_VERSION_LEGACY = "motley-fool-main-text/1"
EXTRACTION_VERSION = "motley-fool-main-text/2"
ADAPTER_VERSION = "1.0.0"
MAX_TIMEOUT_SECONDS = 60
MAX_BODY_BYTES = 10 * 1024 * 1024
MAX_LISTING_BYTES = 2 * 1024 * 1024
MAX_FMP_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_PAYLOAD_RESULT_BYTES = 24 * 1024 * 1024
MIN_CONTENT_CHARS = 200
ALLOWED_HOST = "www.fool.com"
FMP_ALLOWED_HOST = "financialmodelingprep.com"
