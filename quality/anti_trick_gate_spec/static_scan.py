"""AST evidence for S6. No project modules are imported or executed."""
from __future__ import annotations

import ast
import hashlib
import re
import tokenize
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .aliases import LexicalAliases, dotted as _dotted, walk as _walk

_PRIVATE_STORE = {"_connect", "_read_connect", "_prepare"}
_EXPORTS = {"__all__", "MODULE_API_PROTOCOL"}
_WAIVERS = {"_LEGACY_EDGES", "_COMPATIBILITY_PORTS", "STORE_SQL_ADAPTERS", "DOMAIN_BUDGET"}
_TEST_LISTS = {"CI_TESTS", "CI_CONTRACT_CASES", "SHORT_TESTS", "DAILY_TEST_TARGETS"}
_SQL = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE|WITH|PRAGMA|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|CREATE|ALTER|DROP|ATTACH)\b", re.I)
_MACHINE_PATH = re.compile(r"(?:[a-z]:[\\/](?:users[\\/]|miniconda\b)|/users/|~/company-wiki\b|\.\.[\\/]stockwiki\b)", re.I)
_WAIVER_NAME = re.compile(r"(?:WHITELIST|ALLOWLIST|EXEMPTIONS|WAIVERS)$")


@dataclass(frozen=True, order=True)
class Finding:
    rule: str
    path: str
    signal: str
    line: int = 0


@dataclass(frozen=True)
class Scan:
    files: tuple[str, ...]
    findings: tuple[Finding, ...]
    cycles: tuple[tuple[str, ...], ...]
    metrics: dict[str, int]
    constant_shapes: dict[str, str]


def _private(name: str) -> bool:
    part = name.rsplit(".", 1)[-1]
    return part.startswith("_") and not part.startswith("__")


def _inside(path: str, scopes: tuple[str, ...]) -> bool:
    return any(path == scope or path.startswith(scope + "/") for scope in scopes)


def _scope(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("scan paths must resolve inside the selected repository")
    return path


def _inventory(root: Path, scopes: tuple[str, ...]) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    visited: set[Path] = set()
    for name in scopes:
        scope = _scope(root, name)
        if scope.is_dir():
            files = sorted(scope.rglob("*.py"))
            base = scope.parent if (scope / "__init__.py").is_file() else scope
        elif scope.is_file() and scope.suffix == ".py":
            files, base = [scope], scope.parent
        else:
            raise ValueError(f"missing Python source scope: {name}")
        for file in files:
            if file.resolve() != file or not file.is_relative_to(root):
                raise ValueError("linked Python files must be scanned at their canonical location")
            if file in visited:
                raise ValueError("source scopes overlap")
            visited.add(file)
            parts = list(file.relative_to(base).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            module = ".".join(parts)
            if module in modules:
                raise ValueError(f"ambiguous source module: {module}")
            modules[module] = file
    if not modules:
        raise ValueError("source inventory was empty")
    return modules


class _ModuleScan:
    def __init__(self, path, name, package, modules, storage, entry, context, tree, lines):
        self.path = path
        self.name = name
        self.package = package
        self.modules = modules
        self.storage = storage
        self.entry = entry
        self.context = context
        self.aliases = LexicalAliases(tree)
        self.findings = []
        self.dependencies = set()
        self.metrics = {"max_file_lines": lines, "max_function_lines": 0, "max_port_methods": 0}
        self.exports: Counter[tuple[ast.AST, str]] = Counter()
        self.constant_shapes = {}

    def show(self, rule, signal, location):
        self.findings.append(Finding(rule, self.path, signal, location.lineno))

    def test_only(self, guard, parents):
        if self.aliases.resolve(guard, parents) == "typing.TYPE_CHECKING":
            return True
        if isinstance(guard, ast.UnaryOp) and isinstance(guard.op, ast.Not):
            answer = self.test_only(guard.operand, parents)
            return None if answer is None else not answer
        return None

    def runtime(self, node, parents):
        for index, (marker, child) in enumerate(zip(parents, (*parents[1:], node))):
            if isinstance(marker, ast.If):
                guard = self.test_only(marker.test, parents[:index])
                if (guard is True and child in marker.body) or (guard is False and child in marker.orelse):
                    return False
        return True

    def _imports(self, node, parents, function):
        if isinstance(node, ast.Import):
            base_names = [alias.name for alias in node.names]
            candidates = set(base_names)
            private_aliases = []
        else:
            package = self.package.split(".") if self.package else []
            if node.level > 0:
                if node.level > len(package):
                    base = ""
                else:
                    stem = package[: len(package) - node.level + 1]
                    base = ".".join([*stem, node.module] if node.module else stem)
            else:
                base = node.module or ""
            imported = [(f"{base}.{alias.name}" if base else alias.name) for alias in node.names]
            base_names = [base, *imported]
            # Resolved members are submodules. The package initializer is not
            # their dependency unless another item is actually read from it.
            candidates = {name if name in self.modules else base for name in imported}
            private_aliases = [alias.asname for alias in node.names
                               if alias.asname and _private(alias.name) and not _private(alias.asname)]
        for name in private_aliases:
            self.show("AT-02", f"private-import-as:{name}", node)
        if not self.runtime(node, parents):
            return
        if any(name == "tests" or name.startswith("tests.") or ".tests." in name or name.endswith(".tests")
               for name in base_names):
            self.show("AT-08", "production-tests-import", node)
        if isinstance(node, ast.Import):
            for name in base_names:
                while "." in name and name not in self.modules:
                    name = name.rsplit(".", 1)[0]
                candidates.add(name)
        known = candidates & self.modules
        # Importing the current package for a submodule import is not a self edge.
        known.discard(self.name)
        if known:
            self.dependencies.update(known)
            if function:
                for name in sorted(known):
                    self.show("AT-06", f"deferred-import:{name}", node)

    def _assignment(self, node, scope, resolve):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            name = _dotted(target)
            if name and isinstance(scope, (ast.ClassDef, ast.Module)):
                if not _private(name) and _private(_dotted(value)) and name not in _EXPORTS:
                    self.show("AT-02", f"private-alias:{name}", node)
                if name in _EXPORTS:
                    self.exports[scope, name] += 1
                    if self.exports[scope, name] > 1:
                        self.show("AT-02", f"duplicate-export:{name}", node)
            if isinstance(scope, ast.Module) and isinstance(value, ast.Call):
                call = resolve(value.func)
                if call == "weakref.WeakKeyDictionary":
                    self.show("AT-05", f"shared-weak-registry:{name}", node)
                elif call == "contextvars.ContextVar" and f"{self.path}:{name}" not in self.context:
                    self.show("AT-05", f"shared-context-variable:{name}", node)
            if isinstance(scope, ast.Module) and name and name.isupper() and isinstance(value, (ast.List, ast.Set, ast.Tuple, ast.Dict, ast.Call)):
                self.constant_shapes[f"{self.path}:{name}"] = hashlib.sha256(ast.dump(value).encode()).hexdigest()
            if isinstance(scope, (ast.Module, ast.ClassDef)) and (name in _WAIVERS or _WAIVER_NAME.search(name)):
                self.show("AT-10", f"allowance:{name}", node)
                if value is not None:
                    for entry in _entries(value):
                        tag = hashlib.sha256(ast.dump(entry).encode()).hexdigest()
                        self.show("AT-10", f"allowance-member:{name}:{tag}", node)
            if isinstance(scope, ast.Module) and name in _TEST_LISTS:
                self.show("AT-12", f"fixed-test-list:{name}", node)
            if (isinstance(target, ast.Subscript) and resolve(target.value) == "sys.modules"
                    and _dotted(target.slice) == "__name__"):
                self.show("AT-08", "module-replacement-shim", node)

    def _class(self, node, resolve):
        bases = [resolve(base).rsplit(".", 1)[-1] for base in node.bases]
        methods = [member.name for member in node.body if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if "Protocol" in bases:
            self.metrics["max_port_methods"] = max(
                self.metrics["max_port_methods"], len([name for name in methods if not name.startswith("_")]))
        if any(base.endswith(("Store", "Writer")) for base in bases) and {"__getattribute__", "__getattr__"}.intersection(methods):
            self.show("AT-04", f"reader-inherits-writer:{node.name}", node)
        if not self.storage and any(base.endswith("Store") for base in bases) and _PRIVATE_STORE.intersection(methods):
            self.show("AT-04", f"private-store-overrides:{node.name}", node)
        if re.match(r"(?:Fake|Simulated|Stub)[A-Z_]", node.name):
            self.show("AT-08", f"production-test-double:{node.name}", node)

    def _function(self, node):
        size = node.end_lineno - node.lineno + 1
        self.metrics["max_function_lines"] = max(self.metrics["max_function_lines"], size)
        args = node.args
        if node.name in {"main", "run"} and any(
            _private(arg.arg) for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        ):
            self.show("AT-08", f"test-injection-parameter:{node.name}", node)
        if node.name != "invoke" or not args.vararg or not args.kwarg:
            return
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            return
        call = node.body[0].value
        if isinstance(call, ast.Call) and any(
            isinstance(arg, ast.Starred) and _dotted(arg.value) == args.vararg.arg for arg in call.args
        ) and any(kw.arg is None and _dotted(kw.value) == args.kwarg.arg for kw in call.keywords):
            self.show("AT-03", "variadic-invoke-mask", node)

    def _call(self, node, handler, resolve):
        callee = resolve(node.func)
        tail = callee.rsplit(".", 1)[-1]
        kwargs = {keyword.arg: keyword.value for keyword in node.keywords}
        if not self.storage:
            if callee == "sqlite3.connect":
                self.show("AT-07", "sqlite-connection-outside-owner", node)
            if tail in {"_connect", "_read_connect"}:
                self.show("AT-07", "private-connection-outside-owner", node)
            if tail in {"execute", "executemany", "executescript"} and node.args and _SQL.match(_text_prefix(node.args[0])):
                self.show("AT-07", "literal-sql-outside-owner", node)
        if tail == "spec_from_file_location" and len(node.args) >= 2 and any(
            isinstance(part, ast.Constant) and isinstance(part.value, str) and
            "tests" in part.value.replace(chr(92), "/").split("/")
            for part in ast.walk(node.args[1])
        ):
            self.show("AT-08", "file-loaded-from-tests", node)
        if callee == "sys.path.insert":
            self.show("AT-08", "path-injection-shim", node)
        if resolve(kwargs.get("stderr")) == "subprocess.DEVNULL" and callee.startswith("subprocess."):
            self.show("AT-11", "discarded-process-stderr", node)
        if callee == "logging.basicConfig" and resolve(kwargs.get("stream")) == "sys.stdout":
            self.show("AT-11", "logger-overwrites-machine-output", node)
        if handler and tail in {"warning", "error"} and _looks_like_logger(callee):
            exc_info = kwargs.get("exc_info")
            if not (isinstance(exc_info, ast.Constant) and exc_info.value is True):
                self.show("AT-11", "logged-error-without-stack", node)
        if handler and self.entry and callee == "print" and (
            "file" not in kwargs or resolve(kwargs.get("file")) == "sys.stdout"
        ):
            self.show("AT-11", "handler-prints-to-machine-stdout", node)

    def _raise(self, node, parents, handler, resolve):
        if isinstance(node.cause, ast.Constant) and node.cause.value is None:
            if not _preceding_log(node, parents, resolve):
                self.show("AT-11", "suppressed-cause-without-local-log", node)
        elif handler and node.exc and node.cause is None and not (
            handler.name and isinstance(node.exc, ast.Name) and node.exc.id == handler.name
        ):
            self.show("AT-11", "raise-without-explicit-cause", node)

    def inspect(self, tree):
        for node, parents in _walk(tree):
            function = _nearest(parents, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            scope = _nearest(parents, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.Module))
            handler = _actor_handler(parents)

            def resolve(value):
                return self.aliases.resolve(value, parents)

            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self._imports(node, parents, function)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                self._assignment(node, scope, resolve)
            elif isinstance(node, ast.ClassDef):
                self._class(node, resolve)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._function(node)
            elif isinstance(node, ast.Call):
                self._call(node, handler, resolve)
            elif isinstance(node, ast.Raise):
                self._raise(node, parents, handler, resolve)
            elif isinstance(node, ast.ExceptHandler):
                names = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
                broad = node.type is None or any(_dotted(name) in {"Exception", "BaseException"} for name in names)
                if broad and len(node.body) == 1 and _ignored_failure(node.body[0]):
                    self.show("AT-11", "silent-broad-except", node)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _MACHINE_PATH.search(node.value):
                self.show("AT-11", "host-path-literal", node)
            if isinstance(node, (ast.Call, ast.Dict, ast.Tuple, ast.List)) and sum(
                _forwarding_lambda(expr) for expr in _binding_values(node)
            ) >= 3:
                self.show("AT-03", "self-forwarding-binding", node)


def _nearest(parents, types):
    return next((parent for parent in reversed(parents) if isinstance(parent, types)), None)


def _actor_handler(parents):
    boundary = _nearest(parents, (ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
    return boundary if isinstance(boundary, ast.ExceptHandler) else None


def _entries(node):
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return node.elts
    if isinstance(node, ast.Dict):
        return [entry for entry in node.keys if entry is not None]
    return [node]


def _binding_values(node):
    if isinstance(node, ast.Call):
        return [*node.args, *(keyword.value for keyword in node.keywords)]
    if isinstance(node, ast.Dict):
        return node.values
    return node.elts


def _forwarding_lambda(expr):
    return (
        isinstance(expr, ast.Lambda) and isinstance(expr.body, ast.Call)
        and isinstance(expr.body.func, ast.Attribute) and _dotted(expr.body.func.value) == "self"
    )


def _text_prefix(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        return _text_prefix(node.values[0])
    return ""


def _looks_like_logger(callee):
    return any("log" in part.lower() for part in callee.split(".")[:-1])


def _preceding_log(node, parents, resolve):
    if not parents:
        return False
    for _, siblings in ast.iter_fields(parents[-1]):
        if not isinstance(siblings, list) or node not in siblings:
            continue
        return any(
            isinstance(line, ast.Expr) and isinstance(line.value, ast.Call)
            and _looks_like_logger(resolve(line.value.func))
            and resolve(line.value.func).rsplit(".", 1)[-1] in {"warning", "error", "exception"}
            for line in siblings[:siblings.index(node)]
        )
    return False


def _ignored_failure(line):
    if isinstance(line, (ast.Pass, ast.Continue)):
        return True
    if not isinstance(line, ast.Return):
        return False
    value = line.value
    return (
        value is None or isinstance(value, ast.Constant) and (value.value is None or value.value is False)
        or isinstance(value, (ast.List, ast.Tuple)) and not value.elts
        or isinstance(value, ast.Dict) and not value.keys
    )


def _components(graph):
    """Iterative Kosaraju walk also handles graphs exceeding Python's stack depth."""
    reverse = {node: set() for node in graph}
    for node, neighbours in graph.items():
        for neighbour in neighbours:
            reverse[neighbour].add(node)
    finished = []
    visited = set()
    for node in graph:
        if node in visited:
            continue
        visited.add(node)
        stack = [(node, iter(graph[node]))]
        while stack:
            active, neighbours = stack[-1]
            neighbour = next(neighbours, None)
            if neighbour is None:
                finished.append(active)
                stack.pop()
            elif neighbour not in visited:
                visited.add(neighbour)
                stack.append((neighbour, iter(graph[neighbour])))
    seen = set()
    cycles = []
    for node in reversed(finished):
        if node in seen:
            continue
        seen.add(node)
        members = [node]
        todo = [node]
        while todo:
            for neighbour in reverse[todo.pop()]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    todo.append(neighbour)
                    members.append(neighbour)
        if len(members) > 1:
            cycles.append(tuple(sorted(members)))
    return tuple(sorted(cycles))


def _ruff_allowances(root):
    for name in ("pyproject.toml", "ruff.toml", ".ruff.toml"):
        location = root / name
        if not location.is_file():
            continue
        with location.open("rb") as stream:
            data = tomllib.load(stream)
        ruff = data.get("tool", {}).get("ruff", {}) if name == "pyproject.toml" else data
        for family in (ruff, ruff.get("lint", {})):
            for field in ("per-file-ignores", "extend-per-file-ignores"):
                for rule_path, rules in family.get(field, {}).items():
                    path_key = hashlib.sha256(rule_path.encode()).hexdigest()
                    for rule in rules:
                        yield Finding("AT-10", name, f"ruff:{field}:{path_key}:{rule}")


def scan(root, sources, storage=(), entry=(), context=()) -> Scan:
    root = Path(root).resolve(strict=True)
    modules = _inventory(root, tuple(sources))
    storage = tuple(_scope(root, name).relative_to(root).as_posix() for name in storage)
    graph = {}
    paths = {}
    findings = list(_ruff_allowances(root))
    constant_shapes = {}
    metrics = {"max_file_lines": 0, "max_function_lines": 0, "max_port_methods": 0}
    for name, file in sorted(modules.items()):
        path = file.relative_to(root).as_posix()
        paths[name] = path
        try:
            with tokenize.open(file) as stream:
                source = stream.read()
            tree = ast.parse(source, filename=path)
        except (SyntaxError, UnicodeError) as error:
            raise ValueError(f"could not parse {path} at line {getattr(error, 'lineno', None)}") from None
        package = name if file.name == "__init__.py" else name.rpartition(".")[0]
        parsed = _ModuleScan(path, name, package, modules.keys(), _inside(path, storage),
                             path in entry, context, tree, len(source.splitlines()))
        parsed.inspect(tree)
        graph[name] = parsed.dependencies
        findings.extend(parsed.findings)
        constant_shapes.update(parsed.constant_shapes)
        for key, count in parsed.metrics.items():
            metrics[key] = max(metrics[key], count)
    cycles = _components(graph)
    for cycle in cycles:
        findings.append(Finding("AT-01", paths[cycle[0]], "import-cycle:" + ",".join(cycle)))
    return Scan(tuple(sorted(paths.values())), tuple(sorted(findings)), cycles, metrics, constant_shapes)
