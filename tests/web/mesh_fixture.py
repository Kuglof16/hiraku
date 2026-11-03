"""A shell mesh of the sample design's frame and fields on it, for the screen's tests.

Shape example: each member's rectangular tube is meshed as its closed wall (rings of
nodes around the section's mid-line, quads between rings), with the element size
picked so the mesh has about `target` elements. The values on it are made up here
from the sample's beam stress and the node positions; they show the record's shape,
not a result.
"""

import copy
import json
import math

from hiraku.webserve.server import SAMPLE_EVENTS


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return [x / n for x in a]


def _ring(b, h, size):
    """Points (y, z) around a b x h rectangle, counter-clockwise, at most `size` apart."""
    corners = [(-b / 2, -h / 2), (b / 2, -h / 2), (b / 2, h / 2), (-b / 2, h / 2)]
    pts = []
    for i in range(4):
        (y0, z0), (y1, z1) = corners[i], corners[(i + 1) % 4]
        n = max(1, math.ceil(math.hypot(y1 - y0, z1 - z0) / size))
        pts += [(y0 + (y1 - y0) * k / n, z0 + (z1 - z0) * k / n) for k in range(n)]
    return pts


def frame_mesh(design, size):
    """mesh record of the design's rect_tube members (closed walls), element edge <= size."""
    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    xyz, elements, member_of, thickness = [], [], [], {}
    for m in design["members"]:
        sec = design["sections"][m["section"]]
        if sec["shape"] != "rect_tube":
            continue
        d = sec["dims_mm"]
        a, b = nodes[m["node_a"]], nodes[m["node_b"]]
        axis = _sub(b, a)
        length = math.sqrt(_dot(axis, axis))
        e1 = _unit(axis)
        up = [0.0, 0.0, 1.0] if abs(e1[2]) < 0.9 else [1.0, 0.0, 0.0]
        e2 = _unit(_cross(up, e1))
        e3 = _cross(e1, e2)
        ring = _ring(d["b"] - d["t"], d["h"] - d["t"], size)
        stations = max(1, math.ceil(length / size))
        base = len(xyz)
        for i in range(stations + 1):
            s = length * i / stations
            for y, z in ring:
                xyz.append([a[k] + e1[k] * s + e2[k] * y + e3[k] * z for k in range(3)])
        nr = len(ring)
        first = None
        for i in range(stations):
            for j in range(nr):
                q = [base + i * nr + j, base + (i + 1) * nr + j, base + (i + 1) * nr + (j + 1) % nr, base + i * nr + (j + 1) % nr]
                if first is None:
                    # counter-clockwise seen from outside: the normal points away from the axis
                    p0, p1, p3 = xyz[q[0]], xyz[q[1]], xyz[q[3]]
                    normal = _cross(_sub(p1, p0), _sub(p3, p0))
                    out = _sub(p0, [a[k] + e1[k] * _dot(_sub(p0, a), e1) for k in range(3)])
                    first = _dot(normal, out) < 0
                elements.append(q[::-1] if first else q)
                member_of.append(m["id"])
        thickness[m["id"]] = d["t"]
    return {
        "mesh_id": "mesh-0001",
        "design_version": design["design_version"],
        "element_type": "shell",
        "formulation": "MITC4",
        "element_size_mm": size,
        "nodes_mm": [[round(c, 3) for c in p] for p in xyz],
        "elements": elements,
        "element_member": member_of,
        "member_thickness_mm": thickness,
        "notes": "shape example: closed rectangular tube walls of the sample design (screen tests)",
    }


def mesh_events(target=20000):
    """The sample run with a mesh.created before its analysis, which gets shell stress
    fields on four surfaces and a displacement on the mesh nodes beside its beam stress."""
    events = [copy.deepcopy(json.loads(line)) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    design = next(e for e in events if e["kind"] == "design.created")["data"]
    size = 4.0
    mesh = frame_mesh(design, size)
    while len(mesh["elements"]) > target:
        size *= 1.05
        mesh = frame_mesh(design, size)
    ana_ev = next(e for e in events if e["kind"] == "analysis.finished")
    ana = ana_ev["data"]
    beam = next(f for f in ana["fields"] if f["kind"] == "stress")
    centre = [sum(p[k] for p in mesh["nodes_mm"]) / len(mesh["nodes_mm"]) for k in range(3)]

    def per_element(gain):
        # shape example: the member's beam stress, more where the element is far from the frame's mid-height
        out = []
        for el, mid in zip(mesh["elements"], mesh["element_member"]):
            z = sum(mesh["nodes_mm"][n][2] for n in el) / len(el)
            out.append(round(beam["per_member"][mid] * gain * (0.6 + abs(z - centre[2]) / 40.0), 3))
        return out

    top, mid, bottom = per_element(1.0), per_element(0.5), per_element(0.8)
    fields = [
        {"kind": "stress", "label": "シェルの応力 (形の見本)", "unit": "MPa", "mesh_id": mesh["mesh_id"], "surface": s, "per_element": v}
        for s, v in (("top", top), ("mid", mid), ("bottom", bottom), ("max", [max(x, y) for x, y in zip(top, bottom)]))
    ]
    span = max(abs(p[0] - centre[0]) for p in mesh["nodes_mm"]) or 1.0
    disp = [[0.0, 0.0, round(-0.5 * math.cos(math.pi / 2 * (p[0] - centre[0]) / span), 4)] for p in mesh["nodes_mm"]]
    fields.append({"kind": "displacement", "label": "変位 (形の見本)", "unit": "mm", "mesh_id": mesh["mesh_id"], "per_mesh_node": disp})
    ana["fields"] = ana["fields"] + fields
    base = {"run_id": events[0]["run_id"], "ts": ana_ev["ts"]}
    created = {**base, "seq": 0, "t": ana_ev["t"] - 0.05, "kind": "mesh.created", "agent": "system", "design_version": design["design_version"], "data": mesh}
    i = events.index(ana_ev)
    events = events[:i] + [created] + events[i:]
    for k, e in enumerate(events):
        e["seq"] = k
    return events, mesh
