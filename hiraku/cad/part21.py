"""ISO 10303-21 (STEP physical file) text to entities and back.

An entity is `#12=CARTESIAN_POINT('',(0.,0.,1.));` (simple) or `#12=(TYPE_A(...) TYPE_B(...));`
(complex). Parameters become Python values: Ref (a #id), Enum (.T.), Typed (LENGTH_MEASURE(1.)),
str, int, float, None for $, the string '*' for a derived value, and lists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from hiraku.cad import CadError


class Ref(int):
    def __repr__(self) -> str:
        return f"#{int(self)}"


class Enum(str):
    pass


class Derived:
    def __repr__(self) -> str:
        return "*"


DERIVED = Derived()


@dataclass
class Typed:
    name: str
    args: list


@dataclass
class Entity:
    id: int
    parts: list  # [(TYPE, args)], one item for a simple entity
    text: str = field(default="", repr=False)

    @property
    def type(self) -> str:
        return self.parts[0][0] if len(self.parts) == 1 else "(" + " ".join(p[0] for p in self.parts) + ")"

    @property
    def args(self) -> list:
        return self.parts[0][1]

    def has(self, name: str) -> bool:
        if len(self.parts) == 1:
            return self.parts[0][0] == name
        return any(p[0] == name for p in self.parts)

    def part(self, name: str) -> list:
        for p in self.parts:
            if p[0] == name:
                return p[1]
        raise CadError(f"#{self.id} {self.type} has no {name}")


_TOKEN = re.compile(r"'(?:[^']|'')*'|[^\s(),;=']+|[(),;=]")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _decode(s: str) -> str:
    s = s[1:-1].replace("''", "'")
    if "\\" not in s:
        return s

    def x2(m):
        h = m.group(1)
        return "".join(chr(int(h[i:i + 4], 16)) for i in range(0, len(h), 4))

    s = re.sub(r"\\X2\\([0-9A-Fa-f]*)\\X0\\", x2, s)
    s = re.sub(r"\\X\\([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), s)
    return s.replace("\\\\", "\\")


def _atom(tok: str):
    c = tok[0]
    if c == "'":
        return _decode(tok)
    if c == "#":
        return Ref(int(tok[1:]))
    if c == "$":
        return None
    if c == "*":
        return DERIVED
    if c == "." and len(tok) > 1 and (tok[1].isalpha() or tok[1] == "_"):
        return Enum(tok[1:-1].upper())
    if c.isdigit() or c in "+-.":
        try:
            return float(tok) if any(ch in tok for ch in ".Ee") else int(tok)
        except ValueError:
            raise CadError(f"bad number {tok!r}") from None
    raise CadError(f"unexpected {tok!r}")


def _args(toks: list, i: int) -> tuple[list, int]:
    """Parse '(' ... ')' starting at toks[i] == '('; returns (list, index after ')')."""
    if toks[i] != "(":
        raise CadError(f"expected '(' but found {toks[i]!r}")
    i += 1
    out: list = []
    stack: list = []
    expect_value = True
    while True:
        t = toks[i]
        if t == ")":
            i += 1
            if not stack:
                return out, i
            done = out
            out, name = stack.pop()
            out.append(Typed(name, done) if name else done)
            expect_value = False
            continue
        if t == ",":
            i += 1
            expect_value = True
            continue
        if not expect_value:
            raise CadError(f"expected ',' or ')' but found {t!r}")
        if t == "(":
            stack.append((out, None))
            out = []
            i += 1
            continue
        c = t[0]
        if c.isalpha() or c == "_" or c == "!":
            if toks[i + 1] != "(":
                raise CadError(f"expected '(' after {t!r}")
            stack.append((out, t.upper()))
            out = []
            i += 2
            continue
        out.append(_atom(t))
        i += 1
        expect_value = False


def parse(text: str) -> dict[int, Entity]:
    """Entities of the DATA section by id. A broken entity raises CadError naming it."""
    m = re.search(r"\bDATA\b[^;]*;", text)
    if not m or "ISO-10303-21" not in text[:2000].upper().replace(" ", ""):
        raise CadError("not a STEP file (ISO-10303-21 header and DATA section expected)")
    start = m.end()
    end_m = re.search(r"\bENDSEC\s*;", text[start:])
    end = start + end_m.start() if end_m else len(text)
    body = text[start:end]
    if "/*" in body:
        body = _COMMENT.sub(" ", body)
    toks = _TOKEN.findall(body)
    toks.append(";")
    out: dict[int, Entity] = {}
    i, n = 0, len(toks) - 1
    while i < n:
        t = toks[i]
        if t[0] != "#":
            raise CadError(f"expected an entity '#id=' but found {t!r}")
        eid = int(t[1:])
        j = i
        try:
            if toks[i + 1] != "=":
                raise CadError("expected '='")
            i += 2
            parts = []
            if toks[i] == "(":
                i += 1
                while toks[i] != ")":
                    name = toks[i]
                    if not (name[0].isalpha() or name[0] == "_"):
                        raise CadError(f"expected a type name but found {name!r}")
                    a, i = _args(toks, i + 1)
                    parts.append((name.upper(), a))
                i += 1
            else:
                name = toks[i]
                if not (name[0].isalpha() or name[0] == "_"):
                    raise CadError(f"expected a type name but found {name!r}")
                a, i = _args(toks, i + 1)
                parts.append((name.upper(), a))
            if toks[i] != ";":
                raise CadError(f"expected ';' but found {toks[i]!r}")
            i += 1
        except (CadError, IndexError) as e:
            raise CadError(f"entity #{eid} cannot be read ({e}): {' '.join(toks[j:j + 12])}") from None
        out[eid] = Entity(eid, parts)
    return out


def header_schema(text: str) -> str:
    m = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", text)
    return m.group(1) if m else ""


# ---- writing ----

def fmt_float(x: float) -> str:
    x = float(x)
    if x == 0:
        return "0."
    s = f"{x:.15G}"
    if "E" in s:
        mant, exp = s.split("E")
        if "." not in mant:
            mant += "."
        return f"{mant}E{exp}"
    return s if "." in s else s + "."


def _enc_str(s: str) -> str:
    out = []
    for ch in s:
        if ch == "'":
            out.append("''")
        elif ch == "\\":
            out.append("\\\\")
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            out.append("\\X2\\%04X\\X0\\" % ord(ch))
    return "'" + "".join(out) + "'"


def fmt(v: Any) -> str:
    if v is None:
        return "$"
    if isinstance(v, Ref):
        return f"#{int(v)}"
    if isinstance(v, Enum):
        return f".{v}."
    if isinstance(v, Derived):
        return "*"
    if isinstance(v, str):
        return _enc_str(v)
    if isinstance(v, bool):
        return ".T." if v else ".F."
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return fmt_float(v)
    if isinstance(v, Typed):
        return f"{v.name}({','.join(fmt(a) for a in v.args)})"
    if isinstance(v, (list, tuple)):
        return "(" + ",".join(fmt(a) for a in v) + ")"
    raise CadError(f"cannot write value {v!r}")


def fmt_entity_body(parts: list) -> str:
    if len(parts) == 1:
        name, args = parts[0]
        return f"{name}{fmt(list(args))}"
    return "(" + " ".join(f"{n}{fmt(list(a))}" for n, a in parts) + ")"


def refs_in(v: Any):
    if isinstance(v, Ref):
        yield int(v)
    elif isinstance(v, Typed):
        for a in v.args:
            yield from refs_in(a)
    elif isinstance(v, (list, tuple)):
        for a in v:
            yield from refs_in(a)


def remap(v: Any, ids: dict[int, int]) -> Any:
    if isinstance(v, Ref):
        return Ref(ids[int(v)])
    if isinstance(v, Typed):
        return Typed(v.name, [remap(a, ids) for a in v.args])
    if isinstance(v, list):
        return [remap(a, ids) for a in v]
    return v
