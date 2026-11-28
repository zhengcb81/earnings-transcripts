"""Engineer-only partition of the full ET test inventory for CI."""

from __future__ import annotations

import argparse
from hashlib import sha256

import pytest


def shard_for(node_id: str, shard_count: int) -> int:
    digest = sha256(node_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shard_count


def _parse_shard(value: str) -> tuple[int, int]:
    try:
        index, count = map(int, value.split("/"))
        if 0 <= index < count:
            return index, count
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("expected zero-based SHARD/COUNT")


def pytest_addoption(parser) -> None:
    parser.addoption("--et-shard", metavar="SHARD/COUNT", type=_parse_shard,
                     help="Execute the stable partition of the ET test inventory.")


def pytest_collection_modifyitems(config, items) -> None:
    shard = config.getoption("--et-shard")
    if shard is None:
        return
    if config.getoption("collectonly"):
        raise pytest.UsageError("The canonical ET inventory must not be sharded.")
    index, count = shard
    selected, other = [], []
    for item in items:
        bucket = selected if shard_for(item.nodeid, count) == index else other
        bucket.append(item)
    config.hook.pytest_deselected(items=other)
    items[:] = selected
