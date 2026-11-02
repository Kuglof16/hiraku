"""The product property flags, in words a person can read.

The flag names are the contract's (`flags` in hiraku.schema.json). The one-character
symbols and the questions are the catalog's way of asking "does this product ...?";
the screen shows them as chips (A1), so web/js/intake/flags.js is generated from
this table (`python -m hiraku.intake gen-js`) and a test keeps the two equal.
"""

from __future__ import annotations

import ast
import re

from hiraku import contract

# flag -> (symbol, question)
FLAGS: dict[str, tuple[str, str]] = {
    "ride": ("乗", "人が乗って一緒に動くか"),
    "lean": ("預", "人が体を預けるか (寝る・座る・もたれる・手をつく)"),
    "self_propelled": ("走", "自分の動力で移動するか"),
    "outdoor": ("外", "屋外で使うか"),
    "rough": ("悪", "未舗装・凹凸の路面を走るか"),
    "public_road": ("公", "公道を走るか (法令の区分に入るか)"),
    "battery": ("池", "電池を積むか"),
    "electric": ("電", "電動で動く部分があるか (モーター・アクチュエータ)"),
    "pinch": ("挟", "動く部分に体が入る隙間があるか"),
    "cargo": ("荷", "物を載せるか"),
    "standing": ("置", "床・地面に自立して置くか (倒れうるか)"),
    "near_people": ("近", "人のすぐ近くを動くか"),
    "liquid": ("液", "液体を運ぶか"),
    "vibration": ("振", "振動源があるか (回転・弦)"),
    "continuous": ("続", "発熱が問題になるほど連続で動くか"),
    "assembly": ("組", "組み立て・分解・整備があるか"),
    "preload": ("張", "常にかかる内力があるか (弦・ばね)"),
    "sound": ("音", "音が機能か"),
    "handheld": ("持", "手で持つ・身に着けるか"),
}

# speed_class -> (symbol, meaning)
SPEED_CLASSES: dict[str, tuple[str, str]] = {
    "static": ("静", "動かない"),
    "walk": ("歩", "歩く速さ (2 m/s 以下)"),
    "vehicle": ("車", "車の速さ (40 km/h 以下)"),
}

# flag -> a short name for a chip
FLAG_LABELS: dict[str, str] = {
    "ride": "人が乗る", "lean": "体を預ける", "self_propelled": "自分で動く", "outdoor": "屋外", "rough": "悪路",
    "public_road": "公道", "battery": "電池", "electric": "電動", "pinch": "挟まれる隙間", "cargo": "荷を載せる",
    "standing": "床に自立", "near_people": "人のそばを動く", "liquid": "液体を運ぶ", "vibration": "振動源",
    "continuous": "連続で動く", "assembly": "組み立て・整備", "preload": "常に張力", "sound": "音が機能", "handheld": "手で持つ",
}

assert tuple(FLAGS) == tuple(contract.schema()["$defs"]["flags"]["propertyNames"]["enum"]), "flags differ from the contract"
assert tuple(FLAG_LABELS) == tuple(FLAGS)
assert tuple(SPEED_CLASSES) == tuple(contract.schema()["$defs"]["speed_class"]["enum"])

_NAME = re.compile(r"[a-z_]+")


def symbol(flag: str) -> str:
    return FLAGS[flag][0]


def describe_condition(expr: str) -> str:
    """An applies_if expression in symbols: "self_propelled and speed_vehicle" -> "走∧速=車"."""
    tree = ast.parse(expr, mode="eval")

    def walk(node: ast.AST, top: bool = False) -> str:
        if isinstance(node, ast.Expression):
            return walk(node.body, top)
        if isinstance(node, ast.Name):
            if node.id == "always":
                return "常"
            if node.id.startswith("speed_"):
                return f"速={SPEED_CLASSES[node.id.removeprefix('speed_')][0]}"
            return FLAGS[node.id][0]
        if isinstance(node, ast.UnaryOp):
            return "¬" + walk(node.operand)
        if isinstance(node, ast.BoolOp):
            sep = "∧" if isinstance(node.op, ast.And) else "∨"
            text = sep.join(walk(v) for v in node.values)
            return text if top else f"({text})"
        raise ValueError(f"unexpected node {type(node).__name__}")

    return walk(tree, top=True)


def flags_in(expr: str) -> list[str]:
    """Flag names an applies_if expression mentions, in order of appearance."""
    out: list[str] = []
    for name in _NAME.findall(expr):
        if name in FLAGS and name not in out:
            out.append(name)
    return out


def on_flags(flags: dict[str, bool]) -> list[str]:
    """The flags that are true, in the contract's order."""
    return [f for f in FLAGS if flags.get(f)]


def flags_text(flags: dict[str, bool]) -> str:
    """"走・池・電" for the flags that are true."""
    return "・".join(symbol(f) for f in on_flags(flags)) or "なし"
