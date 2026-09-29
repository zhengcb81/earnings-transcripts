"""Bounded, side-effect-free provider API for exact-period earnings transcripts.

The API extracts an untranslated text body from a public Motley Fool transcript
page. It does not read project config, write files, translate, cache, or log.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

REQUEST_SCHEMA = "earnings-transcript-request/1"
RESULT_SCHEMA = "earnings-transcript-result/1"
RESULT_SCHEMA_WITH_SOURCE_PAYLOAD = "earnings-transcript-result/2"
DISCOVERY_RESULT_SCHEMA = "earnings-transcript-discovery-result/1"
CANDIDATE_FETCH_REQUEST_SCHEMA = "earnings-transcript-candidate-fetch-request/1"
EXTRACTION_VERSION = "motley-fool-main-text/1"
ADAPTER_VERSION = "1.0.0"
MAX_TIMEOUT_SECONDS = 60
MAX_BODY_BYTES = 10 * 1024 * 1024
MAX_LISTING_BYTES = 2 * 1024 * 1024
MAX_FMP_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_PAYLOAD_RESULT_BYTES = 24 * 1024 * 1024
MIN_CONTENT_CHARS = 200
ALLOWED_HOST = "www.fool.com"
FMP_ALLOWED_HOST = "financialmodelingprep.com"
EXCHANGES = ("nasdaq", "nyse")


@dataclass(frozen=True)
class ProviderSettings:
    """Reviewed runtime availability; request intent cannot enable a provider."""

    motley_fool_enabled: bool = False
    fmp_enabled: bool = True


DEFAULT_PROVIDER_SETTINGS = ProviderSettings()


SKIP_TEXT = (
    "motley fool stock advisor",
    "click here to learn more",
    "advertisement",
    "get access now",
    "join the motley fool",
    "need a quote from a motley fool analyst",
    "image source:",
)
_PERIOD_PATTERNS = (
    re.compile(r"(?:^|[-/])q([1-4])[-_](20\d{2})(?:[-/]|$)", re.IGNORECASE),
    re.compile(r"(?:^|[-/])(20\d{2})[-_]q([1-4])(?:[-/]|$)", re.IGNORECASE),
)
_TRANSCRIPT_PATH_RE = re.compile(
    r"/earnings/call-transcripts/(\d{4})/(\d{2})/(\d{2})/([a-z0-9-]+)/?",
    re.IGNORECASE,
)
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,14}$")


class _InvalidRequest(Exception):
    pass


class _DeadlineExceeded(Exception):
    pass


class _PayloadTooLarge(Exception):
    pass


class _ProvenanceRejected(Exception):
    pass


class _ProviderFailure(Exception):
    def __init__(self, error_code: str = "provider_response"):
        super().__init__(error_code)
        self.error_code = error_code


class _ProviderUnavailable(Exception):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


class _RateLimited(Exception):
    pass


def _validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _InvalidRequest()
    required = {
        "schema_version",
        "request_id",
        "ticker",
        "exchange",
        "fiscal_year",
        "fiscal_quarter",
        "as_of_date",
        "provider",
        "download_authorized",
        "timeout_seconds",
        "max_body_bytes",
    }
    if set(value) != required:
        raise _InvalidRequest()
    if value["schema_version"] != REQUEST_SCHEMA:
        raise _InvalidRequest()
    if not isinstance(value["request_id"], str) or not value["request_id"].strip():
        raise _InvalidRequest()
    if len(value["request_id"]) > 128:
        raise _InvalidRequest()
    if not isinstance(value["ticker"], str):
        raise _InvalidRequest()
    ticker = value["ticker"].strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise _InvalidRequest()
    if value["exchange"] not in ("auto", *EXCHANGES):
        raise _InvalidRequest()
    if type(value["fiscal_year"]) is not int or not 1990 <= value["fiscal_year"] <= 2100:
        raise _InvalidRequest()
    if type(value["fiscal_quarter"]) is not int or value["fiscal_quarter"] not in (1, 2, 3, 4):
        raise _InvalidRequest()
    if not isinstance(value["as_of_date"], str):
        raise _InvalidRequest()
    try:
        as_of_date = date.fromisoformat(value["as_of_date"])
    except ValueError as exc:
        raise _InvalidRequest() from exc
    if as_of_date.isoformat() != value["as_of_date"]:
        raise _InvalidRequest()
    if not isinstance(value["provider"], str) or not value["provider"]:
        raise _InvalidRequest()
    if type(value["download_authorized"]) is not bool:
        raise _InvalidRequest()
    if type(value["timeout_seconds"]) is not int or not 1 <= value["timeout_seconds"] <= MAX_TIMEOUT_SECONDS:
        raise _InvalidRequest()
    if type(value["max_body_bytes"]) is not int or not 1 <= value["max_body_bytes"] <= MAX_BODY_BYTES:
        raise _InvalidRequest()
    normalized = dict(value)
    normalized["ticker"] = ticker
    normalized["as_of"] = as_of_date
    return normalized


def _base_result(
    request_id: str | None,
    status: str,
    *,
    provider: str = "motley_fool",
    result_schema: str = RESULT_SCHEMA,
    **fields: Any,
) -> dict[str, Any]:
    return {
        "schema_version": result_schema,
        "request_id": request_id,
        "status": status,
        "provider": provider,
        **fields,
    }


def _provider_gate(
    request_id: str,
    provider: str,
    *,
    operation: str,
    result_schema: str,
    settings: ProviderSettings,
) -> dict[str, Any] | None:
    """Apply one provider configuration before creating an HTTP session."""
    if provider not in ("motley_fool", "fmp"):
        return _base_result(
            request_id, "unsupported", provider=provider,
            result_schema=result_schema, error_code="provider_unavailable",
        )
    if provider == "motley_fool" and not settings.motley_fool_enabled:
        return _base_result(
            request_id, "unavailable", provider=provider,
            result_schema=result_schema, error_code="provider_disabled",
        )
    if provider == "fmp" and not settings.fmp_enabled:
        return _base_result(
            request_id, "unavailable", provider=provider,
            result_schema=result_schema, error_code="provider_disabled",
        )
    if operation == "discover" and provider != "motley_fool":
        return _base_result(
            request_id, "unsupported", provider=provider,
            result_schema=result_schema,
            error_code="candidate_discovery_unavailable",
        )
    if operation == "fetch-candidate" and provider != "motley_fool":
        return _base_result(
            request_id, "unsupported", provider=provider,
            result_schema=result_schema,
            error_code="candidate_fetch_unavailable",
        )
    return None


def _mime_type(response: requests.Response) -> str:
    content_type = response.headers.get("Content-Type", "")
    if not isinstance(content_type, str):
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _source_payload_fields(
    payload: bytes,
    *,
    mime_type: str,
    effective_url: str,
) -> dict[str, Any]:
    """Build a bounded, opt-in transport form of the exact response bytes."""
    if not payload or len(payload) > MAX_FMP_RESPONSE_BYTES:
        raise _PayloadTooLarge()
    encoded = base64.b64encode(payload).decode("ascii")
    if len(encoded) > MAX_SOURCE_PAYLOAD_RESULT_BYTES:
        raise _PayloadTooLarge()
    return {
        "provider_payload_encoding": "base64",
        "provider_payload_base64": encoded,
        "provider_payload_mime_type": mime_type,
        "effective_url": effective_url,
        "http_status": 200,
        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adapter_name": "earnings-transcripts-transcript-tool",
        "adapter_version": ADAPTER_VERSION,
    }


def _safe_effective_url(url: str) -> str:
    """Return an origin/path URL without query values or fragments."""
    _check_url(url)
    parsed = urlparse(url)
    return f"https://{parsed.hostname}{parsed.path}"


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() != ALLOWED_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
    ):
        raise _ProvenanceRejected()


def _read_bounded(
    session: requests.Session,
    initial_url: str,
    deadline: float,
    max_bytes: int,
) -> tuple[bytes, str, str]:
    url = initial_url
    for redirect_count in range(4):
        _check_url(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _DeadlineExceeded()
        try:
            response = session.get(
                url,
                stream=True,
                allow_redirects=False,
                timeout=(min(5.0, remaining), max(0.1, remaining)),
            )
        except requests.Timeout as exc:
            raise _DeadlineExceeded() from exc
        except requests.RequestException as exc:
            raise _ProviderFailure() from exc
        try:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location or redirect_count == 3:
                    raise _ProvenanceRejected()
                next_url = urljoin(url, location)
                _check_url(next_url)
                url = next_url
                continue
            if response.status_code == 429:
                raise _RateLimited()
            if response.status_code != 200:
                raise _ProviderFailure(f"provider_http_{response.status_code}")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if time.monotonic() > deadline:
                    raise _DeadlineExceeded()
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise _PayloadTooLarge()
                chunks.append(chunk)
            if time.monotonic() > deadline:
                raise _DeadlineExceeded()
            return b"".join(chunks), url, _mime_type(response)
        finally:
            response.close()
    raise _ProvenanceRejected()


def _period_from_slug(slug: str) -> tuple[int, int] | None:
    for pattern in _PERIOD_PATTERNS:
        match = pattern.search(slug)
        if match:
            first, second = int(match.group(1)), int(match.group(2))
            quarter, year = (first, second) if first <= 4 else (second, first)
            if 1 <= quarter <= 4 and 1990 <= year <= 2100:
                return year, quarter
    return None


def _candidate_urls(listing_html: bytes, ticker: str, base_url: str) -> list[dict[str, Any]]:
    text = listing_html.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    raw_urls = {html.unescape(urljoin(base_url, anchor.get("href", ""))) for anchor in soup.find_all("a", href=True)}
    raw_urls.update(
        html.unescape(match.group(0))
        for match in re.finditer(
            r"https?://www\.fool\.com/earnings/call-transcripts/\d{4}/\d{2}/\d{2}/[a-z0-9-]+/?",
            text,
            re.IGNORECASE,
        )
    )
    ticker_slug = ticker.lower().replace(".", "-")
    candidates: dict[str, dict[str, Any]] = {}
    for raw_url in raw_urls:
        try:
            _check_url(raw_url)
        except _ProvenanceRejected:
            continue
        parsed = urlparse(raw_url)
        match = _TRANSCRIPT_PATH_RE.fullmatch(parsed.path)
        if not match:
            continue
        year, month, day, slug = match.groups()
        if not re.search(rf"(?:^|-){re.escape(ticker_slug)}(?:-|$)", slug.lower()):
            continue
        try:
            published = date(int(year), int(month), int(day))
        except ValueError:
            continue
        period = _period_from_slug(slug)
        canonical_url = f"https://{ALLOWED_HOST}{parsed.path.rstrip('/')}/"
        candidates[canonical_url] = {
            "source_url": canonical_url,
            "provider_document_id": parsed.path.rstrip("/"),
            "published_date": published,
            "period": period,
        }
    return list(candidates.values())


def _extract_body(page: bytes) -> tuple[str, str]:
    soup = BeautifulSoup(page, "html.parser")
    title_node = soup.find("h1")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    main = soup.find("main")
    if main is None:
        raise _ProviderFailure()
    paragraphs: list[str] = []
    for node in main.find_all(["p", "h2", "h3", "li"]):
        text = " ".join(node.get_text(" ", strip=True).split())
        lowered = text.casefold()
        if len(text) < 5 or any(skip in lowered for skip in SKIP_TEXT):
            continue
        paragraphs.append(text)
    body = "\n\n".join(paragraphs).strip()
    if not title or len(body) < MIN_CONTENT_CHARS:
        raise _ProviderFailure()
    return title, body


def _check_fmp_effective_url(
    url: str, normalized: dict[str, Any], api_key: str
) -> None:
    """Check the actual response location without returning its secret query."""
    if not isinstance(url, str):
        raise _ProvenanceRejected()
    try:
        parsed = urlparse(url)
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname != FMP_ALLOWED_HOST
            or parsed.path != "/stable/earning-call-transcript"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.fragment
        ):
            raise _ProvenanceRejected()
        if parsed.query:
            pairs = parse_qsl(
                parsed.query, keep_blank_values=True, strict_parsing=True
            )
            expected = {
                "symbol": normalized["ticker"],
                "year": str(normalized["fiscal_year"]),
                "quarter": str(normalized["fiscal_quarter"]),
                "apikey": api_key,
            }
            if len(pairs) != len(expected) or dict(pairs) != expected:
                raise _ProvenanceRejected()
    except ValueError as exc:
        raise _ProvenanceRejected() from exc


def _read_fmp_payload(
    session: requests.Session,
    normalized: dict[str, Any],
    deadline: float,
    api_key: str,
) -> tuple[bytes, str]:
    url = f"https://{FMP_ALLOWED_HOST}/stable/earning-call-transcript"
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != FMP_ALLOWED_HOST:
        raise _ProvenanceRejected()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _DeadlineExceeded()
    try:
        response = session.get(
            url,
            params={
                "symbol": normalized["ticker"],
                "year": normalized["fiscal_year"],
                "quarter": normalized["fiscal_quarter"],
                "apikey": api_key,
            },
            stream=True,
            allow_redirects=False,
            timeout=(min(5.0, remaining), max(0.1, remaining)),
        )
    except requests.Timeout as exc:
        raise _DeadlineExceeded() from exc
    except requests.RequestException as exc:
        raise _ProviderFailure() from exc
    try:
        _check_fmp_effective_url(response.url, normalized, api_key)
        if response.status_code in (301, 302, 303, 307, 308):
            raise _ProvenanceRejected()
        if response.status_code == 402:
            raise _ProviderUnavailable("provider_entitlement_required")
        if response.status_code == 401:
            raise _ProviderUnavailable("provider_credentials_rejected")
        if response.status_code == 403:
            raise _ProviderUnavailable("provider_entitlement_denied")
        if response.status_code == 429:
            raise _RateLimited()
        if response.status_code != 200:
            raise _ProviderFailure(f"provider_http_{response.status_code}")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if time.monotonic() > deadline:
                raise _DeadlineExceeded()
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_FMP_RESPONSE_BYTES:
                raise _PayloadTooLarge()
            chunks.append(chunk)
        if time.monotonic() > deadline:
            raise _DeadlineExceeded()
        return b"".join(chunks), _mime_type(response)
    finally:
        response.close()


def _fmp_period(item: dict[str, Any]) -> tuple[int, int] | None:
    raw_year = item.get("year")
    if isinstance(raw_year, str) and raw_year.isdigit():
        year = int(raw_year)
    elif type(raw_year) is int:
        year = raw_year
    else:
        return None
    raw_quarter = item.get("period", item.get("quarter"))
    if type(raw_quarter) is int:
        quarter = raw_quarter
    elif isinstance(raw_quarter, str):
        match = re.fullmatch(r"Q?([1-4])", raw_quarter.strip(), re.IGNORECASE)
        if not match:
            return None
        quarter = int(match.group(1))
    else:
        return None
    if not 1990 <= year <= 2100 or not 1 <= quarter <= 4:
        return None
    return year, quarter


def _fetch_fmp_transcript(
    normalized: dict[str, Any],
    session: requests.Session,
    deadline: float,
    api_key: str,
    *,
    include_source_payload: bool,
    result_schema: str,
) -> dict[str, Any]:
    request_id = normalized["request_id"]
    payload, mime_type = _read_fmp_payload(session, normalized, deadline, api_key)
    if include_source_payload and mime_type != "application/json":
        raise _ProvenanceRejected()
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _ProviderFailure() from exc
    if not isinstance(data, list):
        raise _ProviderFailure()
    if not data:
        return _base_result(
            request_id,
            "not_found",
            provider="fmp",
            result_schema=result_schema,
            fiscal_period=f"{normalized['fiscal_year']}-Q{normalized['fiscal_quarter']}",
            candidate_count=0,
        )

    matches: list[dict[str, Any]] = []
    expected_period = (normalized["fiscal_year"], normalized["fiscal_quarter"])
    for item in data:
        if not isinstance(item, dict):
            raise _ProvenanceRejected()
        symbol = item.get("symbol")
        period = _fmp_period(item)
        if not isinstance(symbol, str) or symbol.strip().upper() != normalized["ticker"]:
            raise _ProvenanceRejected()
        if period != expected_period:
            raise _ProvenanceRejected()
        raw_date = item.get("date")
        raw_content = item.get("content")
        if not isinstance(raw_date, str) or not isinstance(raw_content, str):
            raise _ProvenanceRejected()
        try:
            call_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise _ProvenanceRejected() from exc
        if call_date.isoformat() != raw_date:
            raise _ProvenanceRejected()
        content = raw_content.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not content:
            raise _ProviderFailure()
        matches.append({"call_date": call_date, "content": content})

    if len(matches) > 1:
        return _base_result(
            request_id,
            "ambiguous",
            provider="fmp",
            result_schema=result_schema,
            fiscal_period=f"{normalized['fiscal_year']}-Q{normalized['fiscal_quarter']}",
            candidate_count=len(matches),
        )
    match = matches[0]
    if match["call_date"] > normalized["as_of"]:
        return _base_result(
            request_id,
            "not_found",
            provider="fmp",
            result_schema=result_schema,
            fiscal_period=f"{normalized['fiscal_year']}-Q{normalized['fiscal_quarter']}",
            candidate_count=0,
        )
    content_bytes = match["content"].encode("utf-8")
    if len(content_bytes) > normalized["max_body_bytes"]:
        raise _PayloadTooLarge()
    safe_source_url = (
        f"https://{FMP_ALLOWED_HOST}/stable/earning-call-transcript"
        f"?symbol={normalized['ticker']}&year={normalized['fiscal_year']}"
        f"&quarter={normalized['fiscal_quarter']}"
    )
    period_label = f"{normalized['ticker']} {normalized['fiscal_year']} Q{normalized['fiscal_quarter']}"
    result_fields: dict[str, Any] = {
        "ticker": normalized["ticker"],
        "exchange": normalized["exchange"],
        "fiscal_period": f"{normalized['fiscal_year']}-Q{normalized['fiscal_quarter']}",
        "as_of_date": normalized["as_of"].isoformat(),
        "title": f"{period_label} Earnings Call Transcript",
        "source_url": safe_source_url,
        "provider_document_id": (
            f"fmp:{normalized['ticker']}:{normalized['fiscal_year']}"
            f":Q{normalized['fiscal_quarter']}:{match['call_date'].isoformat()}"
        ),
        "call_date": match["call_date"].isoformat(),
        "publication_date": None,
        "as_of_cutoff_verified": False,
        "extraction_version": "fmp-api-content/1",
        "provider_payload_sha256": hashlib.sha256(payload).hexdigest(),
        "canonical_content_sha256": hashlib.sha256(content_bytes).hexdigest(),
        "content_bytes": len(content_bytes),
    }
    if include_source_payload:
        result_fields.update(_source_payload_fields(
            payload,
            mime_type=mime_type,
            effective_url=safe_source_url,
        ))
    else:
        result_fields["content_utf8"] = match["content"]
    return _base_result(
        request_id,
        "fetched",
        provider="fmp",
        result_schema=result_schema,
        **result_fields,
    )


def fetch_transcript(
    request: Any,
    *,
    session_factory: Callable[[], requests.Session] = requests.Session,
    fmp_api_key: str | None = None,
    include_source_payload: bool = False,
    provider_settings: ProviderSettings = DEFAULT_PROVIDER_SETTINGS,
) -> dict[str, Any]:
    """Fetch one exact fiscal quarter or return a bounded structured status.

    The request's download_authorized field is the single network intent.
    Provider availability is separate runtime configuration. The function is
    hermetic in tests through session_factory and has no filesystem effects.
    """
    request_id = request.get("request_id") if isinstance(request, dict) else None
    if type(include_source_payload) is not bool:
        return _base_result(request_id, "invalid_request", error_code="source_payload_flag")
    result_schema = RESULT_SCHEMA_WITH_SOURCE_PAYLOAD if include_source_payload else RESULT_SCHEMA
    try:
        normalized = _validate_request(request)
    except _InvalidRequest:
        return _base_result(request_id, "invalid_request", result_schema=result_schema, error_code="request_schema")
    request_id = normalized["request_id"]
    if not normalized["download_authorized"]:
        return _base_result(
            request_id, "not_authorized", provider=normalized["provider"],
            result_schema=result_schema,
        )
    provider = normalized["provider"]
    gate = _provider_gate(
        request_id, provider, operation="fetch", result_schema=result_schema,
        settings=provider_settings,
    )
    if gate is not None:
        return gate
    if provider == "fmp" and (not isinstance(fmp_api_key, str) or not fmp_api_key.strip()):
        return _base_result(
            request_id,
            "unavailable",
            provider="fmp",
            result_schema=result_schema,
            error_code="provider_credentials_missing",
        )

    deadline = time.monotonic() + normalized["timeout_seconds"]
    ticker = normalized["ticker"]
    exchanges = EXCHANGES if normalized["exchange"] == "auto" else (normalized["exchange"],)
    session = None
    try:
        session = session_factory()
        session.headers.update({
            "User-Agent": "company-wiki-transcript-adapter/1.0",
            "Accept": "application/json" if provider == "fmp" else "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        if provider == "fmp":
            return _fetch_fmp_transcript(
                normalized,
                session,
                deadline,
                fmp_api_key.strip(),
                include_source_payload=include_source_payload,
                result_schema=result_schema,
            )
        eligible: dict[str, dict[str, Any]] = {}
        for exchange in exchanges:
            quote_url = f"https://{ALLOWED_HOST}/quote/{exchange}/{ticker.lower()}/"
            listing, _, _ = _read_bounded(session, quote_url, deadline, MAX_LISTING_BYTES)
            for candidate in _candidate_urls(listing, ticker, quote_url):
                candidate_period = candidate["period"]
                if (
                    candidate_period == (normalized["fiscal_year"], normalized["fiscal_quarter"])
                    and candidate["published_date"] <= normalized["as_of"]
                ):
                    eligible[candidate["source_url"]] = candidate
        if not eligible:
            return _base_result(
                request_id,
                "not_found",
                result_schema=result_schema,
                fiscal_period=f"{normalized['fiscal_year']}-Q{normalized['fiscal_quarter']}",
                candidate_count=0,
            )
        if len(eligible) > 1:
            return _base_result(
                request_id,
                "ambiguous",
                result_schema=result_schema,
                fiscal_period=f"{normalized['fiscal_year']}-Q{normalized['fiscal_quarter']}",
                candidate_count=len(eligible),
                candidate_ids=sorted(item["provider_document_id"] for item in eligible.values()),
            )
        candidate = next(iter(eligible.values()))
        page, final_url, mime_type = _read_bounded(
            session, candidate["source_url"], deadline, normalized["max_body_bytes"]
        )
        _check_url(final_url)
        if include_source_payload and mime_type not in ("text/html", "application/xhtml+xml"):
            raise _ProvenanceRejected()
        title, body = _extract_body(page)
        body_bytes = body.encode("utf-8")
        if len(body_bytes) > normalized["max_body_bytes"]:
            raise _PayloadTooLarge()
        result_fields: dict[str, Any] = {
            "ticker": ticker,
            "exchange": normalized["exchange"],
            "fiscal_period": f"{normalized['fiscal_year']}-Q{normalized['fiscal_quarter']}",
            "as_of_date": normalized["as_of"].isoformat(),
            "title": title,
            "source_url": candidate["source_url"],
            "provider_document_id": candidate["provider_document_id"],
            "published_date": candidate["published_date"].isoformat(),
            "extraction_version": EXTRACTION_VERSION,
            "provider_payload_sha256": hashlib.sha256(page).hexdigest(),
            "canonical_content_sha256": hashlib.sha256(body_bytes).hexdigest(),
            "content_bytes": len(body_bytes),
        }
        if include_source_payload:
            result_fields.update(_source_payload_fields(
                page,
                mime_type=mime_type,
                effective_url=_safe_effective_url(final_url),
            ))
        else:
            result_fields["content_utf8"] = body
        return _base_result(
            request_id,
            "fetched",
            result_schema=result_schema,
            **result_fields,
        )
    except _DeadlineExceeded:
        return _base_result(request_id, "deadline_exceeded", provider=provider, result_schema=result_schema, error_code="provider_deadline")
    except _PayloadTooLarge:
        return _base_result(request_id, "content_too_large", provider=provider, result_schema=result_schema, error_code="byte_limit")
    except _ProvenanceRejected:
        return _base_result(request_id, "provenance_rejected", provider=provider, result_schema=result_schema, error_code="provider_identity_or_host")
    except _ProviderUnavailable as exc:
        return _base_result(
            request_id, "unavailable", provider=provider,
            result_schema=result_schema, error_code=exc.error_code,
        )
    except _RateLimited:
        return _base_result(
            request_id, "rate_limited", provider=provider,
            result_schema=result_schema, error_code="provider_http_429",
        )
    except _ProviderFailure as exc:
        return _base_result(request_id, "provider_error", provider=provider, result_schema=result_schema, error_code=exc.error_code)
    except requests.Timeout:
        return _base_result(request_id, "deadline_exceeded", provider=provider, result_schema=result_schema, error_code="provider_deadline")
    except Exception:
        return _base_result(request_id, "provider_error", provider=provider, result_schema=result_schema, error_code="unexpected_provider_failure")
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def discover_transcripts(
    request: Any,
    *,
    session_factory: Callable[[], requests.Session] = requests.Session,
    provider_settings: ProviderSettings = DEFAULT_PROVIDER_SETTINGS,
) -> dict[str, Any]:
    """Discover exact-period Motley Fool candidates without fetching transcript pages.

    This is the safe first phase for an orchestrator that must authorize the
    selected candidate before requesting its body. Listing-page access still
    requires the caller's discovery rights decision and both download gates.
    """
    request_id = request.get("request_id") if isinstance(request, dict) else None
    try:
        normalized = _validate_request(request)
    except _InvalidRequest:
        return _base_result(
            request_id,
            "invalid_request",
            result_schema=DISCOVERY_RESULT_SCHEMA,
            error_code="request_schema",
        )
    request_id = normalized["request_id"]
    provider = normalized["provider"]
    if not normalized["download_authorized"]:
        return _base_result(
            request_id, "not_authorized", provider=provider,
            result_schema=DISCOVERY_RESULT_SCHEMA,
        )
    gate = _provider_gate(
        request_id, provider, operation="discover",
        result_schema=DISCOVERY_RESULT_SCHEMA, settings=provider_settings,
    )
    if gate is not None:
        return gate

    deadline = time.monotonic() + normalized["timeout_seconds"]
    exchanges = EXCHANGES if normalized["exchange"] == "auto" else (normalized["exchange"],)
    session = None
    try:
        session = session_factory()
        session.headers.update({
            "User-Agent": "company-wiki-transcript-adapter/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        eligible: dict[str, dict[str, Any]] = {}
        expected_period = (normalized["fiscal_year"], normalized["fiscal_quarter"])
        for exchange in exchanges:
            quote_url = f"https://{ALLOWED_HOST}/quote/{exchange}/{normalized['ticker'].lower()}/"
            listing, _, _ = _read_bounded(session, quote_url, deadline, MAX_LISTING_BYTES)
            for candidate in _candidate_urls(listing, normalized["ticker"], quote_url):
                if (
                    candidate["period"] == expected_period
                    and candidate["published_date"] <= normalized["as_of"]
                ):
                    eligible[candidate["source_url"]] = candidate
        candidates = [
            {
                "provider": "motley_fool",
                "ticker": normalized["ticker"],
                "exchange": normalized["exchange"],
                "fiscal_period": f"{normalized['fiscal_year']}-Q{normalized['fiscal_quarter']}",
                "provider_document_id": item["provider_document_id"],
                "source_url": item["source_url"],
                "published_date": item["published_date"].isoformat(),
            }
            for item in sorted(eligible.values(), key=lambda value: value["source_url"])
        ]
        if not candidates:
            status = "not_found"
        elif len(candidates) > 1:
            status = "ambiguous"
        else:
            status = "discovered"
        return _base_result(
            request_id,
            status,
            provider=provider,
            result_schema=DISCOVERY_RESULT_SCHEMA,
            ticker=normalized["ticker"],
            fiscal_period=f"{normalized['fiscal_year']}-Q{normalized['fiscal_quarter']}",
            as_of_date=normalized["as_of"].isoformat(),
            candidate_count=len(candidates),
            candidates=candidates,
        )
    except _DeadlineExceeded:
        return _base_result(
            request_id, "deadline_exceeded", provider=provider,
            result_schema=DISCOVERY_RESULT_SCHEMA, error_code="provider_deadline",
        )
    except _PayloadTooLarge:
        return _base_result(
            request_id, "content_too_large", provider=provider,
            result_schema=DISCOVERY_RESULT_SCHEMA, error_code="byte_limit",
        )
    except _ProvenanceRejected:
        return _base_result(
            request_id, "provenance_rejected", provider=provider,
            result_schema=DISCOVERY_RESULT_SCHEMA, error_code="provider_identity_or_host",
        )
    except _ProviderUnavailable as exc:
        return _base_result(
            request_id, "unavailable", provider=provider,
            result_schema=DISCOVERY_RESULT_SCHEMA, error_code=exc.error_code,
        )
    except _RateLimited:
        return _base_result(
            request_id, "rate_limited", provider=provider,
            result_schema=DISCOVERY_RESULT_SCHEMA, error_code="provider_http_429",
        )
    except _ProviderFailure as exc:
        return _base_result(
            request_id, "provider_error", provider=provider,
            result_schema=DISCOVERY_RESULT_SCHEMA, error_code=exc.error_code,
        )
    except requests.Timeout:
        return _base_result(
            request_id, "deadline_exceeded", provider=provider,
            result_schema=DISCOVERY_RESULT_SCHEMA, error_code="provider_deadline",
        )
    except Exception:
        return _base_result(
            request_id, "provider_error", provider=provider,
            result_schema=DISCOVERY_RESULT_SCHEMA, error_code="unexpected_provider_failure",
        )
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def _validate_candidate_fetch_request(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    top_level_fields = {
        "schema_version", "request_id", "ticker", "exchange", "fiscal_year",
        "fiscal_quarter", "as_of_date", "provider", "download_authorized",
        "timeout_seconds", "max_body_bytes", "candidate",
    }
    candidate_fields = {"provider_document_id", "source_url", "published_date"}
    if not isinstance(value, dict) or set(value) != top_level_fields:
        raise _InvalidRequest()
    if value["schema_version"] != CANDIDATE_FETCH_REQUEST_SCHEMA:
        raise _InvalidRequest()
    candidate = value["candidate"]
    if not isinstance(candidate, dict) or set(candidate) != candidate_fields:
        raise _InvalidRequest()
    base_request = {
        key: value[key]
        for key in top_level_fields
        if key not in {"schema_version", "candidate"}
    }
    base_request["schema_version"] = REQUEST_SCHEMA
    normalized = _validate_request(base_request)
    for key in candidate_fields:
        if not isinstance(candidate[key], str) or not candidate[key].strip():
            raise _InvalidRequest()
    if normalized["provider"] != "motley_fool":
        return normalized, candidate
    source_url = candidate["source_url"]
    try:
        _check_url(source_url)
    except _ProvenanceRejected as exc:
        raise _InvalidRequest() from exc
    parsed = urlparse(source_url)
    if parsed.query or parsed.fragment:
        raise _InvalidRequest()
    match = _TRANSCRIPT_PATH_RE.fullmatch(parsed.path)
    if not match:
        raise _InvalidRequest()
    year_text, month_text, day_text, slug = match.groups()
    try:
        published = date(int(year_text), int(month_text), int(day_text))
        supplied_date = date.fromisoformat(candidate["published_date"])
    except ValueError as exc:
        raise _InvalidRequest() from exc
    expected_period = (normalized["fiscal_year"], normalized["fiscal_quarter"])
    ticker_slug = normalized["ticker"].lower().replace(".", "-")
    if (
        supplied_date.isoformat() != candidate["published_date"]
        or supplied_date != published
        or published > normalized["as_of"]
        or _period_from_slug(slug) != expected_period
        or not re.search(rf"(?:^|-){re.escape(ticker_slug)}(?:-|$)", slug.lower())
        or candidate["provider_document_id"] != parsed.path.rstrip("/")
        or source_url != f"https://{ALLOWED_HOST}{parsed.path.rstrip('/')}/"
    ):
        raise _InvalidRequest()
    return normalized, candidate


def fetch_transcript_candidate(
    request: Any,
    *,
    session_factory: Callable[[], requests.Session] = requests.Session,
    include_source_payload: bool = False,
    provider_settings: ProviderSettings = DEFAULT_PROVIDER_SETTINGS,
) -> dict[str, Any]:
    """Fetch one already-discovered Motley Fool candidate; never discovers here."""
    request_id = request.get("request_id") if isinstance(request, dict) else None
    if type(include_source_payload) is not bool:
        return _base_result(request_id, "invalid_request", error_code="source_payload_flag")
    result_schema = RESULT_SCHEMA_WITH_SOURCE_PAYLOAD if include_source_payload else RESULT_SCHEMA
    try:
        normalized, candidate = _validate_candidate_fetch_request(request)
    except _InvalidRequest:
        return _base_result(
            request_id, "invalid_request", result_schema=result_schema,
            error_code="candidate_request_schema_or_identity",
        )
    request_id = normalized["request_id"]
    provider = normalized["provider"]
    if not normalized["download_authorized"]:
        return _base_result(
            request_id, "not_authorized", provider=provider, result_schema=result_schema,
        )
    gate = _provider_gate(
        request_id, provider, operation="fetch-candidate",
        result_schema=result_schema, settings=provider_settings,
    )
    if gate is not None:
        return gate

    deadline = time.monotonic() + normalized["timeout_seconds"]
    session = None
    try:
        session = session_factory()
        session.headers.update({
            "User-Agent": "company-wiki-transcript-adapter/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        page, final_url, mime_type = _read_bounded(
            session,
            candidate["source_url"],
            deadline,
            normalized["max_body_bytes"],
        )
        safe_effective_url = _safe_effective_url(final_url)
        if safe_effective_url != candidate["source_url"]:
            raise _ProvenanceRejected()
        if include_source_payload and mime_type not in ("text/html", "application/xhtml+xml"):
            raise _ProvenanceRejected()
        title, body = _extract_body(page)
        body_bytes = body.encode("utf-8")
        if len(body_bytes) > normalized["max_body_bytes"]:
            raise _PayloadTooLarge()
        result_fields: dict[str, Any] = {
            "ticker": normalized["ticker"],
            "exchange": normalized["exchange"],
            "fiscal_period": f"{normalized['fiscal_year']}-Q{normalized['fiscal_quarter']}",
            "as_of_date": normalized["as_of"].isoformat(),
            "title": title,
            "source_url": candidate["source_url"],
            "provider_document_id": candidate["provider_document_id"],
            "published_date": candidate["published_date"],
            "extraction_version": EXTRACTION_VERSION,
            "provider_payload_sha256": hashlib.sha256(page).hexdigest(),
            "canonical_content_sha256": hashlib.sha256(body_bytes).hexdigest(),
            "content_bytes": len(body_bytes),
        }
        if include_source_payload:
            result_fields.update(_source_payload_fields(
                page, mime_type=mime_type, effective_url=safe_effective_url,
            ))
        else:
            result_fields["content_utf8"] = body
        return _base_result(
            request_id,
            "fetched",
            provider=provider,
            result_schema=result_schema,
            **result_fields,
        )
    except _DeadlineExceeded:
        return _base_result(
            request_id, "deadline_exceeded", provider=provider,
            result_schema=result_schema, error_code="provider_deadline",
        )
    except _PayloadTooLarge:
        return _base_result(
            request_id, "content_too_large", provider=provider,
            result_schema=result_schema, error_code="byte_limit",
        )
    except _ProvenanceRejected:
        return _base_result(
            request_id, "provenance_rejected", provider=provider,
            result_schema=result_schema, error_code="candidate_effective_url_or_mime",
        )
    except _ProviderUnavailable as exc:
        return _base_result(
            request_id, "unavailable", provider=provider,
            result_schema=result_schema, error_code=exc.error_code,
        )
    except _RateLimited:
        return _base_result(
            request_id, "rate_limited", provider=provider,
            result_schema=result_schema, error_code="provider_http_429",
        )
    except _ProviderFailure as exc:
        return _base_result(
            request_id, "provider_error", provider=provider,
            result_schema=result_schema, error_code=exc.error_code,
        )
    except requests.Timeout:
        return _base_result(
            request_id, "deadline_exceeded", provider=provider,
            result_schema=result_schema, error_code="provider_deadline",
        )
    except Exception:
        return _base_result(
            request_id, "provider_error", provider=provider,
            result_schema=result_schema, error_code="unexpected_provider_failure",
        )
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
