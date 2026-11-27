"""Baseline, test-coverage and review evidence formats."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path, PurePosixPath

from .static_scan import Finding

_BASELINE = "s6-static-baseline/1"
_TEST_EVIDENCE = "s6-pytest-evidence/2"
_TEST_POLICY = "s6-test-policy/1"
_REVIEW_RECEIPT = "anti-trick-review-receipt/1"
_REVISION = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


def baseline(findings):
    counts = Counter((item.rule, item.path, item.signal) for item in findings)
    return {
        "schema": _BASELINE,
        "findings": [
            {"rule": rule, "path": path, "signal": signal, "count": count}
            for (rule, path, signal), count in sorted(counts.items())
        ],
    }


def _count_records(document):
    if not isinstance(document, dict) or document.get("schema") != _BASELINE:
        raise ValueError("unrecognized baseline schema")
    records = document.get("findings")
    if not isinstance(records, list):
        raise ValueError("baseline findings must be an array")
    result = {}
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("baseline finding must be an object")
        key = (item.get("rule"), item.get("path"), item.get("signal"))
        if not all(isinstance(value, str) and value for value in key):
            raise ValueError("missing baseline rule, path or signal")
        count = item.get("count")
        if type(count) is not int or count <= 0 or key in result:
            raise ValueError("invalid or duplicate baseline count")
        result[key] = count
    return result


def compare(current, previous) -> tuple[Finding, ...]:
    """Return every new finding instance, matched by relative path and signal."""
    old = _count_records(previous)
    new = _count_records(current)
    return tuple(
        Finding(rule, path, signal)
        for (rule, path, signal), count in sorted(new.items())
        for _ in range(max(0, count - old.get((rule, path, signal), 0)))
    )


def renamed_waivers(current, previous):
    """Surface named allowances whose binding reappears under another name."""
    before, after = previous.get("constant_shapes", {}), current.get("constant_shapes", {})
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("baseline constant_shapes must be an object")
    allowances = {
        (item["path"], item["signal"].removeprefix("allowance:"))
        for item in previous["findings"]
        if item["rule"] == "AT-10" and item["signal"].startswith("allowance:")
    }
    new_names = {(name, digest) for name, digest in after.items() if before.get(name) != digest}
    findings = []
    for path, name in sorted(allowances):
        key = f"{path}:{name}"
        if key in after or key not in before:
            continue
        for replacement, digest in sorted(new_names):
            if digest == before[key]:
                new_path, new_name = replacement.rsplit(":", 1)
                findings.append(Finding("AT-10", new_path, f"renamed-allowance:{name}->{new_name}"))
    return tuple(findings)


def changed_inventory(report, saved, root: Path) -> tuple[str, ...]:
    """Check a narrower scan cannot hide pre-existing, still-present sources."""
    before = saved.get("source_files")
    if not isinstance(before, list) or not before or not all(isinstance(item, str) for item in before):
        raise ValueError("baseline source_files must name its scanned source inventory")
    files = set(report.files)
    missing = []
    for location in before:
        candidate = (root / location).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("baseline source is outside the selected repository")
        if location not in files and candidate.is_file():
            missing.append(location)
    return tuple(sorted(set(missing)))


def _cases(values):
    if not isinstance(values, list) or not all(isinstance(node, str) and node for node in values):
        raise ValueError("test evidence node IDs must be an array of non-empty strings")
    return set(values)


def coverage(expected, executed, *, required=()) -> tuple[Finding, ...]:
    """Compare independently collected nodes with executed call-phase nodes."""
    if not isinstance(expected, dict) or not expected or not isinstance(executed, dict):
        raise ValueError("expected and executed inventories must be platform mappings")
    findings = []
    for platform in sorted(set(expected) | set(required)):
        if platform not in expected:
            findings.append(Finding("AT-12", platform, "missing-platform-inventory"))
            continue
        wanted = _cases(expected[platform])
        if not wanted:
            raise ValueError(f"test inventory is empty on {platform}")
        actual = _cases(executed.get(platform, []))
        findings.extend(Finding("AT-12", platform, f"unexecuted:{node}") for node in sorted(wanted - actual))
    return tuple(findings)


def _test_policy(value):
    if not isinstance(value, dict) or value.get("schema") != _TEST_POLICY:
        raise ValueError("unrecognized owner test policy")
    platforms = value.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise ValueError("owner test policy requires platform-specific selections")
    for platform, selection in platforms.items():
        if not isinstance(selection, dict) or not isinstance(selection.get("markexpr"), str):
            raise ValueError(f"owner test policy lacks its marker selection for {platform}")
        roots = selection.get("roots")
        if not isinstance(roots, list) or not roots or any(
            not isinstance(root, str) or not root or PurePosixPath(root).is_absolute()
            or "::" in root or "\\" in root or ".." in PurePosixPath(root).parts
            or root != PurePosixPath(root).as_posix()
            for root in roots
        ) or len(set(roots)) != len(roots):
            raise ValueError(f"owner test policy requires canonical repository-relative roots for {platform}")
    return platforms


def test_records(revision, reports, policy):
    """Combine shards without treating a collector's selected list as execution."""
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise ValueError("test evidence requires a full Git revision")
    ownership = _test_policy(policy)
    expected: dict[str, set[str]] = {}
    executed: dict[str, set[str]] = {}
    for report in reports:
        if not isinstance(report, dict) or report.get("schema") != _TEST_EVIDENCE or report.get("revision") != revision:
            raise ValueError("test evidence has an unexpected schema or revision")
        platform = report.get("platform")
        role = report.get("role")
        if not isinstance(platform, str) or not re.fullmatch(r"[a-z][a-z0-9_-]+", platform):
            raise ValueError("invalid platform in test evidence")
        if type(report.get("exit_code")) is not int or report["exit_code"] != 0:
            raise ValueError("pytest evidence requires a successful collection or execution")
        entries = _cases(report.get("node_ids"))
        if role == "inventory":
            selection = report.get("selection")
            approved = ownership.get(platform)
            if not approved or selection != {
                "roots": sorted(approved["roots"]),
                "markexpr": approved["markexpr"].strip(),
                "filters": [],
            }:
                raise ValueError(f"inventory selection does not match the owner policy for {platform}")
            if platform in expected:
                raise ValueError("only one full collection inventory per platform")
            expected[platform] = entries
        elif role == "execution":
            executed.setdefault(platform, set()).update(entries)
        else:
            raise ValueError("invalid pytest evidence role")
    return ({key: sorted(value) for key, value in expected.items()},
            {key: sorted(value) for key, value in executed.items()})


def review_errors(document, revision, *, root: Path | None = None) -> tuple[str, ...]:
    if not isinstance(document, dict):
        return ("review receipt must be an object",)
    problems = []
    if document.get("schema_version") != _REVIEW_RECEIPT:
        problems.append("unrecognized review receipt schema")
    for field in ("card_id", "repo", "adversarial_probe_run"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"missing {field}")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision) or document.get("commit_sha") != revision:
        problems.append("review revision does not match the requested version")
    verdict = document.get("verdict")
    issues = document.get("blocking_findings")
    if verdict not in ("ACCEPTED", "REJECTED") or not isinstance(issues, list):
        problems.append("invalid verdict or blocking findings")
    elif verdict == "ACCEPTED" and issues:
        problems.append("accepted review may not contain unresolved blocking findings")
    elif verdict == "REJECTED" and not issues:
        problems.append("rejected review must identify a blocking finding")
    hashes = document.get("file_sha256", {})
    if not isinstance(hashes, dict):
        return (*problems, "file_sha256 must be an object")
    if root is not None:
        root = root.resolve()
    for path, expected in hashes.items():
        if not isinstance(path, str) or not path or not isinstance(expected, str) or not _DIGEST.fullmatch(expected):
            problems.append("invalid file digest entry")
            continue
        if root is None:
            continue
        file = (root / path).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            problems.append("hashed file is missing or outside the requested root")
        else:
            with file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != expected:
                problems.append(f"file digest mismatch: {file.relative_to(root).as_posix()}")
    return tuple(problems)
