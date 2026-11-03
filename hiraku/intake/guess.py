"""Reading a request's flags from its words, in code: the way a request is read when Claude is not called.

A guess from keywords, nothing more: each flag it sets carries the word that set it as its reason, and
A1 shows the flags as chips the person can change. A sentence none of the words matches gives no flags.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

Record = dict[str, Any]

# flag -> words in a sentence that suggest it (matched on the NFKC, lower-case text)
WORDS: dict[str, tuple[str, ...]] = {
    "ride": ("乗れる", "人乗り", "乗用", "乗員", "乗る"),
    "lean": ("ベッド", "寝", "座る", "座面", "椅子", "いす", "もたれ", "机"),
    "self_propelled": ("走", "自走", "ロボ", "車両", "移動"),
    "outdoor": ("屋外", "林道", "野外", "アウトドア", "オフロード"),
    "rough": ("悪路", "未舗装", "不整地", "凹凸", "林道", "オフロード"),
    "public_road": ("公道", "ナンバー"),
    "battery": ("電池", "バッテリー", "充電"),
    "electric": ("電動", "モーター", "アクチュエータ", "ロボ"),
    "pinch": ("挟", "背上げ", "膝上げ", "リンク", "開閉"),
    "cargo": ("積載", "荷物", "載せ", "運ぶ", "運び"),
    "standing": ("置く", "自立", "据え置", "棚", "机"),
    "near_people": ("そば", "近く", "お客", "介護", "店内"),
    "liquid": ("液体", "飲み物", "こぼ", "スープ"),
    "vibration": ("弦", "振動", "回転", "エンジン", "モーター"),
    "continuous": ("連続", "常時", "長時間"),
    "assembly": ("組み立て", "組立", "分解", "整備", "メンテ"),
    "preload": ("弦", "ばね", "バネ", "テンション"),
    "sound": ("音", "楽器", "ギター", "ベース", "スピーカー"),
    "handheld": ("手で持", "手持ち", "携帯", "身に着け", "ウェアラブル"),
}

_KMH = re.compile(r"(\d+(?:\.\d+)?)\s*km\s*/\s*h")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def guess_flags(want: str) -> tuple[dict[str, bool], dict[str, str], str | None]:
    """(flags set, why each is set, speed class or None) from the words of a sentence."""
    text = _norm(want)
    flags: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    for flag, words in WORDS.items():
        hit = next((w for w in words if w in text), None)
        if hit:
            flags[flag] = True
            reasons[flag] = f"文に「{hit}」とあるので (文の言葉からの推測)"
    return flags, reasons, _speed_class(text)


def _speed_class(text: str) -> str | None:
    m = _KMH.search(text)
    if m:
        return "vehicle" if float(m.group(1)) > 2 * 3.6 else "walk"  # 2 m/s is 7.2 km/h
    if "歩く速さ" in text or "歩行" in text:
        return "walk"
    if "動かない" in text or "据え置" in text:
        return "static"
    return None
