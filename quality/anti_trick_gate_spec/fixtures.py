"""Small helpers for owner tests. The owner supplies its actual application entrypoints."""
from __future__ import annotations

import importlib.abc
import sys
from contextlib import contextmanager
from types import SimpleNamespace


def _contains(prefix, module):
    return module == prefix or module.startswith(prefix + ".")


class _BlockedImports(importlib.abc.MetaPathFinder):
    def __init__(self, prefixes):
        self.prefixes = tuple(prefixes)

    def find_spec(self, fullname, path=None, target=None):
        if any(_contains(prefix, fullname) for prefix in self.prefixes):
            raise ImportError(f"B-01 blocked import: {fullname}")
        return None


@contextmanager
def block_imports(prefixes):
    """Put this around the lower layer's fresh-process import and execution."""
    blocker = _BlockedImports(prefixes)
    if any(_contains(prefix, name) for prefix in blocker.prefixes for name in sys.modules):
        raise AssertionError("B-01 import fence needs a fresh process before blocked imports")
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)


def protocol_double(**methods):
    """A minimal object with no hidden access to a real service."""
    if not methods or any(name.startswith("_") or not callable(fn) for name, fn in methods.items()):
        raise ValueError("B-02 requires only named public methods")
    return SimpleNamespace(**methods)


def check_recovery(before, after):
    """Compare observed upstream request/charge counts and the recovered identity."""
    if before["job_id"] != after["job_id"]:
        raise AssertionError("B-04 recovered a different job")
    for key in ("requests", "confirmed_cost_micros"):
        if before[key] != after[key]:
            raise AssertionError(f"B-04 recovery changed {key}")
    if before["cost_state"] == "unknown" and after["cost_state"] != "unknown":
        raise AssertionError("B-04 unknown cost changed without provider reconciliation")
