"""Code inside the view: expression syntax (B010) and script bodies (E007), checked offline.

Ignition wraps every script body in a function (transform, runAction, onChange, custom methods), so each line of
the body must be indented; an unindented line is a parse error at runtime (Q002/L001). Expressions are parsed with
a small grammar of the expression language: what it cannot parse is a warning, since the grammar here is a subset;
`and`/`or`/`not` and unbalanced delimiters are errors, because no expression accepts them.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from pdk.diagnostics import Diagnostic, join, make
from pdk.validate.context import Context
from pdk.validate.refs import binding_expressions
from pdk.validate.tree import Node

WORD_OPERATORS = {"and": "&&", "or": "||", "not": "!"}
KEYWORD_BINARY = ("xor", "like")
CONSTANTS = ("true", "false", "null", "none")
# Longest first, so `<=` is not read as `<` `=`.
BINARY = ("||", "&&", "==", "!=", "<>", "<=", ">=", "<<", ">>", "|", "&", "=", "<", ">", "+", "-", "*", "/",
          "%", "^")
UNARY = ("-", "!", "+")
_NUMBER = re.compile(r"0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SCRIPT_GROUPS = ("customMethods", "messageHandlers", "extensionFunctions")


class _Syntax(Exception):
    def __init__(self, message: str, position: int, certain: bool = False):
        super().__init__(message)
        self.position = position
        self.certain = certain  # wrong for any Ignition expression, not just outside this grammar


def check(ctx: Context) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for node, owner_pointer, config in _prop_configs(ctx):
        where = node.path if node else ""
        for path, entry in config.items():
            if not isinstance(entry, dict):
                continue
            base = join(owner_pointer, path)
            binding = entry.get("binding")
            for text, pointer, _ in binding_expressions(binding, join(base, "binding")):
                out += _expression(text, pointer, where)
            if isinstance(binding, dict):
                for i, transform in enumerate(binding.get("transforms") or []):
                    if isinstance(transform, dict) and transform.get("type") == "script":
                        out += _script(transform.get("code"), join(base, "binding", "transforms", i, "code"), where)
            on_change = entry.get("onChange")
            if isinstance(on_change, dict):
                out += _script(on_change.get("script"), join(base, "onChange", "script"), where)
    for node, events, pointer in _event_owners(ctx):
        where = node.path if node else ""
        for script, spointer in _event_scripts(events, pointer):
            out += _script(script, spointer, where)
    for node in ctx.nodes:
        scripts = node.component.get("scripts")
        if isinstance(scripts, dict):
            for group in SCRIPT_GROUPS:
                for i, item in enumerate(scripts.get(group) or []):
                    if isinstance(item, dict):
                        out += _script(item.get("script"), join(node.pointer, "scripts", group, i, "script"),
                                       node.path)
    return out


# --- where the code is ------------------------------------------------------------------------


def _prop_configs(ctx: Context) -> Iterator[tuple[Node | None, str, dict[str, Any]]]:
    if isinstance(ctx.view.get("propConfig"), dict):
        yield None, "/propConfig", ctx.view["propConfig"]
    for node in ctx.nodes:
        config = node.component.get("propConfig")
        if isinstance(config, dict):
            yield node, join(node.pointer, "propConfig"), config


def _event_owners(ctx: Context) -> Iterator[tuple[Node | None, Any, str]]:
    yield None, ctx.view.get("events"), "/events"
    for node in ctx.nodes:
        yield node, node.component.get("events"), join(node.pointer, "events")


def _event_scripts(events: Any, base: str) -> Iterator[tuple[Any, str]]:
    if not isinstance(events, dict):
        return
    for group, handlers in events.items():
        if not isinstance(handlers, dict):
            continue
        for name, handler in handlers.items():
            items = handler if isinstance(handler, list) else [handler]
            for i, item in enumerate(items):
                pointer = join(base, group, name, i) if isinstance(handler, list) else join(base, group, name)
                if isinstance(item, dict) and item.get("type") == "script" and isinstance(item.get("config"), dict):
                    yield item["config"].get("script"), join(pointer, "config", "script")


# --- scripts ----------------------------------------------------------------------------------


def _script(text: Any, pointer: str, where: str) -> list[Diagnostic]:
    if not isinstance(text, str):
        return []
    lines = text.split("\n")
    for number, (line, starts) in enumerate(zip(lines, _logical_starts(lines), strict=True), 1):
        stripped = line.strip()
        if starts and stripped and not stripped.startswith("#") and not line[0].isspace():
            return [make("E007", "error", pointer, where,
                         f"script line {number} is not indented: {_short(line)!r}",
                         "Ignition wraps the body in a function: start every line with a tab "
                         '("\\tsystem.perspective.print(value)"), and nest blocks with more tabs')]
    return []


def _logical_starts(lines: list[str]) -> list[bool]:
    """For each physical line, whether it begins a logical Python line: not inside a triple-quoted
    string, not inside brackets, not after a backslash continuation. Single-line strings end at the
    line break; a '#' outside strings ends the code of its line."""
    starts: list[bool] = []
    triple = ""  # closing delimiter of the triple-quoted string that is still open, if any
    depth = 0
    continued = False
    for raw in lines:
        line = raw.rstrip("\r")
        starts.append(not triple and depth == 0 and not continued)
        continued = False
        i, n = 0, len(line)
        while i < n:
            ch = line[i]
            if triple:
                if line.startswith(triple, i):
                    triple = ""
                    i += 3
                elif ch == "\\":
                    i += 2
                else:
                    i += 1
            elif ch == "#":
                break
            elif ch in "'\"":
                if line.startswith(ch * 3, i):
                    triple = ch * 3
                    i += 3
                else:
                    i += 1
                    while i < n and line[i] != ch:
                        i += 2 if line[i] == "\\" else 1
                    i += 1
            else:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth = max(0, depth - 1)
                elif ch == "\\" and i == n - 1:
                    continued = True
                i += 1
    return starts


# --- expressions ------------------------------------------------------------------------------


def _expression(text: str, pointer: str, where: str) -> list[Diagnostic]:
    try:
        _Parser(text).parse()
    except _Syntax as exc:
        excerpt = _short(text[max(0, exc.position - 20): exc.position + 20])
        if exc.certain:
            return [make("B010", "error", pointer, where, f"expression syntax: {exc}", _hint(str(exc)),
                         f"near: {excerpt}")]
        return [make("B010", "warning", pointer, where, f"expression not understood offline: {exc}",
                     "check it in the Designer or with pdk_inspect (a failing expression is Q002); "
                     "logical operators are && || !, functions are name(args), references go in {braces}",
                     f"near: {excerpt}")]
    return []


def _hint(message: str) -> str:
    for word, symbol in WORD_OPERATORS.items():
        if f"'{word}'" in message:
            return f"the expression language has no '{word}': use {symbol} (&& and, || or, ! not)"
    return "close every (, [, { and quote; references go in {braces}, strings in quotes"


def _short(text: str, width: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


class _Parser:
    """Recursive descent over the token stream; operator precedence does not matter for a syntax check."""

    def __init__(self, text: str):
        self.text = text
        self.tokens = list(_tokens(text))
        self.i = 0

    def parse(self) -> None:
        if not self.tokens:
            raise _Syntax("empty expression", 0)
        self._expr()
        if self.i < len(self.tokens):
            kind, value, position = self.tokens[self.i]
            raise _Syntax(f"unexpected {value!r}", position, certain=kind == "close")

    def _peek(self) -> tuple[str, str, int] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _take(self) -> tuple[str, str, int]:
        token = self._peek()
        if token is None:
            raise _Syntax("the expression ends too early", len(self.text), certain=True)
        self.i += 1
        return token

    def _expr(self) -> None:
        self._operand()
        while (token := self._peek()) and (token[0] == "op" and token[1] in BINARY
                                           or token[0] == "word" and token[1].lower() in KEYWORD_BINARY):
            self.i += 1
            self._operand()

    def _operand(self) -> None:
        kind, value, position = self._take()
        if kind == "op" and value in UNARY:
            self._operand()
            return
        if kind in ("number", "string", "ref"):
            pass
        elif kind == "word":
            following = self._peek()
            if following and following[1] == "(":
                self.i += 1
                self._arguments()
            elif value.lower() not in CONSTANTS:
                raise _Syntax(f"bare word {value!r} (functions are name(args), references go in braces)",
                              position)
        elif value == "(":
            self._expr()
            self._expect(")", position)
        else:
            raise _Syntax(f"expected a value, got {value!r}", position)
        while (token := self._peek()) and token[1] == "[":
            self.i += 1
            self._expr()
            self._expect("]", token[2])

    def _arguments(self) -> None:
        if (token := self._peek()) and token[1] == ")":
            self.i += 1
            return
        while True:
            self._expr()
            kind, value, position = self._take()
            if value == ")":
                return
            if value != ",":
                raise _Syntax(f"expected , or ) in the arguments, got {value!r}", position)

    def _expect(self, closer: str, opened: int) -> None:
        token = self._peek()
        if token is None or token[1] != closer:
            raise _Syntax(f"missing {closer!r} for the one opened at {opened}",
                          token[2] if token else len(self.text), certain=token is None)
        self.i += 1


def _tokens(text: str) -> Iterator[tuple[str, str, int]]:
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif c in "\"'":
            end = i + 1
            while end < n and text[end] != c:
                end += 2 if text[end] == "\\" else 1
            if end >= n:
                raise _Syntax("unterminated string", i, certain=True)
            yield "string", text[i:end + 1], i
            i = end + 1
        elif c == "{":
            end = text.find("}", i)
            if end < 0:
                raise _Syntax("unterminated {reference}", i, certain=True)
            yield "ref", text[i:end + 1], i
            i = end + 1
        elif c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            match = _NUMBER.match(text, i)
            assert match
            yield "number", match.group(), i
            i = match.end()
        elif c.isalpha() or c == "_":
            match = _IDENT.match(text, i)
            assert match
            word = match.group()
            if word.lower() in WORD_OPERATORS:
                after = text[match.end():].lstrip()
                if not after.startswith("("):
                    raise _Syntax(f"'{word}' is not an operator", i, certain=True)
            yield "word", word, i
            i = match.end()
        elif c in "([":
            yield "open", c, i
            i += 1
        elif c in ")]":
            yield "close", c, i
            i += 1
        elif c == "}":
            raise _Syntax("unexpected '}'", i, certain=True)
        elif c == ",":
            yield "comma", c, i
            i += 1
        else:
            op = next((o for o in BINARY + UNARY if text.startswith(o, i)), None)
            if op is None:
                raise _Syntax(f"unexpected character {c!r}", i)
            yield "op", op, i
            i += len(op)
