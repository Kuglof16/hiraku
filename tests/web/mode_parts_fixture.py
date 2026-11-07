"""A shell mode shape whose per_node gives the design nodes (as modal_shell writes it), for the
screen's tests of the parts moving with their carried_by nodes.

Shape example: the design is tests/web/occupant_designs' four-wheeler (an occupant in segments,
seats, battery, steering, cargo); its rect_tube members are meshed as in mesh_fixture. The mode
shape is made up here from the node positions (a bend along x and a twist about it), scaled so
the largest mesh node vector is 1; per_node holds, for each design node a part is carried by,
the mean of the mesh nodes within JOINT_MM of it (on the mesh's scale, as modal_shell does with
its joints), and "<support>.wheel_center" for each wheel. It shows the record's shape, not a
result.
"""

import copy
import json
import math
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS
from tests.web.mesh_fixture import frame_mesh

DESIGN = Path(__file__).resolve().parent / "occupant_designs" / "offroad-4w-v1-9ee90a9.json"
JOINT_MM = 60.0
WHEEL_MOVE = {"FL": [0.0, 0.0, 0.5], "FR": [0.0, 0.0, -0.5], "RL": [0.0, 0.0, 0.3], "RR": [0.0, 0.0, -0.3]}


def design_of():
    d = json.loads(DESIGN.read_text(encoding="utf-8"))
    return d.get("design", d)


def mode_parts_events(target=6000, with_design_nodes=True):
    """The sample run with the four-wheeler's design, its mesh and an analysis holding one mode
    shape on the mesh. Returns (events, mesh, field)."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    run = next(e for e in src if e["kind"] == "run.started")
    design_ev = copy.deepcopy(next(e for e in src if e["kind"] == "design.created"))
    ana_ev = copy.deepcopy(next(e for e in src if e["kind"] == "analysis.finished"))
    design = design_of()
    design_ev["data"] = design
    size = 8.0
    mesh = frame_mesh(design, size)
    while len(mesh["elements"]) > target:
        size *= 1.1
        mesh = frame_mesh(design, size)
    pts = mesh["nodes_mm"]
    xs = [p[0] for p in pts]
    x0, x1 = min(xs), max(xs)

    def shape(p):
        u = (p[0] - x0) / (x1 - x0)
        return [0.0, 0.2 * p[2] / 1000.0 * math.sin(math.pi * u), math.sin(math.pi * u) + 0.15 * p[1] / 600.0]

    raw = [shape(p) for p in pts]
    big = max(math.hypot(*v) for v in raw)
    per_mesh = [[round(c / big, 4) + 0.0 for c in v] for v in raw]
    per_node = {}
    if with_design_nodes:
        xyz = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
        carried = sorted({n for p in design["parts"] for n in (p.get("carried_by") or [])})
        for n in carried:
            ids = [i for i, p in enumerate(pts) if math.dist(p, xyz[n]) <= JOINT_MM]
            if ids:
                per_node[n] = [round(sum(per_mesh[i][j] for i in ids) / len(ids), 4) + 0.0 for j in range(3)]
        for s in design["supports"]:
            per_node[f"{s['id']}.wheel_center"] = WHEEL_MOVE[s["id"]]
    field = {"kind": "mode_shape", "label": "mode 1 (bending): 形の見本", "unit": "1", "frequency_Hz": 21.5,
             "mesh_id": mesh["mesh_id"], "per_mesh_node": per_mesh}
    if per_node:
        field["per_node"] = per_node
    ana = ana_ev["data"]
    ana["fields"] = [field]
    base = {"run_id": run["run_id"], "ts": ana_ev["ts"]}
    created = {**base, "t": ana_ev["t"] - 0.05, "kind": "mesh.created", "agent": "system", "design_version": design["design_version"], "data": mesh}
    events = [copy.deepcopy(run), design_ev, created, {**ana_ev, "design_version": design["design_version"]}]
    for i, e in enumerate(events):
        e["seq"] = i
    return events, mesh, field
