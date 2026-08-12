#!/usr/bin/env python3
"""Transpile the widget's Jinja2 template into C.

`custom_components/thermiq_mqtt/frontend/heatpump_widget.j2` is the animated
heat-pump schematic the Home Assistant card renders. The bridge renders the
same file, so the two never diverge - but C has no Jinja2, and shipping a
template interpreter to evaluate a fixed asset at runtime would be both larger
and slower than compiling it once.

So the template is compiled here, at build time, into a C function whose static
text is memcpy'd and whose handful of expressions are C arithmetic. Entity ids
are resolved to array indices, so no string lookup survives into the render.

This handles exactly the subset of Jinja the template uses. Anything outside
that subset is an error rather than a guess - if the template grows a feature,
this script must grow with it, and it will say so loudly.

Correctness is not argued, it is measured: `tests/test_widget.c` renders the
generated code and `codegen/render_reference.py` renders the same states
through real Jinja2, and the build fails unless the bytes are identical.

Usage:
    python3 standalone/codegen/gen_widget.py [--check]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "custom_components/thermiq_mqtt/frontend/heatpump_widget.j2"
OUTPUT = REPO / "standalone/src/widget_gen.c"

# Entity ids the template reads that this integration does not provide. They
# always read as unknown, exactly as they would in a Home Assistant without
# those entities.
FOREIGN_ENTITIES = {"binary_sensor.pool_heating_active"}
# The template's demo switch. The bridge drives it from THERMIQ_DEMO.
DEMO_ENTITY = "input_boolean.hpviz_demo"

# Value types the compiler tracks. DYN is a tagged value, used only where a
# static type cannot be pinned down (macro parameters, and `63 if demo else
# t_sup` which is an int on one branch and a string on the other).
INT, DBL, BOOL, STR, DYN = "int", "dbl", "bool", "str", "dyn"

C_TYPE = {INT: "long long", DBL: "double", BOOL: "bool", STR: "const char *", DYN: "jval"}


class TemplateError(Exception):
    pass


# --------------------------------------------------------------------------
# Lexing the template into text / statement / expression nodes
# --------------------------------------------------------------------------


@dataclass
class Text:
    text: str


@dataclass
class Output:
    expr: str


@dataclass
class Statement:
    keyword: str
    rest: str


# Only openers are scanned for. The template's CSS contains "}}" and "%}"
# sequences that are plain text, and Jinja treats them that way too.
TAG = re.compile(r"\{\{-?|\{%-?|\{#-?")


def lex(source: str) -> list:
    """Split the template into nodes, honouring {%- -%} whitespace control."""
    nodes: list = []
    position = 0
    pending_text: list[str] = []

    def flush(strip_right: bool) -> None:
        text = "".join(pending_text)
        pending_text.clear()
        if strip_right:
            text = text.rstrip()
        if text:
            nodes.append(Text(text))

    while position < len(source):
        match = TAG.search(source, position)
        if not match:
            pending_text.append(source[position:])
            break
        pending_text.append(source[position : match.start()])
        opener = match.group(0)
        if opener.startswith("{{"):
            close, kind = "}}", "output"
        elif opener.startswith("{%"):
            close, kind = "%}", "statement"
        elif opener.startswith("{#"):
            close, kind = "#}", "comment"
        else:
            raise TemplateError(f"unexpected {opener!r} at offset {match.start()}")

        flush(strip_right=opener.endswith("-"))

        end = source.find(close, match.end())
        # A trailing '-' before the closer is whitespace control, not content
        trimmed_end = source.find("-" + close, match.end())
        strip_next = trimmed_end != -1 and trimmed_end + 1 == end
        if end == -1:
            raise TemplateError(f"unterminated {opener!r} at offset {match.start()}")
        body = source[match.end() : trimmed_end if strip_next else end].strip()
        position = end + len(close)

        if kind == "output":
            nodes.append(Output(body))
        elif kind == "statement":
            keyword, _, rest = body.partition(" ")
            nodes.append(Statement(keyword.strip(), rest.strip()))

        if strip_next:
            # Swallow the whitespace that follows the tag
            while position < len(source) and source[position] in " \t\r\n":
                position += 1

    flush(strip_right=False)
    return nodes


# --------------------------------------------------------------------------
# Expression parsing
# --------------------------------------------------------------------------

TOKEN = re.compile(
    r"""
    \s*(
        \*\*                     |
        ==|!=|<=|>=|<|>          |
        [-+*/()\[\],|]           |
        '(?:[^'\\]|\\.)*'        |
        "(?:[^"\\]|\\.)*"        |
        \d+\.\d+|\d+             |
        [A-Za-z_][A-Za-z_0-9]*
    )
    """,
    re.VERBOSE,
)


def tokenize(expression: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    while position < len(expression):
        match = TOKEN.match(expression, position)
        if not match:
            if expression[position:].strip() == "":
                break
            raise TemplateError(f"cannot tokenize {expression[position:]!r}")
        tokens.append(match.group(1))
        position = match.end()
    return tokens


@dataclass
class Value:
    """A compiled expression: the C code for it and its static type."""

    code: str
    type: str

    def as_double(self, default: str = "0.0") -> str:
        if self.type == DBL:
            return self.code
        if self.type == INT:
            return f"(double)({self.code})"
        if self.type == STR:
            return f"str_float({self.code}, {default})"
        if self.type == DYN:
            return f"jv_float({self.code}, {default})"
        raise TemplateError(f"cannot use {self.type} as a number: {self.code}")

    def as_bool(self) -> str:
        if self.type == BOOL:
            return self.code
        raise TemplateError(f"cannot use {self.type} as a condition: {self.code}")

    def as_dyn(self) -> str:
        return {
            DYN: lambda: self.code,
            STR: lambda: f"jv_s({self.code})",
            INT: lambda: f"jv_i({self.code})",
            DBL: lambda: f"jv_d({self.code})",
        }.get(self.type, lambda: self._unsupported())()

    def _unsupported(self):
        raise TemplateError(f"cannot pass {self.type} to a macro: {self.code}")


class Compiler:
    """Compiles the template's expression subset to C."""

    def __init__(self) -> None:
        self.entities: list[str] = []
        self.scopes: list[dict[str, str]] = [{}]
        self.macros: dict[str, str] = {}

    # -- entities --------------------------------------------------------
    def entity_index(self, entity_id: str) -> int:
        if entity_id not in self.entities:
            self.entities.append(entity_id)
        return self.entities.index(entity_id)

    # -- scopes ----------------------------------------------------------
    def declare(self, name: str, value_type: str) -> None:
        self.scopes[-1][name] = value_type

    def lookup(self, name: str) -> str | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    # -- entry point -----------------------------------------------------
    def compile(self, expression: str) -> Value:
        self.tokens = tokenize(expression)
        self.position = 0
        value = self.parse_ternary()
        if self.position != len(self.tokens):
            raise TemplateError(
                f"trailing tokens in {expression!r}: {self.tokens[self.position:]}"
            )
        return value

    # -- token helpers ---------------------------------------------------
    def peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> str:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def accept(self, token: str) -> bool:
        if self.peek() == token:
            self.position += 1
            return True
        return False

    def expect(self, token: str) -> None:
        if not self.accept(token):
            raise TemplateError(f"expected {token!r}, found {self.peek()!r}")

    # -- grammar ---------------------------------------------------------
    def parse_ternary(self) -> Value:
        value = self.parse_or()
        if not self.accept("if"):
            return value
        condition = self.parse_or()
        if not self.accept("else"):
            # Jinja yields Undefined, which renders as an empty string
            if value.type != STR:
                raise TemplateError("an if without else must produce a string")
            return Value(f"({condition.as_bool()} ? {value.code} : \"\")", STR)
        other = self.parse_ternary()
        return self.merge(condition, value, other)

    def merge(self, condition: Value, when_true: Value, when_false: Value) -> Value:
        if when_true.type == when_false.type:
            return Value(
                f"({condition.as_bool()} ? {when_true.code} : {when_false.code})",
                when_true.type,
            )
        if {when_true.type, when_false.type} <= {INT, DBL}:
            return Value(
                f"({condition.as_bool()} ? {when_true.as_double()} : "
                f"{when_false.as_double()})",
                DBL,
            )
        return Value(
            f"({condition.as_bool()} ? {when_true.as_dyn()} : {when_false.as_dyn()})",
            DYN,
        )

    def parse_or(self) -> Value:
        value = self.parse_and()
        while self.peek() == "or":
            self.take()
            right = self.parse_and()
            value = Value(f"({value.as_bool()} || {right.as_bool()})", BOOL)
        return value

    def parse_and(self) -> Value:
        value = self.parse_not()
        while self.peek() == "and":
            self.take()
            right = self.parse_not()
            value = Value(f"({value.as_bool()} && {right.as_bool()})", BOOL)
        return value

    def parse_not(self) -> Value:
        if self.accept("not"):
            value = self.parse_not()
            return Value(f"(!{value.as_bool()})", BOOL)
        return self.parse_comparison()

    def parse_comparison(self) -> Value:
        left = self.parse_additive()
        operator = self.peek()
        if operator not in ("==", "!=", "<", "<=", ">", ">="):
            return left
        self.take()
        right = self.parse_additive()
        if left.type == STR and right.type == STR:
            comparison = f"strcmp({left.code}, {right.code})"
            return Value(f"({comparison} {operator} 0)", BOOL)
        return Value(f"({left.as_double()} {operator} {right.as_double()})", BOOL)

    def parse_additive(self) -> Value:
        value = self.parse_multiplicative()
        while self.peek() in ("+", "-"):
            operator = self.take()
            right = self.parse_multiplicative()
            value = self.arithmetic(value, operator, right)
        return value

    def parse_multiplicative(self) -> Value:
        value = self.parse_power()
        while self.peek() in ("*", "/"):
            operator = self.take()
            right = self.parse_power()
            if operator == "/":
                # Python's / is always true division
                value = Value(f"({value.as_double()} / {right.as_double()})", DBL)
            else:
                value = self.arithmetic(value, operator, right)
        return value

    def arithmetic(self, left: Value, operator: str, right: Value) -> Value:
        if left.type == INT and right.type == INT:
            return Value(f"({left.code} {operator} {right.code})", INT)
        return Value(f"({left.as_double()} {operator} {right.as_double()})", DBL)

    def parse_power(self) -> Value:
        value = self.parse_unary()
        if self.accept("**"):
            exponent = self.parse_power()
            return Value(f"pow({value.as_double()}, {exponent.as_double()})", DBL)
        return value

    def parse_unary(self) -> Value:
        if self.accept("-"):
            value = self.parse_unary()
            if value.type == INT:
                return Value(f"(-{value.code})", INT)
            return Value(f"(-{value.as_double()})", DBL)
        return self.parse_postfix()

    def parse_postfix(self) -> Value:
        value = self.parse_primary()
        while True:
            if self.accept("|"):
                value = self.parse_filter(value)
            elif self.accept("["):
                index = self.parse_ternary()
                self.expect("]")
                value = self.subscript(value, index)
            else:
                return value

    def parse_filter(self, value: Value) -> Value:
        name = self.take()
        if name == "float":
            default = "0.0"
            if self.accept("("):
                default = self.parse_ternary().as_double()
                self.expect(")")
            return Value(value.as_double(default), DBL)
        if name == "round":
            if self.accept("("):
                self.expect(")")
            # Jinja's default rounding is Python's, which is half-to-even -
            # and so is rint() under the default rounding mode.
            return Value(f"rint({value.as_double()})", DBL)
        if name == "sort":
            if self.accept("("):
                self.expect(")")
            if not value.code.startswith("__list3:"):
                raise TemplateError("sort is only supported on a 3-element list")
            return value
        raise TemplateError(f"unsupported filter {name!r}")

    def subscript(self, value: Value, index: Value) -> Value:
        if not value.code.startswith("__list3:"):
            raise TemplateError("indexing is only supported on a sorted 3-list")
        if index.code != "1":
            raise TemplateError("only the median element [1] is supported")
        parts = value.code[len("__list3:") :].split("\x00")
        return Value(f"median3({parts[0]}, {parts[1]}, {parts[2]})", DBL)

    def parse_primary(self) -> Value:
        token = self.peek()
        if token is None:
            raise TemplateError("unexpected end of expression")

        if token == "(":
            self.take()
            value = self.parse_ternary()
            self.expect(")")
            return value

        if token == "[":
            self.take()
            items = [self.parse_ternary()]
            while self.accept(","):
                items.append(self.parse_ternary())
            self.expect("]")
            if len(items) != 3:
                raise TemplateError("only 3-element lists are supported")
            # Carried symbolically until |sort)[1] turns it into a median
            return Value("__list3:" + "\x00".join(i.as_double() for i in items), DBL)

        self.take()

        if token[0] in "\"'":
            return Value(c_string(unquote(token)), STR)

        if re.fullmatch(r"\d+", token):
            return Value(token, INT)
        if re.fullmatch(r"\d+\.\d+", token):
            return Value(token, DBL)

        if token == "states":
            self.expect("(")
            entity = unquote(self.take())
            self.expect(")")
            return Value(f"state[{self.entity_index(entity)}]", STR)

        if token == "is_state":
            self.expect("(")
            entity = unquote(self.take())
            self.expect(",")
            expected = unquote(self.take())
            self.expect(")")
            index = self.entity_index(entity)
            return Value(f"(strcmp(state[{index}], {c_string(expected)}) == 0)", BOOL)

        if token in self.macros:
            self.expect("(")
            argument = self.parse_ternary()
            self.expect(")")
            # Macros write straight into the output buffer, so a call is only
            # valid as a whole {{ ... }} - flagged by the caller.
            return Value(f"__macro:{token}\x00{argument.as_dyn()}", STR)

        declared = self.lookup(token)
        if declared is None:
            raise TemplateError(f"unknown name {token!r}")
        return Value(f"v_{token}", declared)


def unquote(token: str) -> str:
    if not token or token[0] not in "\"'":
        raise TemplateError(f"expected a string literal, found {token!r}")
    body = token[1:-1]
    return re.sub(r"\\(.)", r"\1", body)


def c_string(text: str) -> str:
    out = ['"']
    for byte in text.encode("utf-8"):
        char = chr(byte)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "?":
            # Keeps trigraph-happy compilers quiet
            out.append("\\?")
        elif 0x20 <= byte < 0x7F:
            out.append(char)
        else:
            out.append(f"\\{byte:03o}")
    out.append('"')
    return "".join(out)


def c_string_chunks(text: str, width: int = 100) -> list[str]:
    """Split into adjacent literals so no source line is unreadable."""
    encoded = text.encode("utf-8")
    chunks = []
    for start in range(0, len(encoded), width):
        piece = encoded[start : start + width]
        chunks.append(c_string(piece.decode("utf-8", "surrogateescape")))
    return chunks or ['""']


# --------------------------------------------------------------------------
# Code generation
# --------------------------------------------------------------------------


@dataclass
class Emitter:
    lines: list[str] = field(default_factory=list)
    indent: int = 1

    def line(self, text: str = "") -> None:
        self.lines.append(("    " * self.indent + text) if text else "")


def emit_text(emitter: Emitter, text: str) -> None:
    chunks = c_string_chunks(text)
    if len(chunks) == 1:
        emitter.line(f"buf_add(out, {chunks[0]}, {len(text.encode())});")
        return
    emitter.line("buf_add(out,")
    emitter.indent += 1
    for chunk in chunks:
        emitter.line(chunk)
    emitter.line(f", {len(text.encode())});")
    emitter.indent -= 1


def emit_output(compiler: Compiler, emitter: Emitter, expression: str) -> None:
    value = compiler.compile(expression)
    if value.code.startswith("__macro:"):
        name, argument = value.code[len("__macro:") :].split("\x00")
        emitter.line(f"m_{name}(out, state, {argument});")
        return
    if "__macro:" in value.code or "__list3:" in value.code:
        raise TemplateError(f"unsupported composite expression: {expression}")
    if value.type == STR:
        emitter.line(f"buf_str(out, {value.code});")
    elif value.type == INT:
        emitter.line(f"buf_int(out, {value.code});")
    elif value.type == DBL:
        emitter.line(f"buf_double(out, {value.code});")
    elif value.type == DYN:
        emitter.line(f"jv_render(out, {value.code});")
    else:
        emitter.line(f'buf_str(out, {value.code} ? "True" : "False");')


def emit_body(compiler: Compiler, emitter: Emitter, nodes: list, depth: int = 0) -> int:
    """Emit nodes until the matching end tag; returns the index consumed."""
    index = 0
    while index < len(nodes):
        node = nodes[index]
        index += 1
        if isinstance(node, Text):
            emit_text(emitter, node.text)
        elif isinstance(node, Output):
            emit_output(compiler, emitter, node.expr)
        elif isinstance(node, Statement):
            keyword = node.keyword
            if keyword in ("endif", "endmacro", "else"):
                return index - 1
            if keyword == "set":
                name, _, expression = node.rest.partition("=")
                name = name.strip()
                value = compiler.compile(expression.strip())
                if value.code.startswith("__macro:"):
                    raise TemplateError("a macro call cannot be assigned")
                compiler.declare(name, value.type)
                emitter.line(f"{C_TYPE[value.type]} v_{name} = {value.code};")
                emitter.line(f"(void)v_{name};")
            elif keyword == "if":
                condition = compiler.compile(node.rest)
                emitter.line(f"if ({condition.as_bool()}) {{")
                emitter.indent += 1
                consumed = emit_body(compiler, emitter, nodes[index:], depth + 1)
                index += consumed
                emitter.indent -= 1
                terminator = nodes[index]
                index += 1
                if isinstance(terminator, Statement) and terminator.keyword == "else":
                    emitter.line("} else {")
                    emitter.indent += 1
                    consumed = emit_body(compiler, emitter, nodes[index:], depth + 1)
                    index += consumed
                    emitter.indent -= 1
                    index += 1  # endif
                emitter.line("}")
            elif keyword == "macro":
                raise TemplateError("macros must be defined at the top level")
            else:
                raise TemplateError(f"unsupported tag {{% {keyword} %}}")
    return index


def split_macros(nodes: list) -> tuple[list, dict[str, tuple[str, list]]]:
    """Lift {% macro %} definitions out of the node stream."""
    body: list = []
    macros: dict[str, tuple[str, list]] = {}
    index = 0
    while index < len(nodes):
        node = nodes[index]
        if isinstance(node, Statement) and node.keyword == "macro":
            signature = re.fullmatch(r"(\w+)\((\w+)\)", node.rest)
            if not signature:
                raise TemplateError(f"unsupported macro signature: {node.rest}")
            name, parameter = signature.group(1), signature.group(2)
            index += 1
            collected: list = []
            while index < len(nodes):
                inner = nodes[index]
                if isinstance(inner, Statement) and inner.keyword == "endmacro":
                    break
                collected.append(inner)
                index += 1
            macros[name] = (parameter, collected)
        else:
            body.append(node)
        index += 1
    return body, macros


def generate() -> str:
    source = TEMPLATE.read_text(encoding="utf-8")
    # Jinja2 defaults to keep_trailing_newline=False, dropping exactly one
    # newline at the end of the file. Without this the render is one byte long.
    if source.endswith("\r\n"):
        source = source[:-2]
    elif source.endswith("\n"):
        source = source[:-1]
    nodes = lex(source)
    body, macros = split_macros(nodes)

    compiler = Compiler()
    compiler.macros = {name: name for name in macros}

    macro_code: list[str] = []
    for name, (parameter, macro_nodes) in macros.items():
        emitter = Emitter()
        compiler.scopes.append({parameter: DYN})
        emit_body(compiler, emitter, macro_nodes)
        compiler.scopes.pop()
        macro_code.append(
            f"static void m_{name}(struct buf *out, const char *const *state, "
            f"jval v_{parameter})\n{{\n"
            f"    (void)state;\n    (void)v_{parameter};\n"
            + "\n".join(emitter.lines)
            + "\n}\n"
        )

    emitter = Emitter()
    emit_body(compiler, emitter, body)
    render_code = "\n".join(emitter.lines)

    entity_rows = []
    for entity in compiler.entities:
        domain, _, rest = entity.partition(".")
        if entity in FOREIGN_ENTITIES or entity == DEMO_ENTITY:
            key = "NULL"
        else:
            prefix = "thermiq_mqtt_vp1_"
            if not rest.startswith(prefix):
                raise TemplateError(f"unexpected entity id in template: {entity}")
            key = c_string(rest[len(prefix) :])
        is_demo = "true" if entity == DEMO_ENTITY else "false"
        entity_rows.append(
            f"    {{{c_string(entity)}, {c_string(domain)}, {key}, {is_demo}}},"
        )

    return (
        "/* @generated by standalone/codegen/gen_widget.py - do not edit.\n"
        " *\n"
        " * Source of truth:\n"
        " * custom_components/thermiq_mqtt/frontend/heatpump_widget.j2\n"
        " *\n"
        " * Regenerate with:\n"
        " *     python3 standalone/codegen/gen_widget.py\n"
        " */\n"
        "#include <math.h>\n"
        "#include <string.h>\n"
        "\n"
        '#include "util.h"\n'
        '#include "widget.h"\n'
        "\n"
        "const struct widget_entity WIDGET_ENTITIES[] = {\n"
        + "\n".join(entity_rows)
        + "\n};\n"
        f"const int WIDGET_ENTITY_COUNT = {len(compiler.entities)};\n"
        "\n" + "\n".join(macro_code) + "\n"
        "void widget_render(struct buf *out, const char *const *state)\n{\n"
        "    (void)state;\n" + render_code + "\n}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify, do not write")
    args = parser.parse_args()

    try:
        generated = generate()
    except TemplateError as error:
        print(f"heatpump_widget.j2: {error}", file=sys.stderr)
        return 2

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != generated:
            print(
                f"{OUTPUT.relative_to(REPO)} is out of date.\n"
                f"Run: python3 standalone/codegen/gen_widget.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT.relative_to(REPO)} is up to date")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generated, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
