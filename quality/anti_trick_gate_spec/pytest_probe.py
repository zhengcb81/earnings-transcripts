"""Optional pytest hook using pytest only as the calling test runner."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .evidence import _TEST_EVIDENCE

# These settings can change test discovery or the code/plugins that perform it.
_COLLECTION_INI = {
    "addopts", "collect_imported_tests", "consider_namespace_packages",
    "norecursedirs", "python_classes", "python_files", "python_functions",
    "pythonpath", "required_plugins", "testpaths",
}
_COLLECTORS = {"no:python", "no:unittest", "no:doctest"}


def pytest_addoption(parser):
    parser.addoption("--s6-evidence", metavar="FILE")
    parser.addoption("--s6-revision", metavar="SHA")


def pytest_configure(config):
    if config.getoption("--s6-evidence"):
        if not config.getoption("--s6-revision"):
            raise ValueError("--s6-evidence requires --s6-revision")
        config.pluginmanager.register(_Recorder(), "s6-evidence-recorder")


def _selection(config):
    """Describe the actual collection, independently of any declared target."""
    filters = {
        name for name in ("keyword", "deselect", "ignore", "ignore_glob", "noconftest",
                          "pyargs", "lf", "stepwise", "continue_on_collection_errors")
        if config.getoption(name, default=False)
    }
    overrides = config.getoption("override_ini", default=()) or ()
    for override in overrides:
        name = override.partition("=")[0].strip()
        if name in _COLLECTION_INI:
            filters.add(f"ini-override:{name}")
    if config.getoption("inifilename", default=None):
        filters.add("alternate-config")
    if _COLLECTORS.intersection(config.getoption("plugins", default=()) or ()):
        filters.add("disabled-test-collector")
    roots = []
    for selected in config.args:
        name, separator, _ = selected.partition("::")
        if separator:
            filters.add("node-selector")
        target = (config.invocation_params.dir / name).resolve()
        if not target.is_relative_to(config.rootpath):
            filters.add("outside-root")
        else:
            roots.append(target.relative_to(config.rootpath).as_posix())
    confcut = config.getoption("confcutdir", default=None)
    if confcut and Path(confcut).resolve() != config.rootpath:
        filters.add("confcutdir")
    if config.rootpath != config.invocation_params.dir:
        filters.add("outside-owner-working-directory")
    return {"roots": sorted(roots), "markexpr": config.getoption("markexpr").strip(),
            "filters": sorted(filters)}


class _Recorder:
    def __init__(self):
        self.executed: set[str] = set()

    def pytest_runtest_logreport(self, report):
        if report.when == "call" and not report.skipped:
            self.executed.add(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if exitstatus in (2, 3, 4, 5):
            return
        config = session.config
        collect_only = config.getoption("collectonly")
        nodes = {item.nodeid for item in session.items} if collect_only else self.executed
        result = {
            "schema": _TEST_EVIDENCE,
            "revision": config.getoption("--s6-revision"),
            "platform": {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform),
            "role": "inventory" if collect_only else "execution",
            "exit_code": int(exitstatus),
            "node_ids": sorted(nodes),
            "selection": _selection(config),
        }
        path = Path(config.getoption("--s6-evidence"))
        path.write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
