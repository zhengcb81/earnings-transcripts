"""Provider availability settings and the local capability descriptor.

This module is intentionally stdlib-only: the supervisor, the protocol layer and
the CLI's ``--capabilities`` path must describe what a provider can do without
importing requests, BeautifulSoup, the extractor, or any provider
implementation. ``transcript_api`` re-exports these names, so
``transcript_api.ProviderSettings`` and ``transcript_api.DEFAULT_PROVIDER_SETTINGS``
stay the same objects for existing callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EXCHANGES = ("nasdaq", "nyse")


@dataclass(frozen=True)
class ProviderSettings:
    """Reviewed runtime availability; request intent cannot enable a provider."""

    motley_fool_enabled: bool = False
    fmp_enabled: bool = True


DEFAULT_PROVIDER_SETTINGS = ProviderSettings()


def provider_operation_capability(
    provider: str,
    operation: str,
    *,
    settings: ProviderSettings = DEFAULT_PROVIDER_SETTINGS,
) -> dict[str, Any]:
    """Local operation/pricing metadata; account entitlement remains unknown.

    FMP uses the existing account subscription/rate/bandwidth quota. A GET
    neither purchases nor upgrades a plan, and has zero incremental request
    charge: https://site.financialmodelingprep.com/pricing-plans . A provider
    rejection must still be reported from the actual request.
    """
    supported = (provider == "fmp" and operation == "fetch") or (
        provider == "motley_fool"
        and operation in {"fetch", "discover", "fetch-candidate"}
    )
    enabled = {
        "fmp": settings.fmp_enabled,
        "motley_fool": settings.motley_fool_enabled,
    }.get(provider, False)
    return {
        "provider": provider,
        "operation": operation,
        "enabled": enabled,
        "supported": supported,
        "incremental_cost_usd": "0" if supported else None,
        "billing_model": "subscription_quota" if provider == "fmp" else "public_http",
        "entitlement": "runtime_unknown",
        "supported_markets": ["US"] if supported else [],
        "supported_exchanges": ["auto", *EXCHANGES] if supported else [],
    }
