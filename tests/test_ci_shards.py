"""Validate that ET's CI partition preserves the complete collected suite."""

from quality.shards import pytest_collection_modifyitems, shard_for


def test_matrix_shards_are_disjoint_and_stable_under_reordering():
    heavy = [f"tests/test_f4_batch_replay.py::test_case[{index}]" for index in range(90)]
    nodes = [f"tests/test_runner.py::test_case[{index}]" for index in range(900)] + heavy
    partitions = [{node for node in nodes if shard_for(node, 3) == index} for index in range(3)]
    assert set.union(*partitions) == set(nodes)
    assert sum(map(len, partitions)) == len(nodes)
    assert partitions == [
        {node for node in reversed(nodes) if shard_for(node, 3) == index} for index in range(3)
    ]
    assert all(set(heavy) & shard for shard in partitions)


def test_sharding_declares_deselected_items_but_keeps_original_order():
    class Config:
        def __init__(self):
            self.hook = self
            self.deselected = []

        def getoption(self, name):
            return (1, 3) if name == "--et-shard" else False

        def pytest_deselected(self, *, items):
            self.deselected.extend(items)

    class Item:
        def __init__(self, nodeid):
            self.nodeid = nodeid

    original = [Item(f"test_probe.py::test_case[{index}]") for index in range(30)]
    items = original.copy()
    config = Config()
    pytest_collection_modifyitems(config, items)
    assert items == [item for item in original if shard_for(item.nodeid, 3) == 1]
    assert set(items).isdisjoint(config.deselected)
    assert set(items + config.deselected) == set(original)
