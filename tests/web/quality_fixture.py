"""The mesh of mesh_fixture with a mesh check (mesh.quality) and its analysis with a
setup (analysis.setup), for the screen's tests.

Shape example. Two flaws are put in on purpose: one element is squashed (two of its
nodes moved, so it is a thin, sheared quad) and one element is taken out (a hole, whose
four sides become free edges). Every value of the check is computed here from the
mesh's nodes and elements:

- aspect_ratio: the element's longest side over its shortest side
- min_angle_deg / max_angle_deg: the smallest / largest corner angle of the element
- p95: the value 95 % of the elements are at or better than
- over_limit: elements past the limit (larger, or smaller for min_angle_deg)
- free_edges: sides used by one element only; coincident_nodes: nodes at the same
  place (to 0.001 mm) as an earlier node; unconnected_groups: groups of elements
  joined through shared nodes, less one

The limits (5, 30 deg, 150 deg) are shape-example limits, not taken from a solver's
manual. The members of the smaller section have 3 mm walls in the mesh (2 mm in the
design), so the thickness view has two colours. The setup's masses are computed from the mesh (element area x wall thickness
x density) and from the design (member length x section area x density).
"""

import copy
import math

from tests.web.mesh_fixture import _cross, _dot, _sub, mesh_events

LIMITS = {"aspect_ratio": 5.0, "min_angle_deg": 30.0, "max_angle_deg": 150.0}  # shape example
LOW_IS_BAD = {"min_angle_deg"}
THICK_SECTION, THICK_MM = "rt40x40x2", 3.0  # shape example: thicker in the mesh than in the design


def _norm(a):
    return math.sqrt(_dot(a, a))


def element_metrics(pts):
    """aspect_ratio, min_angle_deg, max_angle_deg of one element from its corner points."""
    n = len(pts)
    sides = [_norm(_sub(pts[(i + 1) % n], pts[i])) for i in range(n)]
    angles = []
    for i in range(n):
        u = _sub(pts[(i - 1) % n], pts[i])
        v = _sub(pts[(i + 1) % n], pts[i])
        c = _dot(u, v) / (_norm(u) * _norm(v))
        angles.append(math.degrees(math.acos(max(-1.0, min(1.0, c)))))
    return {"aspect_ratio": max(sides) / min(sides), "min_angle_deg": min(angles), "max_angle_deg": max(angles)}


def past(name, v):
    lim = LIMITS[name]
    return v < lim if name in LOW_IS_BAD else v > lim


def p95(name, values):
    """The value 95 % of the elements are at or better than."""
    s = sorted(values, reverse=name in LOW_IS_BAD)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def free_edges(elements):
    uses = {}
    for e in elements:
        for i in range(len(e)):
            k = tuple(sorted((e[i], e[(i + 1) % len(e)])))
            uses[k] = uses.get(k, 0) + 1
    return sorted(k for k, c in uses.items() if c == 1)


def coincident_nodes(nodes):
    seen = set()
    extra = 0
    for p in nodes:
        k = tuple(round(c * 1000) for c in p)
        if k in seen:
            extra += 1
        seen.add(k)
    return extra


def element_groups(elements, n_nodes):
    parent = list(range(n_nodes))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for e in elements:
        for b in e[1:]:
            ra, rb = find(e[0]), find(b)
            if ra != rb:
                parent[ra] = rb
    return len({find(e[0]) for e in elements})


def quality_of(mesh):
    P = mesh["nodes_mm"]
    per = {k: [] for k in LIMITS}
    for e in mesh["elements"]:
        m = element_metrics([P[i] for i in e])
        for k in LIMITS:
            per[k].append(round(m[k], 4))
    metrics = {}
    for k, vals in per.items():
        worst = min(range(len(vals)), key=vals.__getitem__) if k in LOW_IS_BAD else max(range(len(vals)), key=vals.__getitem__)
        metrics[k] = {
            "worst": vals[worst],
            "p95": p95(k, vals),
            "limit": LIMITS[k],
            "over_limit": sum(1 for v in vals if past(k, v)),
            "worst_element": worst,
        }
    topology = {
        "free_edges": len(free_edges(mesh["elements"])),
        "coincident_nodes": coincident_nodes(P),
        "unconnected_groups": element_groups(mesh["elements"], len(P)) - 1,
    }
    verdict = "warn" if any(m["over_limit"] for m in metrics.values()) else "ok"
    return {
        "verdict": verdict,
        "metrics": metrics,
        "per_element": per,
        "topology": topology,
        "notes": "shape example: limits 5 / 30 deg / 150 deg are not from a solver manual; the tubes are not joined at the frame nodes, so their end rings are free edges and each tube is its own group",
    }


def _element_area(pts):
    a = 0.0
    for t in range(1, len(pts) - 1):
        a += _norm(_cross(_sub(pts[t], pts[0]), _sub(pts[t + 1], pts[0]))) / 2
    return a


def model_mass_kg(mesh, design):
    mats = {m["id"]: design["materials"][m["material"]]["density_kg_m3"] for m in design["members"]}
    P = mesh["nodes_mm"]
    total = 0.0
    for e, mid in zip(mesh["elements"], mesh["element_member"]):
        total += _element_area([P[i] for i in e]) * mesh["member_thickness_mm"][mid] * mats[mid] * 1e-9
    return total


def design_mass_kg(design, members):
    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    total = 0.0
    for m in design["members"]:
        if m["id"] not in members:
            continue
        d = design["sections"][m["section"]]["dims_mm"]
        area = d["b"] * d["h"] - (d["b"] - 2 * d["t"]) * (d["h"] - 2 * d["t"])
        length = _norm(_sub(nodes[m["node_b"]], nodes[m["node_a"]]))
        total += length * area * design["materials"][m["material"]]["density_kg_m3"] * 1e-9
    return total


def _middle_element(mesh, member):
    """The element of a member nearest its middle (by element centroid)."""
    P = mesh["nodes_mm"]
    idx = [i for i, m in enumerate(mesh["element_member"]) if m == member]
    cen = [[sum(P[n][k] for n in mesh["elements"][i]) / 4 for k in range(3)] for i in idx]
    mid = [sum(c[k] for c in cen) / len(cen) for k in range(3)]
    return idx[min(range(len(idx)), key=lambda j: _norm(_sub(cen[j], mid)))]


def _nearest_node(P, xyz, skip=()):
    return min((i for i in range(len(P)) if i not in skip), key=lambda i: _norm(_sub(P[i], xyz)))


def quality_events(target=8000):
    """events (the sample run with a mesh.created whose mesh has a quality and an analysis
    with a setup), the mesh record, the analysis record, and what was put in on purpose:
    {squashed: element index, hole_edges: the four free sides of the hole}."""
    events, mesh = mesh_events(target)
    design = next(e for e in events if e["kind"] == "design.created")["data"]
    ana = next(e for e in events if e["kind"] == "analysis.finished")["data"]
    members = [m["id"] for m in design["members"] if m["id"] in mesh["member_thickness_mm"]]
    on_mesh = [f for f in ana["fields"] if f.get("mesh_id") == mesh["mesh_id"] and "per_element" in f]

    # the hole: one element of the second member taken out (and its value from each field)
    hole = _middle_element(mesh, members[1])
    hole_nodes = mesh["elements"][hole]
    hole_edges = sorted(tuple(sorted((hole_nodes[i], hole_nodes[(i + 1) % 4]))) for i in range(4))
    for key in ("elements", "element_member"):
        del mesh[key][hole]
    for f in on_mesh:
        del f["per_element"][hole]

    # the squashed element: in the first member, its third and fourth nodes moved
    # towards the first two (to 15 % of the side), the fourth also half way along
    sq = _middle_element(mesh, members[0])
    P = mesh["nodes_mm"]
    q0, q1, q2, q3 = (P[i] for i in mesh["elements"][sq])
    new2 = [q1[k] + 0.15 * (q2[k] - q1[k]) for k in range(3)]
    new3 = [q0[k] + 0.15 * (q3[k] - q0[k]) + 0.5 * (q1[k] - q0[k]) for k in range(3)]
    e = mesh["elements"][sq]
    P[e[2]] = [round(c, 3) for c in new2]
    P[e[3]] = [round(c, 3) for c in new3]

    # the members of the smaller section get 3 mm walls in the mesh (the design's section
    # says 2 mm), so the thickness view has two colours and the masses differ
    for m in design["members"]:
        if m["id"] in mesh["member_thickness_mm"] and m["section"] == THICK_SECTION:
            mesh["member_thickness_mm"][m["id"]] = THICK_MM

    mesh["quality"] = quality_of(mesh)

    # setup: four constraints at the mesh nodes nearest the wheels' frame nodes; a point
    # load spread to the six nodes nearest it (weights by inverse distance, summing to 1)
    # and a load with a moment at a frame node; checks computed from the mesh and design
    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    fixed = []
    for s, dofs in zip(design["supports"], (["ux", "uy", "uz", "rx", "ry", "rz"], ["uy", "uz"], ["uz"], ["uz", "rx"])):
        fixed.append({"node": _nearest_node(P, nodes[s["frame_node"]], {c["node"] for c in fixed}), "dofs": dofs})
    lo = [min(p[k] for p in P) for k in range(3)]
    hi = [max(p[k] for p in P) for k in range(3)]
    centre = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hi[2]]
    near = sorted(range(len(P)), key=lambda i: _norm(_sub(P[i], centre)))[:6]
    inv = [1.0 / max(1e-6, _norm(_sub(P[i], centre))) for i in near]
    weights = [w / sum(inv) for w in inv]
    loads = [
        {"label": "載せた重さ (形の見本)", "origin_mm": [round(c, 3) for c in centre], "vector_N": [0.0, 0.0, -3000.0],
         "spread_to": [{"node": i, "weight": round(w, 6)} for i, w in zip(near, weights)]},
        {"label": "横の力とねじり (形の見本)", "origin_mm": nodes[design["members"][0]["node_b"]], "vector_N": [0.0, 800.0, 0.0],
         "moment_Nmm": [0.0, 0.0, 250000.0]},
    ]
    total = [sum(l["vector_N"][k] for l in loads) for k in range(3)]
    ana["setup"] = {
        "mesh_id": mesh["mesh_id"],
        "constraints": fixed,
        "loads": loads,
        "inertia_relief": False,
        "checks": {
            "model_mass": {"value": round(model_mass_kg(mesh, design), 4), "unit": "kg"},
            "design_mass": {"value": round(design_mass_kg(design, set(members)), 4), "unit": "kg"},
            "applied_load_total": {"value": round(_norm(total), 4), "unit": "N"},
        },
    }
    return events, mesh, ana, {"squashed": sq, "hole_edges": hole_edges}


def failed_events():
    """The same mesh judged fail: the solver did not solve, so the analysis failed and
    has no fields or results."""
    events, mesh, ana, flaws = quality_events()
    events = copy.deepcopy(events)
    m = next(e for e in events if e["kind"] == "mesh.created")["data"]
    m["quality"]["verdict"] = "fail"
    a = next(e for e in events if e["kind"] == "analysis.finished")["data"]
    a.update(status="failed", error="mesh check failed (shape example)", results=[])
    for k in ("fields", "vectors"):
        a.pop(k, None)
    return events, m
