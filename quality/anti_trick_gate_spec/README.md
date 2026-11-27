# S6 engineering checks

P1-00 provides a reference for each module to use from its own repository. It reads source, offline test results and review receipts. Python 3.11+ is sufficient. Pytest is needed only when its optional recording plugin is used.

## Scan the real source

Run from the owning repository with the module's complete production source inventory. Use additional source arguments for production directories or standalone entrypoints. Record the initial inventory using the same source, storage and machine-entry selections on the unchanged integration target.

```text
python -m tools.anti_trick_gate_spec baseline --source src/company_wiki --storage src/company_wiki/catalog_storage
python -m tools.anti_trick_gate_spec scan --source src/company_wiki --storage src/company_wiki/catalog_storage --baseline ratchet_baseline.json --strict
python -m unittest discover -s tools/anti_trick_gate_spec/tests
```

Storage selection follows the repository's existing authoritative storage package. The sample is illustrative and should be corrected to the current supported package paths at adoption. Use the baseline command's JSON stdout as the versioned baseline. The strict command exits 1 for a new finding identity, increased multiplicity, or an existing source omitted from the previously covered inventory. A moved line alone preserves finding identity. A normal scan without strict produces observations with exit 0. Input or evidence errors return 2. Do not regenerate a baseline from the candidate to decide whether to accept it. Review the baseline diff against the integration target when retiring legacy cases.

AT-01 builds a dependency graph of explicit, resolved intra-repo imports including deferred imports. TYPE_CHECKING-only paths are excluded. A package initializer is a dependency when its public attributes are read, while an exact sibling submodule is a direct dependency. Import aliases follow their module and function binding scopes and respect parameter and assignment shadows. AT-02 to AT-08 locate compatibility aliases, captured forwarding bindings, inherited write and storage paths, implicit registries, deferred imports, SQL owner bypass and test/package contamination. AT-09 reports file and function size and Protocol width for architectural review. AT-10 compares the identities and membership hashes of waiver declarations and Ruff per-file ignores. AT-11 reports host-path literals, hidden exceptions and changes to diagnostic channels. Supply `--machine-entry` for owners with machine-readable stdout. Use `--log-context relative/path.py:NAME` for an independently reviewed logging-only ContextVar. AT-12 identifies fixed CI test lists in source and separately checks what CI executed.

The scanner identifies inspectable patterns. Review call ownership when an attribute receiver or callback is ambiguous. Dynamic loading, lazy package `__getattr__`, OS process behavior and many side effects need the owner behavior tests below. Report paths and symbolic findings avoid original source text.

## Record what CI ran

Keep a reviewed owner policy in the adopting repository. Declare its complete canonical test roots and marker expression for each required platform. For example:

```json
{
  "schema": "s6-test-policy/1",
  "platforms": {
    "linux": {"roots": ["tests"], "markexpr": "not live and not windows_only"},
    "windows": {"roots": ["tests"], "markexpr": "not live"}
  }
}
```

From the repository root on each required OS, collect the entire admitted suite. Use the same owner policy in the actual jobs. Supply the job's full Git revision. The first two commands show the Windows marker. For Linux use the expression from the Linux policy above. Multiple execution shards may contribute to each platform.

```text
python -m pytest -p tools.anti_trick_gate_spec.pytest_probe --s6-revision FULL_SHA --s6-evidence collected.json --collect-only -m "not live" tests/
python -m pytest -p tools.anti_trick_gate_spec.pytest_probe --s6-revision FULL_SHA --s6-evidence executed.json -m "not live" tests/
python -m tools.anti_trick_gate_spec coverage --revision FULL_SHA --policy test_policy.json --platform linux --platform windows linux-collected.json linux-executed.json windows-collected.json windows-executed.json
```

Run coverage after gathering platform-specific artifacts. The expected inventory must match the independently reviewed roots and marker expression. An individual node selector, additional keyword or deselection filter, changed working directory, or ignored path invalidates a purported full inventory. Use the repository’s auto-discovered pytest configuration for collection. Explicit alternate configuration files, collection-related INI overrides such as `python_functions`, and disabled built-in test collectors invalidate the inventory. Reporting-only options such as `console_output_style` are supported. A skipped case supplies no execution credit. Coverage complements the regular test exit status. For conditional native-only cases collect with the owner's explicit platform policy so the expected set includes the cases applicable to that OS. Missing manifests, missing platforms and incomplete node sets remain distinct failures. Use a fresh evidence output path for each invocation.

## Prove behavior at the boundary

| Check | Owner fixture |
|---|---|
| B-01 | Run a new Python process with the repository's actual lower-layer entrypoint. Wrap imports and a real operation in `block_imports` and supply the forbidden prefixes. Include a negative control whose forbidden import demonstrably triggers the finder. |
| B-02 | Inject a `protocol_double` containing only the used methods into the actual consumer. Assert public inputs, output and side effects. An attempted internal read should fail against the fixture. |
| B-03 | Build a wheel using local build dependencies and `pip wheel --no-deps --no-build-isolation --no-index`. Create a clean venv and install from an offline wheelhouse. In an empty temporary cwd with PYTHONPATH removed, execute installed entrypoints with help and an offline contract request. Verify callable results and package data and remove the scratch directory. |
| B-04 | Run the owner's real CLI using an isolated state directory and a local-provider stub. Use synthetic credentials and verify sanitized durable records, correct fallback behavior on unwritable storage, and preservation of stdout and structured stderr. Force-stop before and after dispatch, cold-read the original job from a new process and call `check_recovery` on the observed fixture counters. Verify the native Windows process tree did not create a visible console and test permitted special paths and concurrent writes. |

Use an allowlisted, fixture-only test environment and native launcher with redirected streams. Native Windows jobs should test the actual spawning entrypoint and descendants. Report the actual commands, test revision and scenario results at acceptance.

## Check review receipt provenance

```text
python -m tools.anti_trick_gate_spec review --revision FULL_SHA --root . review_receipt.json
```

The command checks the governance receipt shape and binds the stated version to the requested SHA. It verifies supplied file digests against an explicit checkout when requested. The reviewer still owns the adjudication and the real behavior evidence.
