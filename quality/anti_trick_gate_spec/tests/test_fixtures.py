"""Positive and negative controls for the reusable behavior helpers."""
from __future__ import annotations

import importlib
import unittest

from tools.anti_trick_gate_spec.fixtures import block_imports, check_recovery, protocol_double


class TestFixtures(unittest.TestCase):
    def test_import_probe_really_blocks_and_then_restores(self):
        module = "encodings.ptcp154"
        with block_imports([module]):
            with self.assertRaisesRegex(ImportError, "B-01 blocked import"):
                importlib.import_module(module)
        self.assertEqual(importlib.import_module(module).__name__, module)
        with self.assertRaisesRegex(AssertionError, "fresh process"):
            with block_imports([module]):
                pass

    def test_protocol_stub_has_no_underlying_store(self):
        port = protocol_double(fetch=lambda key: key.upper())
        self.assertEqual(port.fetch("id"), "ID")
        with self.assertRaises(AttributeError):
            port._store.fetch("id")
        with self.assertRaises(ValueError):
            protocol_double(_store=lambda: None)

    def test_recovery_keeps_pre_dispatch_zero_and_post_dispatch_one(self):
        for count in (0, 1):
            before = {"job_id": "case-a", "requests": count,
                      "confirmed_cost_micros": 0, "cost_state": "unknown"}
            check_recovery(before, before.copy())
            with self.assertRaisesRegex(AssertionError, "requests"):
                check_recovery(before, {**before, "requests": count + 1})
            with self.assertRaisesRegex(AssertionError, "unknown cost"):
                check_recovery(before, {**before, "cost_state": "known"})
