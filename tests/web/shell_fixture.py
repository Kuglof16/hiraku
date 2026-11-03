"""Two design versions, each with a beam analysis and a shell analysis of the same load
case, for the screen's tests of the shell views (the field switch, the zoom to the
largest element, before / after and the N07 cut).

Shape example: version 1 is the sample design; version 2 adds gusset plates at the
rail joints (nodes L1 and R1), so it gets a mesh of its own (mesh.created again). The
values on the meshes are made up here: a base from the sample's beam stress and the
height in the section, plus a bump on the rails' side walls near the joints, larger
than the allowable stress in version 1 and well under it in version 2. They show the
record's shape, not a result. Every number is computed below.
"""

import copy
import json
import math

from hiraku.webserve.server import SAMPLE_EVENTS
from tests.web.mesh_fixture import frame_mesh

JOINTS = ("L1", "R1")  # where the rails meet the middle cross member
BUMP_RADIUS_MM = 60.0
# the side-wall bump at a joint, as a share of the allowable stress (shape example)
BUMP_SHARE = {1: {"L1": 2.0, "R1": 1.6}, 2: {"L1": 0.55, "R1": 0.45}}
GUSSET = {"size_mm": 120.0, "t_mm": 3.0, "cells": 6}  # square plate on the joint's top wall (shape example)

SHELL_TOOL = {"name": "frame_shell", "title": "シェルのフレーム (板のメッシュ)", "domain": "strength", "base": "K1",
              "tests": ["R1"], "origin": "builtin", "usable": True, "checks": []}
BEAM_TOOL = {"name": "frame_static", "title": "梁のフレーム (K1)", "domain": "strength", "base": "K1",
             "tests": ["R1"], "origin": "builtin", "usable": True, "checks": []}


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm(a):
    return math.sqrt(sum(x * x for x in a))


def version2(design):
    """Version 1 with a gusset plate (a part) on the top wall at each rail joint."""
    d = copy.deepcopy(design)
    d["design_version"] = 2
    d["parent_version"] = 1
    d["change_reason"] = "レールのつなぎ目 (L1・R1) にガセットの板を足した (形の見本)"
    nodes = {n["id"]: n["xyz_mm"] for n in d["nodes"]}
    rail = d["sections"]["rt40x80x2"]["dims_mm"]
    steel = d["materials"]["steel"]["density_kg_m3"]
    s, t = GUSSET["size_mm"], GUSSET["t_mm"]
    for j in JOINTS:
        x, y, z = nodes[j]
        inward = -1.0 if y > 0 else 1.0  # toward the middle of the frame
        d["parts"].append({
            "id": f"gusset_{j}", "name": f"ガセット {j}", "kind": "frame",
            "box": {"center_mm": [x, y + inward * s / 2, z + rail["h"] / 2 + t / 2], "size_mm": [s, s, t]},
            "mass_kg": round(s * s * t * 1e-9 * steel, 4), "carried_by": [j],
        })
    return d


def gusset_mesh(design, mesh):
    """The mesh of version 2: the tube walls and a flat plate of quads at each joint."""
    out = copy.deepcopy(mesh)
    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    rail = design["sections"]["rt40x80x2"]["dims_mm"]
    s, n = GUSSET["size_mm"], GUSSET["cells"]
    for j in JOINTS:
        x, y, z = nodes[j]
        inward = -1.0 if y > 0 else 1.0
        zt = z + (rail["h"] - rail["t"]) / 2  # the top wall's mid-surface
        base = len(out["nodes_mm"])
        for a in range(n + 1):
            for b in range(n + 1):
                out["nodes_mm"].append([round(x - s / 2 + s * a / n, 3), round(y + inward * s * b / n, 3), round(zt, 3)])
        for a in range(n):
            for b in range(n):
                q = [base + a * (n + 1) + b, base + (a + 1) * (n + 1) + b, base + (a + 1) * (n + 1) + b + 1, base + a * (n + 1) + b + 1]
                p0, p1, p3 = (out["nodes_mm"][i] for i in (q[0], q[1], q[3]))
                if _cross(_sub(p1, p0), _sub(p3, p0))[2] < 0:  # counter-clockwise seen from above (the outer side)
                    q = q[::-1]
                out["elements"].append(q)
                out["element_member"].append(f"gusset_{j}")
        out["member_thickness_mm"][f"gusset_{j}"] = GUSSET["t_mm"]
    out["mesh_id"] = "mesh-0002"
    out["design_version"] = 2
    out["notes"] = "shape example: closed tube walls and a gusset plate on each rail joint (screen tests); the plates share no nodes with the walls"
    return out


def shell_fields(mesh, design, beam, limit, version):
    """Stress on the mesh's four surfaces (shape example, see the module's words)."""
    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    zc = sum(p[2] for p in mesh["nodes_mm"]) / len(mesh["nodes_mm"])
    half_h = design["sections"]["rt40x80x2"]["dims_mm"]["h"] / 2
    top, bottom = [], []
    for el, mid in zip(mesh["elements"], mesh["element_member"]):
        pts = [mesh["nodes_mm"][i] for i in el]
        c = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
        normal = _cross(_sub(pts[2], pts[0]), _sub(pts[3], pts[1]))
        side_wall = abs(normal[1]) > 0.9 * _norm(normal)  # a wall facing +y or -y
        base = beam["per_member"].get(mid, 0.0) * (0.5 + 0.3 * min(1.0, abs(c[2] - zc) / half_h))
        bump = 0.0
        for j in JOINTS:
            r = _norm(_sub(c, nodes[j]))
            share = BUMP_SHARE[version][j] * (1.0 if side_wall and mid.startswith("rail") else 0.25)
            bump = max(bump, share * limit * math.exp(-((r / BUMP_RADIUS_MM) ** 2)))
        top.append(round(base + bump, 3))
        bottom.append(round(0.8 * base + 0.9 * bump, 3))
    mid_s = [round(0.5 * (a + b), 3) for a, b in zip(top, bottom)]
    biggest = [max(a, b) for a, b in zip(top, bottom)]
    return [
        {"kind": "stress", "label": "シェルの応力 (形の見本)", "unit": "MPa", "mesh_id": mesh["mesh_id"], "surface": s, "per_element": v}
        for s, v in (("top", top), ("mid", mid_s), ("bottom", bottom), ("max", biggest))
    ]


def shell_events(target=8000):
    """The sample run, then: the two tools checked, version 1's beam analysis (the
    sample's) and shell analysis, then version 2 with its prediction, beam analysis,
    mesh and shell analysis. Returns (events, {version: mesh}, {version: shell analysis})."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    by_kind = lambda k: next(e for e in src if e["kind"] == k)  # noqa: E731
    run, design_ev, pred_ev, ana_ev = by_kind("run.started"), by_kind("design.created"), by_kind("prediction.recorded"), by_kind("analysis.finished")
    lc = run["data"]["load_cases"][0]
    limit = lc["criterion"]["limit"]["value"]
    d1 = design_ev["data"]
    d2 = version2(d1)
    size = 4.0
    m1 = frame_mesh(d1, size)
    while len(m1["elements"]) > target:
        size *= 1.05
        m1 = frame_mesh(d1, size)
    m2 = gusset_mesh(d2, frame_mesh(d2, size))
    beam1 = ana_ev["data"]
    beam_field = next(f for f in beam1["fields"] if f["kind"] == "stress")

    def shell_analysis(aid, version, mesh, design, pred_id):
        fields = shell_fields(mesh, design, beam_field, limit, version)
        mx = next(f for f in fields if f["surface"] == "max")["per_element"]
        at = max(range(len(mx)), key=mx.__getitem__)
        return {"analysis_id": aid, "tool": "frame_shell", "base": "K1", "design_version": version, "load_case_id": lc["load_case_id"],
                "prediction_ids": [pred_id], "status": "ok",
                "results": [{"quantity": "max_stress", "value": mx[at], "unit": "MPa", "where": mesh["element_member"][at]}],
                "fields": fields, "inputs_from": [pred_id], "wall_time_s": 1.0}

    shell1 = shell_analysis("ana-0002", 1, m1, d1, pred_ev["data"]["prediction_id"])
    pred2 = {**copy.deepcopy(pred_ev["data"]), "prediction_id": "pred-0002", "design_version": 2}
    beam2 = {**copy.deepcopy(beam1), "analysis_id": "ana-0003", "design_version": 2, "prediction_ids": ["pred-0002"], "inputs_from": ["pred-0002"]}
    shell2 = shell_analysis("ana-0004", 2, m2, d2, "pred-0002")
    strength = {"agent": "cae.strength", "domain": "strength"}
    more = [
        {"kind": "tool.checked", "agent": "system", "data": BEAM_TOOL},
        {"kind": "tool.checked", "agent": "system", "data": SHELL_TOOL},
        pred_ev,
        ana_ev,
        {"kind": "mesh.created", "agent": "system", "design_version": 1, "data": m1},
        {"kind": "analysis.finished", **strength, "design_version": 1, "data": shell1},
        {"kind": "design.created", "agent": "designer", "design_version": 2, "data": d2},
        {"kind": "prediction.recorded", **strength, "design_version": 2, "data": pred2},
        {"kind": "analysis.finished", **strength, "design_version": 2, "data": beam2},
        {"kind": "mesh.created", "agent": "system", "design_version": 2, "data": m2},
        {"kind": "analysis.finished", **strength, "design_version": 2, "data": shell2},
    ]
    base = {"run_id": run["run_id"], "ts": ana_ev["ts"]}
    events = [copy.deepcopy(run), copy.deepcopy(design_ev)]
    t = design_ev["t"]
    for ev in more:
        t += 0.5
        e = {**base, **copy.deepcopy(ev), "t": t}
        e.pop("seq", None)
        events.append(e)
    events.append({**base, "t": t + 0.5, "kind": "run.finished", "agent": "system", "data": {"status": "done", "stop_reason": "shape example"}})
    for i, e in enumerate(events):
        e["seq"] = i
    return events, {1: m1, 2: m2}, {1: shell1, 2: shell2}
