"""Batch retrieval quotas shared by the parent supervisor and the worker.

The classes live here (not in scraper.py) because the retrieval worker must
enforce the batch request/byte/deadline quotas without importing the scraper
CLI, its config/logging stack, or any translator module. scraper.py re-exports
these names, so ``scraper.BatchBudget`` and friends keep their old identity.
"""

from __future__ import annotations

import time
from typing import Any


class BatchBudgetExceeded(Exception):
    """Batch-level resource cap reached; carries the named reason."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class BatchBudget:
    """Whole-run quotas checked before every HTTP request and every write."""

    def __init__(
        self,
        max_requests: int | None,
        max_seconds: float,
        max_response_bytes: int | None,
        max_output_bytes: int,
    ):
        self.max_requests = max_requests
        self.requests_left = max_requests
        self.deadline = time.monotonic() + max_seconds
        self.max_response_bytes = max_response_bytes
        self.response_bytes_left = max_response_bytes
        self.max_output_bytes = max_output_bytes
        self.output_bytes_left = max_output_bytes
        self.exhausted: str | None = None
        self.requests_used = 0
        self.response_bytes_used = 0
        # True once a worker vanished without reporting its consumption; the
        # report must then refuse to claim an exact (or zero) used amount.
        self.usage_unknown = False

    def _exhaust(self, reason: str) -> None:
        if self.exhausted is None:
            self.exhausted = reason
        raise BatchBudgetExceeded(reason)

    def remaining_seconds(self) -> float:
        return self.deadline - time.monotonic()

    def check_request(self) -> None:
        if self.exhausted is not None:
            raise BatchBudgetExceeded(self.exhausted)
        if self.requests_left is not None and self.requests_left <= 0:
            self._exhaust("request_limit")
        if self.remaining_seconds() <= 0:
            self._exhaust("batch_deadline")
        if self.response_bytes_left is not None and self.response_bytes_left <= 0:
            self._exhaust("response_bytes")

    def record_request(self) -> None:
        self.requests_used += 1
        if self.requests_left is not None:
            self.requests_left -= 1

    def constrain_response(self, limit: int) -> None:
        """Narrow this operation's remaining quota without resetting usage."""
        if self.response_bytes_left is None or limit < self.response_bytes_left:
            self.response_bytes_left = limit

    def record_response(self, size: int) -> None:
        # A yielded chunk was already consumed; account it even if it exceeds
        # permission. Negative remaining is evidence, never clamped to zero.
        self.response_bytes_used += size
        if self.response_bytes_left is not None:
            self.response_bytes_left -= size
            if self.response_bytes_left < 0:
                self._exhaust("response_bytes")
        if self.remaining_seconds() <= 0:
            self._exhaust("batch_deadline")

    def take_output(self, size: int) -> None:
        if self.exhausted is not None:
            raise BatchBudgetExceeded(self.exhausted)
        if size > self.output_bytes_left:
            self._exhaust("output_bytes")
        self.output_bytes_left -= size

    def usage_snapshot(self) -> dict[str, Any]:
        """What this budget object consumed so far (worker → parent contract)."""
        return {
            "requests_used": self.requests_used,
            "response_bytes_used": self.response_bytes_used,
            "exhausted": self.exhausted,
        }

    def apply_usage(self, usage: dict[str, Any] | None) -> None:
        """Fold a worker's reported consumption into this batch budget."""
        if usage is None:
            return
        self.requests_used += usage["requests_used"]
        self.response_bytes_used += usage["response_bytes_used"]
        if self.requests_left is not None:
            self.requests_left -= usage["requests_used"]
        if self.response_bytes_left is not None:
            self.response_bytes_left -= usage["response_bytes_used"]
        reason = usage.get("exhausted")
        if reason and self.exhausted is None:
            self.exhausted = reason

    def stop_with(self, reason: str) -> None:
        """Record a terminal reason without raising (the caller stops the batch)."""
        if self.exhausted is None:
            self.exhausted = reason

    def mark_usage_unknown(self) -> None:
        """A worker ended without trustworthy usage; never claim an exact total."""
        self.usage_unknown = True

    def report(self) -> dict[str, Any]:
        return {
            "max_requests": self.max_requests,
            "requests_used": None if self.usage_unknown else (self.requests_used),
            "max_response_bytes": self.max_response_bytes,
            "response_bytes_used": None
            if self.usage_unknown
            else (self.response_bytes_used),
            "max_output_bytes": self.max_output_bytes,
            "output_bytes_used": self.max_output_bytes - self.output_bytes_left,
            "exhausted": self.exhausted,
            "usage_unknown": self.usage_unknown,
        }


class UsageCounter:
    """One operation's actual usage, with an optional raw-response quota.

    The API/supervisor enforce time; a request can narrow response consumption
    without requiring a batch request-count quota or creating another meter.
    """

    def __init__(self):
        self.requests_used = 0
        self.response_bytes_used = 0
        self.response_bytes_left: int | None = None
        self.exhausted: str | None = None

    def constrain_response(self, limit: int) -> None:
        if self.response_bytes_left is None or limit < self.response_bytes_left:
            self.response_bytes_left = limit

    def check_request(self) -> None:
        if self.exhausted is not None:
            raise BatchBudgetExceeded(self.exhausted)
        if self.response_bytes_left is not None and self.response_bytes_left <= 0:
            self.exhausted = "response_bytes"
            raise BatchBudgetExceeded(self.exhausted)

    def record_request(self) -> None:
        self.requests_used += 1

    def record_response(self, size: int) -> None:
        self.response_bytes_used += size
        if self.response_bytes_left is not None:
            self.response_bytes_left -= size
            if self.response_bytes_left < 0:
                self.exhausted = "response_bytes"
                raise BatchBudgetExceeded(self.exhausted)

    def take_output(self, size: int) -> None:
        return None

    def usage_snapshot(self) -> dict[str, Any]:
        return {
            "requests_used": self.requests_used,
            "response_bytes_used": self.response_bytes_used,
            "exhausted": self.exhausted,
        }


class _BudgetResponse:
    """Delegating response that counts streamed bytes against the budget."""

    def __init__(self, inner: Any, budget: Any):
        self._inner = inner
        self._budget = budget

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def iter_content(self, chunk_size: int):
        remaining = self._budget.response_bytes_left
        if remaining is not None:
            chunk_size = min(chunk_size, max(1, remaining))
        for chunk in self._inner.iter_content(chunk_size=chunk_size):
            self._budget.record_response(len(chunk))
            yield chunk

    def close(self) -> None:
        self._inner.close()


class _BudgetSession:
    """Delegating session that enforces the quota before every HTTP GET."""

    def __init__(self, inner: Any, budget: Any):
        self._inner = inner
        self._budget = budget
        self.headers = inner.headers

    def constrain_response(self, limit: int) -> None:
        self._budget.constrain_response(limit)

    def get(self, url: str, **kwargs: Any) -> _BudgetResponse:
        self._budget.check_request()
        self._budget.record_request()
        return _BudgetResponse(self._inner.get(url, **kwargs), self._budget)

    def close(self) -> None:
        self._inner.close()
