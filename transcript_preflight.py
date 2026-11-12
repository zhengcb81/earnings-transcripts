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

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,14}$")


class _InvalidRequest(Exception):
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
    if not required.issubset(value) or set(value) - required - {
        "max_cost_usd",
        "max_response_bytes",
    }:
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
    exchange = value["exchange"]
    if not isinstance(exchange, str):
        raise _InvalidRequest()
    exchange = exchange.strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", exchange) is None:
        raise _InvalidRequest()
    if (
        type(value["fiscal_year"]) is not int
        or not 1990 <= value["fiscal_year"] <= 2100
    ):
        raise _InvalidRequest()
    if type(value["fiscal_quarter"]) is not int or value["fiscal_quarter"] not in (
        1,
        2,
        3,
        4,
    ):
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
    if (
        type(value["timeout_seconds"]) is not int
        or value["timeout_seconds"] < 1
    ):
        raise _InvalidRequest()
    if (
        type(value["max_body_bytes"]) is not int
        or value["max_body_bytes"] <= 0
    ):
        raise _InvalidRequest()
    if "max_response_bytes" in value and (
        type(value["max_response_bytes"]) is not int or value["max_response_bytes"] <= 0
    ):
        raise _InvalidRequest()
    normalized = dict(value)
    fee = value.get("max_cost_usd", "0")
    if not isinstance(fee, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", fee
    ):
        raise _InvalidRequest()
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
        normalized = _validate_request(request)
    except _InvalidRequest:
        return _base_result(request_id, "invalid_request", result_schema=result_schema,
                            error_code="request_schema")
    if not normalized["download_authorized"]:
        return _base_result(request_id, "not_authorized", provider=normalized["provider"],
                            result_schema=result_schema)
    return _provider_gate(normalized["request_id"], normalized["provider"], operation="fetch",
                          result_schema=result_schema, settings=settings,
                          cost_limit=normalized["cost_limit"], exchange=normalized["exchange"])


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
    cost_limit: Decimal = Decimal("0"),
    exchange: str | None = None,
) -> dict[str, Any] | None:
    """Apply one provider configuration before creating an HTTP session."""
    if provider not in ("motley_fool", "fmp"):
        return _base_result(
            request_id,
            "unsupported",
            provider=provider,
            result_schema=result_schema,
            error_code="provider_unavailable",
        )
    capability = provider_operation_capability(provider, operation, settings=settings)
    if not capability["enabled"]:
        return _base_result(
            request_id,
            "unavailable",
            provider=provider,
            result_schema=result_schema,
            error_code="provider_disabled",
        )
    if not capability["supported"]:
        return _base_result(
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
        return _base_result(
            request_id, "unsupported", provider=provider, result_schema=result_schema,
            error_code="unsupported_market" if exchange == "hkex" else "unsupported_exchange",
        )
    fee = capability.get("incremental_cost_usd")
    try:
        charge = Decimal(fee) if isinstance(fee, str) else Decimal("NaN")
    except InvalidOperation:
        charge = Decimal("NaN")
    if not charge.is_finite() or charge < 0:
        return _base_result(
            request_id,
            "unavailable",
            provider=provider,
            result_schema=result_schema,
            error_code="provider_cost_unknown",
        )
    if charge > cost_limit:
        return _base_result(
            request_id,
            "unavailable",
            provider=provider,
            result_schema=result_schema,
            error_code="provider_cost_budget_exceeded",
        )
    return None
