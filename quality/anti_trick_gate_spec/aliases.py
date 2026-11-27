"""Resolve statically visible imports using Python's lexical binding scopes."""
from __future__ import annotations

import ast

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SCOPES = (ast.Module, ast.ClassDef, *_FUNCTIONS, *_COMPREHENSIONS)


def walk(node: ast.AST, parents: tuple[ast.AST, ...] = ()):
    yield node, parents
    for child in ast.iter_child_nodes(node):
        yield from walk(child, parents + (node,))


def dotted(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return ""


def _position(node, *, completed=False):
    if completed:
        return (node.end_lineno, node.end_col_offset)
    return (node.lineno, node.col_offset)


def _targets(target):
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.List, ast.Tuple)):
        for item in target.elts:
            yield from _targets(item)
    elif isinstance(target, ast.Starred):
        yield from _targets(target.value)


def _arguments(args):
    yield from (*args.posonlyargs, *args.args, *args.kwonlyargs)
    if args.vararg is not None:
        yield args.vararg
    if args.kwarg is not None:
        yield args.kwarg


class LexicalAliases:
    """Same-scope binding order and nearest visible imports, with local shadows."""

    def __init__(self, tree):
        self.tree = tree
        self.bindings = {tree: {}}
        for node, parents in walk(tree):
            if isinstance(node, _SCOPES):
                self.bindings.setdefault(node, {})
            if not parents:
                continue
            scope = next(item for item in reversed(parents) if isinstance(item, _SCOPES))
            self._capture(node, scope)
        for scope in self.bindings.values():
            for events in scope.values():
                events.sort(key=lambda event: event[0])

    def _bind(self, scope, name, position, target=None):
        self.bindings[scope].setdefault(name, []).append((position, target))

    def _capture(self, node, scope):
        if isinstance(node, ast.Import):
            for alias in node.names:
                first = alias.name.split(".", 1)[0]
                self._bind(scope, alias.asname or first, _position(node, completed=True),
                           alias.name if alias.asname else first)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported = f"{node.module}.{alias.name}" if node.module and not node.level else None
                self._bind(scope, alias.asname or alias.name, _position(node, completed=True), imported)
        elif isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
            if hasattr(node, "name"):
                self._bind(scope, node.name, _position(node, completed=True))
            if isinstance(node, _FUNCTIONS):
                for arg in _arguments(node.args):
                    self._bind(node, arg.arg, (0, 0))
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for target in targets:
                for name in _targets(target):
                    self._bind(scope, name, _position(node, completed=True))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            for name in _targets(node.target):
                self._bind(scope, name, _position(node.target, completed=True))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self._bind(scope, node.name, _position(node))
        elif isinstance(node, ast.withitem) and node.optional_vars:
            for name in _targets(node.optional_vars):
                self._bind(scope, name, _position(node.optional_vars, completed=True))

    def resolve(self, node, parents):
        name = dotted(node)
        head, sep, rest = name.partition(".")
        if not head:
            return name
        here = _position(node)
        crossed_function = False
        for scope in reversed(parents):
            if scope not in self.bindings:
                continue
            if isinstance(scope, ast.ClassDef) and crossed_function:
                continue
            local_scope = isinstance(scope, (*_FUNCTIONS, *_COMPREHENSIONS))
            crossed_function |= local_scope
            events = self.bindings[scope].get(head)
            if not events:
                continue
            for position, target in reversed(events):
                if position <= here:
                    return target + sep + rest if target else name
            if local_scope:
                return name
        return name
