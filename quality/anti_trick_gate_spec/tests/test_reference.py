"""Contract and counterexample tests for the standalone S6 reference gate."""
from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from tools.anti_trick_gate_spec.evidence import (
    baseline, changed_inventory, compare, coverage, renamed_waivers, review_errors,
)
from tools.anti_trick_gate_spec.static_scan import scan


class StaticChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.put("src/pkg/__init__.py", "")

    def put(self, path, body):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(body), encoding="utf-8")

    def run_scan(self, *, entry=(), storage=(), context=()):
        return scan(self.root, ("src",), storage, entry, context)

    def check(self, rule, body, **kwargs):
        self.put("src/pkg/worker.py", body)
        result = self.run_scan(**kwargs)
        self.assertIn(rule, {item.rule for item in result.findings}, result.findings)
        self.assertEqual(result.files, ("src/pkg/__init__.py", "src/pkg/worker.py"))

    def test_real_relative_deferred_cycle_and_type_only_positive(self):
        self.put("src/pkg/one.py", "from typing import TYPE_CHECKING as TC\n"
                 "if not TC:\n    from . import two\nelse:\n    from . import unused\n")
        self.put("src/pkg/two.py", "def fn():\n    from . import one\n")
        self.put("src/pkg/unused.py", "from . import one\n")
        result = self.run_scan()
        self.assertEqual(result.cycles, (("pkg.one", "pkg.two"),))
        self.assertEqual({x.rule for x in result.findings}, {"AT-01", "AT-06"})

    def test_package_public_reexport_and_relative_sibling_have_no_cycle(self):
        self.put("src/pkg/__init__.py", "from .service import read\n")
        self.put("src/pkg/service.py", "from . import helpers\n"
                 "def read():\n    return helpers.parse()\n")
        self.put("src/pkg/helpers.py", "def parse():\n    return 'ok'\n")
        self.assertFalse(self.run_scan().cycles)
        self.put("src/pkg/helpers.py", "from .service import read\n")
        self.assertEqual(self.run_scan().cycles, (("pkg.helpers", "pkg.service"),))

    def test_scoped_import_aliases_and_using_shadowed_binding(self):
        self.put("src/pkg/worker.py", """\
            import sqlite3 as root_db
            def uncaught(path):
                from sqlite3 import connect as open_store
                def nested():
                    return open_store(path)
                return nested()
            def shadowed(root_db, path):
                return root_db.connect(path)
            def reassigned(factory, path):
                import sqlite3 as driver
                driver = factory
                return driver.connect(path)
            def local_module(path):
                import sqlite3 as driver
                return driver.connect(path)
            """)
        sqlite_calls = [x for x in self.run_scan().findings if x.rule == "AT-07"]
        self.assertEqual([(x.line, x.signal) for x in sqlite_calls],
                         [(5, "sqlite-connection-outside-owner"), (15, "sqlite-connection-outside-owner")])

    def test_public_alias_and_private_import(self):
        self.check("AT-02", """\
        from .owner import _old as published
        public = _private
        __all__ = ["published"]
        __all__ = ["public"]
        """)
        self.assertEqual(len(self.run_scan().findings), 3)

    def test_forwarding_lambdas_and_vararg_mask(self):
        self.check("AT-03", """\
        class Service:
            def bind(self):
                return Ports(a=lambda x: self.a(x), b=lambda x: self.b(x),
                             c=lambda x: self.c(x))
        def invoke(*args, **kwargs):
            return hidden(*args, **kwargs)
        """)
        self.assertEqual(len(self.run_scan().findings), 2)

    def test_write_inheritance_and_storage_private_overrides(self):
        self.check("AT-04", """\
        class ReadOnlyWorkView(Writer):
            def __getattribute__(self, name):
                return None
        class ReadStore(OwnerStore):
            def _connect(self):
                return None
        """)
        self.assertEqual(len(self.run_scan().findings), 2)
        self.assertEqual(len(self.run_scan(storage=("src/pkg/worker.py",)).findings), 1)

    def test_registry_and_allowed_log_context(self):
        self.check("AT-05", """\
        from weakref import WeakKeyDictionary as Registry
        import contextvars as cv
        HANDLES = Registry()
        LOG_RUN = cv.ContextVar("run")
        ACCESS = cv.ContextVar("access")
        """, context=("src/pkg/worker.py:LOG_RUN",))
        self.assertEqual(len(self.run_scan(context=("src/pkg/worker.py:LOG_RUN",)).findings), 2)

    def test_sql_bypass(self):
        self.check("AT-07", """\
        from sqlite3 import connect as connect_store
        connection = connect_store(path)
        data = external._read_connect().execute("SELECT id FROM collection")
        """)
        self.assertEqual(len(self.run_scan().findings), 3)
        self.assertFalse(self.run_scan(storage=("src/pkg/worker.py",)).findings)

    def test_test_infiltration(self):
        self.check("AT-08", """\
        from importlib.util import spec_from_file_location as load
        import sys as system
        from tests.fixtures import fixture
        load("cases", Path("tests") / "cases.py")
        class SimulatedRunner:
            pass
        def main(_fixture=None):
            pass
        system.path.insert(0, "/temp")
        system.modules[__name__] = replacement
        """)
        self.assertEqual(len(self.run_scan().findings), 6)

    def test_legitimate_protocol_and_platform_cases(self):
        self.put("src/pkg/worker.py", """\
            from typing import Protocol, TYPE_CHECKING
            import logging
            if TYPE_CHECKING:
                from . import one
            class ReadPort(Protocol):
                def get(self, key: str) -> str: ...
            def fetch(port: ReadPort, key):
                try:
                    return port.get(key)
                except LookupError as error:
                    logging.exception("lookup")
                    raise ValueError("invalid key") from error
            labels = sorted(values, key=lambda x: x.name)
            """)
        self.put("src/pkg/one.py", "from . import worker\n")
        report = self.run_scan()
        self.assertFalse(report.findings)
        self.assertGreater(report.metrics["max_port_methods"], 0)
        self.assertGreater(report.metrics["max_file_lines"], 0)

    def test_whitelist_rename_replacement_or_growth(self):
        self.put("src/pkg/worker.py", "_LEGACY_EDGES = {\"before\"}\n")
        before = baseline(self.run_scan().findings)
        for source in ('_COMPATIBILITY_PORTS = {"before"}', '_LEGACY_EDGES = {"after"}',
                       '_LEGACY_EDGES = {"before", "after"}'):
            with self.subTest(source=source):
                self.put("src/pkg/worker.py", source)
                current = baseline(self.run_scan().findings)
                self.assertEqual({x.rule for x in compare(current, before)}, {"AT-10"})

    def test_whitelist_renamed_to_unmatched_constant(self):
        self.put("src/pkg/worker.py", '_LEGACY_EDGES = {"same-rule"}\n')
        result = self.run_scan()
        before = {**baseline(result.findings), "constant_shapes": result.constant_shapes}
        self.put("src/pkg/worker.py", 'COMPAT_MODE = {"same-rule"}\n')
        result = self.run_scan()
        current = {**baseline(result.findings), "constant_shapes": result.constant_shapes}
        self.assertEqual(len(renamed_waivers(current, before)), 1)

    def test_ruff_extra_ignore_is_a_new_waiver(self):
        self.put("pyproject.toml", """
            [tool.ruff.lint.per-file-ignores]
            "src/pkg/worker.py" = ["E402"]
        """)
        before = baseline(self.run_scan().findings)
        self.put("pyproject.toml", """
            [tool.ruff.lint.per-file-ignores]
            "src/pkg/worker.py" = ["E402", "F401"]
        """)
        current = baseline(self.run_scan().findings)
        self.assertEqual(len(compare(current, before)), 1)

    def test_still_present_production_file_cannot_leave_scan_scope(self):
        self.put("src/pkg/public.py", "")
        self.put("src/pkg/owner.py", "")
        before = {"source_files": list(self.run_scan().files)}
        narrowed = scan(self.root, ("src/pkg/public.py",))
        self.assertEqual(changed_inventory(narrowed, before, self.root),
                         ("src/pkg/__init__.py", "src/pkg/owner.py"))

    def test_secret_redacted_and_diagnostics(self):
        self.check("AT-11", r'''
        import logging
        import subprocess as runner
        import sys
        location = "C:\\Users\\sensitive\\path"
        logging.basicConfig(stream=sys.stdout)
        runner.run(command, stderr=runner.DEVNULL)
        def main():
            try:
                invoke()
            except Exception:
                print("failed")
                raise RuntimeError() from None
        def another():
            try:
                invoke()
            except BaseException:
                return None
        def finish():
            try:
                invoke()
            except ValueError:
                logging.warning("lost")
                raise RuntimeError()
        ''', entry=("src/pkg/worker.py",))
        issues = self.run_scan(entry=("src/pkg/worker.py",)).findings
        self.assertEqual(len(issues), 8)
        self.assertNotIn("sensitive", repr(issues))

    def test_source_cannot_be_empty(self):
        with self.assertRaises(ValueError):
            scan(self.root, ("other",))

    def test_legacy_entry_listing_reported(self):
        self.check("AT-12", 'CI_TESTS = ("tests/test_only.py",)\n')

    def test_baseline_allows_old_identity_at_new_line(self):
        self.put("src/pkg/worker.py", "public = _owner\n")
        old = baseline(self.run_scan().findings)
        self.put("src/pkg/worker.py", "\n\npublic = _owner\n")
        self.assertFalse(compare(baseline(self.run_scan().findings), old))


class EvidenceChecks(unittest.TestCase):
    def test_ci_coverage_needs_native_executed_case(self):
        files = ["tests/test_owner.py::test_owner", "tests/test_http.py::test_http"]
        expected = {"linux": files, "windows": files}
        actual = {"linux": files, "windows": files[:1]}
        self.assertEqual(len(coverage(expected, actual)), 1)
        self.assertFalse(coverage(expected, expected))

    def test_ci_coverage_rejects_empty_inventory(self):
        with self.assertRaises(ValueError):
            coverage({"linux": []}, {"linux": []})

    def test_receipt_rejects_wrong_version_or_accepted_open_issue(self):
        receipt = {"schema_version": "anti-trick-review-receipt/1", "card_id": "P1-00",
                   "repo": "example", "commit_sha": "0" * 40, "verdict": "ACCEPTED",
                   "adversarial_probe_run": "Test with a failing negative control",
                   "blocking_findings": [], "file_sha256": {}}
        self.assertFalse(review_errors(receipt, "0" * 40))
        self.assertTrue(review_errors(receipt, "1" * 40))
        receipt["blocking_findings"] = ["Boundary was bypassed"]
        self.assertTrue(review_errors(receipt, "0" * 40))


if __name__ == "__main__":
    unittest.main()
