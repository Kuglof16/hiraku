"""Designs split into modules (docs/contract.md, モジュールに分けた設計): each part and member carries `module`, the
design carries `modules` (owner, envelope, interfaces, variables, choices), and a round's module proposals are built
into one version by build_version.

The envelope (a module's space) is a list of boxes. When a generator splits its design (split), each module's envelope
is the axis-aligned box of each of its parts and members grown by ENVELOPE_MARGIN_MM: the space the layout gives it
now, with room to grow. A later version keeps its parent's envelopes unless the packager changes a layout variable
(then the envelopes are made again from the new layout) or gives envelopes; a part or member of the new version that
is not inside a box of its own module's envelope (k4_geometry.joints.outside_envelopes) makes build_version refuse, with which and by
how much.

Interfaces (where two modules meet), read from the design: a node that carries a part of a module other than the
frame's (carried_by; via as the part's connects_to says, else bolt), a node where members of two modules meet (weld),
a suspension's frame-side hard points (the arms' pivots by bushing, the damper's top by bolt), and a connects_to between
parts of two modules (at the middle of their boxes' centres)."""

from __future__ import annotations

from typing import Any

import numpy as np

Record = dict[str, Any]

ENVELOPE_MARGIN_MM = 25.0  # a module's room round what it has now (HIRAKU's choice, no source)
# the module's owner: one of the three design roles (structure, mechanism, electric) or the packager
OWNER = {"package": "packager", "frame": "designer.structure", "body": "designer.structure",
         "occupant": "designer.structure", "suspension": "designer.mechanism", "steering": "designer.mechanism",
         "powertrain": "designer.electric"}  # a module not listed: designer.<module>
SUSPENSION_FRAME_POINTS = {"lca_front": "bushing", "lca_rear": "bushing", "uca_front": "bushing", "uca_rear": "bushing",
                           "damper_upper": "bolt"}


class ModuleError(ValueError):
    """A design cannot be split, or a round's proposals cannot be built into a version: the message says why."""


def owner(module_id: str) -> str:
    return OWNER.get(module_id, f"designer.{module_id}")


def body_boxes(design: Record) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """{"part:<id>" | "member:<id>": (lo, hi)}: the axis-aligned box round each body's shape (k4_geometry's boxes)."""
    from hiraku.solvers.k4_geometry import model as k4

    out = {}
    for k, b in k4.bodies(design).items():
        c = b.obb.corners()
        out[k] = (c.min(axis=0), c.max(axis=0))
    return out


def _box(lo, hi) -> Record:
    return {"center_mm": [float(x) for x in (np.asarray(lo) + np.asarray(hi)) / 2.0],
            "size_mm": [float(x) for x in np.asarray(hi) - np.asarray(lo)]}


def module_of(design: Record) -> dict[str, str]:
    """{"part:<id>" | "member:<id>": module id} from the design's own module fields."""
    out = {f"part:{p['id']}": p["module"] for p in design.get("parts", []) if "module" in p}
    out.update({f"member:{m['id']}": m["module"] for m in design.get("members", []) if "module" in m})
    return out


def envelopes(design: Record, margin: float = ENVELOPE_MARGIN_MM) -> dict[str, list[Record]]:
    """Each module's envelope as made from the design now: its bodies' boxes grown by `margin`."""
    mods = module_of(design)
    out: dict[str, list[Record]] = {}
    for k, (lo, hi) in body_boxes(design).items():
        m = mods.get(k)
        if m is not None:
            out.setdefault(m, []).append(_box(lo - margin, hi + margin))
    return out


def interfaces(design: Record) -> list[Record]:
    """Where two modules meet (see the module docstring), one record per pair of modules and node or point."""
    parts = {p["id"]: p for p in design.get("parts", [])}
    out: dict[tuple, Record] = {}

    def add(a: str, b: str, via: str, node: str | None = None, point=None) -> None:
        if a == b:
            return
        pair = tuple(sorted((a, b)))
        key = (pair, node) if node is not None else (pair, tuple(round(float(x), 1) for x in point))
        if key in out:
            return
        rec: Record = {"id": f"if-{len(out) + 1:03d}", "between": list(pair), "via": via}
        if node is not None:
            rec["node"] = node
        else:
            rec["point_mm"] = [float(x) for x in point]
        out[key] = rec

    for p in parts.values():
        via = {c["to"]: c.get("via", "bolt") for c in p.get("connects_to") or []}
        for n in p.get("carried_by") or []:
            add("frame", p["module"], via.get(n, "bolt"), node=n)
    at: dict[str, set[str]] = {}
    for m in design.get("members", []):
        for n in (m["node_a"], m["node_b"]):
            at.setdefault(n, set()).add(m["module"])
    for n, mods in sorted(at.items()):
        mods = sorted(mods)
        for i, a in enumerate(mods):
            for b in mods[i + 1:]:
                add(a, b, "weld", node=n)
    for s in design.get("supports", []):
        hp = s.get("suspension", {}).get("hardpoints_mm") or {}
        for name, via in SUSPENSION_FRAME_POINTS.items():
            if name in hp:
                add("frame", "suspension", via, point=hp[name])
    for p in parts.values():
        for c in p.get("connects_to") or []:
            q = parts.get(c["to"])
            if q is not None and q["module"] != p["module"]:
                mid = (np.asarray(p["box"]["center_mm"], float) + np.asarray(q["box"]["center_mm"], float)) / 2.0
                add(p["module"], q["module"], c.get("via", "bolt"), point=mid)
    return list(out.values())


def split(design: Record, part_module, member_module, variables: dict[str, str], choices: dict[str, str],
          order: tuple[str, ...], joint: str | None = "weld") -> Record:
    """Write `module` on every part and member (part_module(part), member_module(member): a module id, or raise
    ModuleError), `joint` on every node (when given), and design.modules (in `order`): owner, envelope, interfaces,
    variables and choices ({name: module id})."""
    for p in design.get("parts", []):
        p["module"] = part_module(p)
    for m in design.get("members", []):
        m["module"] = member_module(m)
    if joint is not None:
        for n in design.get("nodes", []):
            n["joint"] = joint
    env = envelopes(design)
    ifs = interfaces(design)
    from hiraku.contract import MODULE_NAMES_JA

    design["modules"] = [{
        "module_id": mid, "title": MODULE_NAMES_JA.get(mid, mid), "owner": owner(mid),
        "envelope": env.get(mid, []) if mid != "package" else [],
        "interfaces": [i for i in ifs if mid in i["between"]] if mid != "package" else ifs,
        "variables": sorted(k for k, m in variables.items() if m == mid),
        "choices": sorted(k for k, m in choices.items() if m == mid),
    } for mid in order]
    return design


def outside(design: Record, envs: dict[str, list[Record]]) -> list[tuple[str, str, float]]:
    """Bodies not inside any box of their module's envelope: (body id, module, mm out), the farthest first
    (k4_geometry.joints.outside_envelopes, the check H1-envelope makes)."""
    from hiraku.solvers.k4_geometry.joints import outside_envelopes

    return outside_envelopes(design, envelopes=envs)


def build_version(previous: Record, proposals: list[Record], changes: dict | None = None,
                  new_envelopes: dict[str, list[Record]] | None = None, new_interfaces: list[Record] | None = None,
                  reason: str = "", regrow: bool = False) -> Record:
    """The next version from a round's module proposals (contract module_change: module_id, changes, choices) and the
    packager's own changes (layout variables), envelopes and interfaces; regrow: the packager makes every module's
    envelope again from the new version (as a layout change does), new_envelopes then override some. Refuses (ModuleError, with the reason) a
    variable or choice that is not its module's, a variable set by two proposals (also through a choice), and a part
    or member outside its module's envelope."""
    from hiraku import designgen

    mods = {m["module_id"]: m for m in previous.get("modules") or []}
    if not mods:
        raise ModuleError("this design is not split into modules (no design.modules): use revise_design")
    gen = designgen.generator_of(previous)
    rounds = [dict(p) for p in proposals]
    if changes:
        rounds.append({"change_id": "packager", "module_id": "package", "changes": changes})
    problems: list[str] = []
    who: dict[str, str] = {}
    merged: dict[str, Any] = {}
    for p in rounds:
        mid = p.get("module_id")
        if mid not in mods:
            problems.append(f"{p.get('change_id')}: no module {mid!r} in this design")
            continue
        for k in p.get("changes") or {}:
            if k not in mods[mid]["variables"]:
                problems.append(f"{p.get('change_id')}: {k} is not a variable of module {mid}")
        for k in p.get("choices") or {}:
            if k not in mods[mid].get("choices", []):
                problems.append(f"{p.get('change_id')}: {k} is not a choice of module {mid}")
        sets = dict(p.get("changes") or {})
        if p.get("choices"):
            sets.update(gen.apply_choices(dict(p["choices"]), {}))
        for k in sets:
            if k in who and who[k] != p.get("change_id"):
                problems.append(f"{k} is changed by both {who[k]} and {p.get('change_id')}")
            who[k] = p.get("change_id")
        merged.update(p.get("changes") or {})
        merged.update(p.get("choices") or {})
    if problems:
        raise ModuleError("the round's proposals cannot be built: " + "; ".join(problems))
    v = int(previous["design_version"])
    new = dict(designgen.regenerate(previous, merged)) if merged else dict(previous)
    new.update(design_version=v + 1, parent_version=v, change_reason=reason or previous.get("change_reason"))
    regrown = bool(changes) or regrow or new_envelopes is not None
    envs = {m["module_id"]: m["envelope"] for m in previous["modules"]}
    if bool(changes) or regrow:
        envs = {m["module_id"]: m["envelope"] for m in new["modules"]}
    if new_envelopes:
        envs.update(new_envelopes)
    ifs = new_interfaces if new_interfaces is not None else (
        next((m["interfaces"] for m in new["modules"] if m["module_id"] == "package"), None) if regrown else
        next((m["interfaces"] for m in previous["modules"] if m["module_id"] == "package"), None))
    for m in new.get("modules", []):
        m["envelope"] = envs.get(m["module_id"], []) if m["module_id"] != "package" else []
        if ifs is not None:
            m["interfaces"] = ifs if m["module_id"] == "package" else [i for i in ifs if m["module_id"] in i["between"]]
    out = outside(new, envs)
    if out:
        where = ", ".join(f"{k.split(':', 1)[1]} ({m}, {d:.0f} mm out)" for k, m, d in out[:10])
        raise ModuleError(f"{len(out)} parts or members are outside their module's space: {where}"
                          + (" ..." if len(out) > 10 else "") + "; the packager gives that module more room "
                          "(build_version envelopes, or a layout change) or the module's designer changes the proposal")
    new["module_changes"] = [p for p in proposals]
    return new
