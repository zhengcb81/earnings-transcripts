"""Pure request validation and provider gating for the transcript API.

This module owns the decisions that must happen before any provider code or HTTP
session exists: request schema/normalization, download authorization, provider
availability, exchange support and the explicit cost ceiling. It is stdlib-only
(plus the pure contracts/settings modules), so the public CLI can reject a
request in the parent process without importing requests, BeautifulSoup, the
extractor, or the provider implementation. ``transcript_api`` re-exports these
names for existing internal callers.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from transcript_contracts import REQUEST_SCHEMA, RESULT_SCHEMA
from transcript_provider_settings import (
    EXCHANGES,
    ProviderSettings,
    provider_operation_capability,
)

__all__ = [
    "InvalidRequest",
    "build_result",
    "check_provider_request",
    "is_valid_ticker",
    "preflight_transcript_fetch",
    "validate_request",
]

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,14}$")


def is_valid_ticker(ticker: str) -> bool:
    """Validate the already-normalized provider ticker."""
    return _TICKER_RE.fullmatch(ticker) is not None


class InvalidRequest(Exception):
    """The supplied request does not match the wire contract."""


def validate_request(value: Any) -> dict[str, Any]:
    """Normalize a wire request before any credential or provider access."""
    if not isinstance(value, dict):
        raise InvalidRequest()
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
    if not required.issubset(value) or set(value) - required - {
        "max_cost_usd",
        "max_response_bytes",
    }:
        raise InvalidRequest()
    if value["schema_version"] != REQUEST_SCHEMA:
        raise InvalidRequest()
    if not isinstance(value["request_id"], str) or not value["request_id"].strip():
        raise InvalidRequest()
    if len(value["request_id"]) > 128:
        raise InvalidRequest()
    if not isinstance(value["ticker"], str):
        raise InvalidRequest()
    ticker = value["ticker"].strip().upper()
    if not is_valid_ticker(ticker):
        raise InvalidRequest()
    exchange = value["exchange"]
    if not isinstance(exchange, str):
        raise InvalidRequest()
    exchange = exchange.strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", exchange) is None:
        raise InvalidRequest()
    if (
        type(value["fiscal_year"]) is not int
        or not 1990 <= value["fiscal_year"] <= 2100
    ):
        raise InvalidRequest()
    if type(value["fiscal_quarter"]) is not int or value["fiscal_quarter"] not in (
        1,
        2,
        3,
        4,
    ):
        raise InvalidRequest()
    if not isinstance(value["as_of_date"], str):
        raise InvalidRequest()
    try:
        as_of_date = date.fromisoformat(value["as_of_date"])
    except ValueError as exc:
        raise InvalidRequest() from exc
    if as_of_date.isoformat() != value["as_of_date"]:
        raise InvalidRequest()
    if not isinstance(value["provider"], str) or not value["provider"]:
        raise InvalidRequest()
    if type(value["download_authorized"]) is not bool:
        raise InvalidRequest()
    if (
        type(value["timeout_seconds"]) is not int
        or value["timeout_seconds"] < 1
    ):
        raise InvalidRequest()
    if (
        type(value["max_body_bytes"]) is not int
        or value["max_body_bytes"] <= 0
    ):
        raise InvalidRequest()
    if "max_response_bytes" in value and (
        type(value["max_response_bytes"]) is not int or value["max_response_bytes"] <= 0
    ):
        raise InvalidRequest()
    normalized = dict(value)
    fee = value.get("max_cost_usd", "0")
    if not isinstance(fee, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", fee
    ):
        raise InvalidRequest()
    normalized["cost_limit"] = Decimal(fee)
    normalized["exchange"] = exchange
    normalized["ticker"] = ticker
    normalized["as_of"] = as_of_date
    return normalized


def preflight_transcript_fetch(
    request: Any, *, result_schema: str, settings: ProviderSettings,
) -> dict[str, Any] | None:
    """Validate the fetch contract/capability without key access or HTTP."""
    request_id = request.get("request_id") if isinstance(request, dict) else None
    try:
        normalized = validate_request(request)
    except InvalidRequest:
        return build_result(request_id, "invalid_request", result_schema=result_schema,
                            error_code="request_schema")
    if not normalized["download_authorized"]:
        return build_result(request_id, "not_authorized", provider=normalized["provider"],
                            result_schema=result_schema)
    return check_provider_request(normalized["request_id"], normalized["provider"], operation="fetch",
                          result_schema=result_schema, settings=settings,
                          cost_limit=normalized["cost_limit"], exchange=normalized["exchange"])


def build_result(
    request_id: str | None,
    status: str,
    *,
    provider: str = "motley_fool",
    result_schema: str = RESULT_SCHEMA,
    **fields: Any,
) -> dict[str, Any]:
    """Build the standard operation-result envelope."""
    return {
        "schema_version": result_schema,
        "request_id": request_id,
        "status": status,
        "provider": provider,
        **fields,
    }


def check_provider_request(
    request_id: str,
    provider: str,
    *,
    operation: str,
    result_schema: str,
    settings: ProviderSettings,
    cost_limit: Decimal = Decimal("0"),
    exchange: str | None = None,
) -> dict[str, Any] | None:
    """Apply one provider configuration before creating an HTTP session."""
    if provider not in ("motley_fool", "fmp"):
        return build_result(
            request_id,
            "unsupported",
            provider=provider,
            result_schema=result_schema,
            error_code="provider_unavailable",
        )
    capability = provider_operation_capability(provider, operation, settings=settings)
    if not capability["enabled"]:
        return build_result(
            request_id,
            "unavailable",
            provider=provider,
            result_schema=result_schema,
            error_code="provider_disabled",
        )
    if not capability["supported"]:
        return build_result(
            request_id,
            "unsupported",
            provider=provider,
            result_schema=result_schema,
            error_code="candidate_discovery_unavailable"
            if operation == "discover"
            else "candidate_fetch_unavailable",
        )
    if exchange is not None and exchange not in capability.get(
        "supported_exchanges", ("auto", *EXCHANGES)
    ):
        return build_result(
            request_id, "unsupported", provider=provider, result_schema=result_schema,
            error_code="unsupported_market" if exchange == "hkex" else "unsupported_exchange",
        )
    fee = capability.get("incremental_cost_usd")
    try:
        charge = Decimal(fee) if isinstance(fee, str) else Decimal("NaN")
    except InvalidOperation:
        charge = Decimal("NaN")
    if not charge.is_finite() or charge < 0:
        return build_result(
            request_id,
            "unavailable",
            provider=provider,
            result_schema=result_schema,
            error_code="provider_cost_unknown",
        )
    if charge > cost_limit:
        return build_result(
            request_id,
            "unavailable",
            provider=provider,
            result_schema=result_schema,
            error_code="provider_cost_budget_exceeded",
        )
    return None
