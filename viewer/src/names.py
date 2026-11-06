"""Names on the page, from where HIRAKU's screen takes them, written into viewer/js/names.js:

- the fields' domain names: hiraku.contract.DOMAIN_NAMES_JA (操安・動力・空力・衝突・強度信頼性・音振・熱・組み立て);
- the quantities' and the series channels' Japanese names: web/js/labels.js QUANTITY_JA and CHANNEL_JA (read from
  that file, not copied by hand);
- VIEWER_QUANTITY_JA: the few quantities the viewer shows that labels.js does not name (the viewer's own words;
  labels.js wins when it has the name).

    python -m viewer.src.names          # rewrite viewer/js/names.js (tests/viewer checks it is up to date)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from viewer.src.common import VIEWER

REPO = VIEWER.parent
LABELS_JS = REPO / "web" / "js" / "labels.js"
OUT = VIEWER / "js" / "names.js"

# the page's field -> the contract's domain (fatigue is the strength domain's R3; the page tells the two apart by
# their labels: FIELD_LABEL below)
FIELD_DOMAIN = {"strength": "strength", "fatigue": "strength", "nvh": "nvh", "crash": "crash", "handling": "handling",
                "powertrain": "powertrain", "aero": "aero", "thermal": "thermal", "assembly": "assembly"}
# the two strength fields: the domain's name with the catalog's R1 / R1s title (静的強度), and 疲労 (R3 疲労・耐久)
FIELD_SUFFIX = {"strength": " (静的強度)"}
FIELD_OWN = {"fatigue": "疲労"}

VIEWER_QUANTITY_JA = {   # not in web/js/labels.js QUANTITY_JA (the viewer's own words)
    "frame_split_fraction": "座席側から離れる塊の質量の割合",
    "occupant_space_left": "乗員の箱までの最小のすき間",
    "crush_margin": "前のつぶれの残り",
    "crumple_length": "つぶれてよい長さ",
    "broken_members": "折れた部材の数",
    "max_plastic_rotation": "最大の塑性ヒンジ回転",
    "battery_mount_force": "電池の取付の力",
    "crush_at_split": "分離までのつぶれ",
    "frame_split_time": "分離の時刻",
    "member_axial_force": "部材の軸力",
    "member_hinge": "部材の端のヒンジ回転",
}


def _js_table(text: str, name: str) -> dict[str, str]:
    """The `key: "value",` lines of `const <name> = { ... };` in a JS file (a key given twice: the last, as in JS)."""
    m = re.search(r"const " + re.escape(name) + r" = \{\n(.*?)\n\};", text, re.S)
    if m is None:
        raise LookupError(f"{LABELS_JS}: no table {name}")
    out = {}
    for k, v in re.findall(r'^\s*([A-Za-z_]\w*)\s*:\s*"([^"]*)"\s*,?\s*(?://.*)?$', m.group(1), re.M):
        out[k] = v
    return out


def quantity_names() -> dict[str, str]:
    return _js_table(LABELS_JS.read_text(encoding="utf-8"), "QUANTITY_JA")


def channel_names() -> dict[str, str]:
    return _js_table(LABELS_JS.read_text(encoding="utf-8"), "CHANNEL_JA")


def domain_names() -> dict[str, str]:
    from hiraku import contract
    return dict(contract.DOMAIN_NAMES_JA)


def field_labels() -> dict[str, str]:
    dn = domain_names()
    return {f: FIELD_OWN.get(f) or dn[d] + FIELD_SUFFIX.get(f, "") for f, d in FIELD_DOMAIN.items()}


def render() -> str:
    j = lambda o: json.dumps(o, ensure_ascii=False, sort_keys=True, indent=1)
    return ("// Written by `python -m viewer.src.names` (do not edit): the names HIRAKU's screen uses.\n"
            "// DOMAIN_JA: hiraku/contract DOMAIN_NAMES_JA; FIELD_LABEL: the page's field buttons from it (strength's two\n"
            "// fields: 強度信頼性 (静的強度) and 疲労); QUANTITY_JA, CHANNEL_JA: web/js/labels.js; QUANTITY_JA_VIEWER: the\n"
            "// viewer's own words for the quantities labels.js does not name.\n"
            "window.HV = window.HV || {};\n"
            f"HV.DOMAIN_JA = {j(domain_names())};\n"
            f"HV.FIELD_DOMAIN = {j(FIELD_DOMAIN)};\n"
            f"HV.FIELD_LABEL = {j(field_labels())};\n"
            f"HV.QUANTITY_JA = {j(quantity_names())};\n"
            f"HV.CHANNEL_JA = {j(channel_names())};\n"
            f"HV.QUANTITY_JA_VIEWER = {j(VIEWER_QUANTITY_JA)};\n")


OLD_TITLE = {"強度: ": "strength", "NVH: ": "nvh", "NVH (シェル): ": "nvh"}   # titles of data written before


def title(field: str, rest: str, variant: str = "") -> str:
    """A data title: the field's domain name (the contract's), its variant, then what it shows."""
    head = domain_names()[FIELD_DOMAIN[field]] if field != "fatigue" else FIELD_OWN["fatigue"]
    return f"{head}{f' ({variant})' if variant else ''}: {rest}"


def retitle(obj: dict) -> dict:
    """A data object written before with the old field names in its title: the contract's names."""
    t = obj.get("title")
    for old, d in OLD_TITLE.items():
        if isinstance(t, str) and t.startswith(old):
            v = re.match(r"NVH \((.*)\): ", old)
            obj["title"] = f"{domain_names()[d]}{f' ({v.group(1)})' if v else ''}: {t[len(old):]}"
    return obj


def main() -> None:
    import sys
    from viewer.src import common
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
    if len(sys.argv) > 2 and sys.argv[1] == "--data":   # also retitle the data written before
        for p in sorted(Path(sys.argv[2]).rglob("*.js")):
            field, obj = common.read_data_js(p)
            before = obj.get("title")
            if retitle(obj).get("title") != before:
                p.write_text(common.data_js(field, obj), encoding="ascii")
                print(f"retitled {p.name}: {obj['title']}")


if __name__ == "__main__":
    main()
