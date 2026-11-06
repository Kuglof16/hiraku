"""NVH (modal) data for the viewer: python -m viewer.src.nvh <run dir> [--out viewer/data]

Field keys added to the common object (see common.py):
    modes [{label, f_Hz, shape (packed f32, n x 3, in frame.node_ids order), amp_max, share_bending}]
        shape: the record's mode_shape per_node vectors (dimensionless, as stored);
        amp_max: the largest node |vector| of that mode (the page scales by it)
    frf {channel, x (packed), y (packed), x_unit, y_unit, note}
    lines [{label, f_Hz}]   excitation frequencies from the record's results (wheel, motor)
    each mode also has (members in frame.member_ids order; see member_strain):
        strain {axial, bending, total (lists), max, per_mm, has_rotations}
            relative strain per member, total / max in 0..1 on the page; per_mm = the largest total
            strain per 1 mm of the largest node's motion (strain is linear in the mode amplitude)
        energy {share (list, null where the section is unknown), top [{member, share, axial_part}]}
    strain_method: text, the method as shown on the page
"""

from __future__ import annotations

import math
import sys

from viewer.src import common, names

TOP = 5


# ---------------------------------------------------------------- section properties
def section_props(sec: dict | None) -> dict | None:
    """A (mm^2), I_v and I_s (mm^4: bending in the section's h direction and in its b direction), c_v, c_s
    (mm, half sizes). rect_tube {b, h, t} and round_tube {d, t}; None for anything else."""
    if not sec:
        return None
    d = sec.get("dims_mm", {})
    if sec.get("shape") == "rect_tube" and {"b", "h", "t"} <= set(d):
        b, h, t = float(d["b"]), float(d["h"]), float(d["t"])
        bi, hi = b - 2 * t, h - 2 * t
        return {"A": b * h - bi * hi, "I_v": (b * h ** 3 - bi * hi ** 3) / 12.0, "I_s": (h * b ** 3 - hi * bi ** 3) / 12.0,
                "c_v": h / 2.0, "c_s": b / 2.0}
    if sec.get("shape") == "round_tube" and {"d", "t"} <= set(d):
        D, t = float(d["d"]), float(d["t"])
        di = D - 2 * t
        I = math.pi * (D ** 4 - di ** 4) / 64.0
        return {"A": math.pi * (D ** 2 - di ** 2) / 4.0, "I_v": I, "I_s": I, "c_v": D / 2.0, "c_s": D / 2.0}
    return None


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return [c / n for c in a] if n > 0 else [0.0, 0.0, 0.0]


def local_axes(e):
    """The member's 'vertical' axis v (h direction) and sideways axis s: v = global Z made square to the
    member, as K1 orients a horizontal member (hiraku/solvers/k1_frame/model.py: local y up, h vertical);
    a member within 25 deg of vertical uses global X instead (the viewer's choice)."""
    ref = [1.0, 0.0, 0.0] if abs(e[2]) > math.cos(math.radians(25.0)) else [0.0, 0.0, 1.0]
    v = _unit(_sub(ref, [c * _dot(ref, e) for c in e]))
    return v, _cross(e, v)


STRAIN_METHOD = (
    "Strain per member from the mode shape, which has node translations only (no rotations in the record). "
    "Axial: (u_b - u_a) . e / L. Bending: each node's rotation is estimated as the mean of the chord rotations "
    "e x (u_b - u_a) / L of the members meeting there (rigid joints); a member's end rotation is that node rotation "
    "less its own chord rotation (twist dropped), and the curvature at an end is (4 phi_near + 2 phi_far) / L "
    "(Euler-Bernoulli, a cubic between the ends), times the half depth of the section in that direction. "
    "Total = |axial| + the larger end's bending, normalised to the mode's largest member (relative: the mode has no "
    "physical size). Strain energy: EA d^2 / 2L + (EI/L)(2 phi_a^2 + 2 phi_a phi_b + 2 phi_b^2) in each bending "
    "direction, h taken as vertical as in K1 (members within 25 deg of vertical: h along x, the viewer's choice); "
    "torsion is not included (no rotations in the record).")


def member_strain(design: dict, frame: dict, shape: dict[str, list[float]]) -> dict:
    """Per member (frame.member_ids order) axial, bending and total strain and strain energy for a shape
    given as node -> translation (STRAIN_METHOD)."""
    xyz = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    dm = {m["id"]: m for m in design.get("members", [])}
    secs, mats = design.get("sections", {}), design.get("materials", {})
    ids = frame["node_ids"]
    u = {nid: [float(c) for c in shape.get(nid, [0.0, 0.0, 0.0])] for nid in ids}
    geo = []
    rot_sum = {nid: [0.0, 0.0, 0.0] for nid in ids}
    rot_n = {nid: 0 for nid in ids}
    for (ia, ib), mid in zip(frame["members"], frame["member_ids"]):
        a, b = ids[ia], ids[ib]
        d = _sub(xyz[b], xyz[a])
        L = math.sqrt(_dot(d, d))
        e = _unit(d)
        du = _sub(u[b], u[a])
        chord = [c / L for c in _cross(e, du)] if L > 0 else [0.0, 0.0, 0.0]
        geo.append((a, b, L, e, du, chord))
        for nid in (a, b):
            rot_sum[nid] = [x + y for x, y in zip(rot_sum[nid], chord)]
            rot_n[nid] += 1
    rot = {nid: [c / rot_n[nid] for c in rot_sum[nid]] if rot_n[nid] else [0.0, 0.0, 0.0] for nid in ids}
    axial, bend, total, energy, en_axial = [], [], [], [], []
    for (a, b, L, e, du, chord), mid in zip(geo, frame["member_ids"]):
        if L <= 0:
            axial.append(0.0); bend.append(0.0); total.append(0.0); energy.append(None); en_axial.append(None)
            continue
        ea = _dot(du, e) / L
        v, s = local_axes(e)
        pa, pb = _sub(rot[a], chord), _sub(rot[b], chord)
        # rotation about s bends in the v (h) direction, about v bends in the s (b) direction
        phi = {"v": (_dot(pa, s), _dot(pb, s)), "s": (_dot(pa, v), _dot(pb, v))}
        m = dm.get(mid, {})
        sp = section_props(secs.get(m.get("section")))
        E = (mats.get(m.get("material")) or {}).get("E_MPa")
        c = {"v": sp["c_v"], "s": sp["c_s"]} if sp else {"v": 0.0, "s": 0.0}
        kb = 0.0
        for k, (fa, fb) in phi.items():
            kb = max(kb, abs(4 * fa + 2 * fb) / L * c[k], abs(4 * fb + 2 * fa) / L * c[k])
        axial.append(abs(ea))
        bend.append(kb)
        total.append(abs(ea) + kb)
        if sp and E:
            ua = E * sp["A"] * (ea * L) ** 2 / (2 * L)
            ub = sum(E * sp["I_" + k] / L * (2 * fa * fa + 2 * fa * fb + 2 * fb * fb) for k, (fa, fb) in phi.items())
            energy.append(ua + ub)
            en_axial.append(ua)
        else:
            energy.append(None)
            en_axial.append(None)
    return {"axial": axial, "bending": bend, "total": total, "energy": energy, "energy_axial": en_axial}


PART_MOTION_NOTE = "部品はモード形に自分の自由度を持たない。載っている節点と一緒に動かしている (1 節点はその並進、複数は平均の並進 + 当てはめた剛体回転)。"


def node_xyz_of(design: dict) -> dict[str, list[float]]:
    """Design nodes and the wheel centre nodes wc:<support> (K1's wheel_node, at the support's position)."""
    out = {n["id"]: [float(c) for c in n["xyz_mm"]] for n in design.get("nodes", [])}
    for s in design.get("supports", []):
        out.setdefault(f"wc:{s['id']}", [float(c) for c in s.get("position_mm", [0.0, 0.0, 0.0])])
    return out


def part_motion(parts: list[dict], ledger: dict, design: dict, per_node: dict[str, list[float]]) -> dict:
    """Each part's rigid motion in one mode shape (common.rigid_fit on the nodes it moves with,
    common.motion_nodes): c (fit centre, mm), t (translation), w (rotation vector) per unit of the shape, and
    rel = |motion at the box centre| / the largest of the parts (0 for parts that stay)."""
    xyz = node_xyz_of(design)
    has = lambda n: n in per_node and n in xyz
    out = {"nodes": [], "c": [], "t": [], "w": [], "rel": []}
    mags = []
    for p, e in zip(parts, ledger["parts"]):
        ns = common.motion_nodes(e, design, has)
        if ns:
            c, t, w = common.rigid_fit([xyz[n] for n in ns], [[float(v) for v in per_node[n]] for n in ns])
        else:
            c, t, w = list(p["c"]), [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        r = [p["c"][k] - c[k] for k in range(3)]
        u = [t[0] + w[1] * r[2] - w[2] * r[1], t[1] + w[2] * r[0] - w[0] * r[2], t[2] + w[0] * r[1] - w[1] * r[0]]
        mags.append(math.sqrt(sum(x * x for x in u)))
        out["nodes"].append(ns)
        out["c"].append(c)
        out["t"].append(t)
        out["w"].append(w)
    mx = max(mags, default=0.0)
    out["rel"] = [m / mx if mx > 0 else 0.0 for m in mags]
    return out


def wheels_of(design: dict, per_node: dict[str, list[float]] | None = None) -> list[dict]:
    """The supports drawn as tyres with their spring as K1 has it (hiraku.solvers.k1_frame.model
    restrain_on_wheels: a spring from the wheel node to a fixed ground point at the support's position less its
    radius in z). node: the node whose motion the tyre takes (wc:<id> when the record holds it, else the
    support's frame node)."""
    out = []
    for s in design.get("supports", []):
        pos = [float(c) for c in s.get("position_mm", [0.0, 0.0, 0.0])]
        r = float(s.get("radius_mm", 0.0) or 0.0)
        susp = s.get("suspension", {}) or {}
        k = susp.get("stiffness_N_per_mm")
        wc = f"wc:{s['id']}"
        node = wc if per_node is not None and wc in per_node else s.get("frame_node")
        out.append({"id": s["id"], "c": pos, "r": r, "width": float(s.get("width_mm", 0.0) or 0.0), "node": node,
                    "spring": bool(susp.get("type") in ("spring", "kc_table") and k), "k_N_per_mm": k,
                    "ground": [pos[0], pos[1], pos[2] - (r or 100.0)]})
    return out


def build(args) -> dict:
    vdir, rec = common.find_analysis(args.run, "modal", version=args.version, analysis_id=args.analysis)
    design = common.read_json(vdir / "design.json")
    obj = common.base_object("nvh", names.title("nvh", "フレームの固有振動と周波数応答"), args.run, vdir, rec, design)
    obj["model"] = "beam"   # the page's NVH field also takes nvh_shell.py's "shell"
    ids = obj["frame"]["node_ids"]
    lc =common.load_case_of(vdir, rec["load_case_id"])
    obj["ledger"] = common.parts_ledger("modal", design, lc, rec, obj["parts"])
    effects = {e["name"]: e.get("value") for e in rec.get("effects", [])}
    modes = []
    missing = set()
    wheels = None
    for i, f in enumerate(x for x in rec.get("fields", []) if x.get("kind") == "mode_shape"):
        pn = f.get("per_node", {})
        flat, amp = [], 0.0
        for nid in ids:
            v = pn.get(nid)
            if v is None:
                missing.add(nid)
                v = [0.0, 0.0, 0.0]
            flat.extend(float(c) for c in v)
            amp = max(amp, math.sqrt(sum(float(c) ** 2 for c in v)))
        n = i + 1
        ms = member_strain(design, obj["frame"], pn)
        smax = max(ms["total"], default=0.0)
        en = [x for x in ms["energy"] if x is not None]
        etot = sum(en)
        share = [None if x is None or etot <= 0 else x / etot for x in ms["energy"]]
        mids = obj["frame"]["member_ids"]
        top = sorted((j for j in range(len(mids)) if share[j] is not None), key=lambda j: -share[j])[:TOP]
        modes.append({"label": f.get("label", f"mode {n}"), "f_Hz": float(f["frequency_Hz"]),
                      "shape": common.pack_f32(flat, [len(ids), 3]), "amp_max": amp,
                      "share_bending": effects.get(f"mode {n}: share of the shape that bends the frame"),
                      "strain": {"axial": ms["axial"], "bending": ms["bending"], "total": ms["total"], "max": smax,
                                 "per_mm": smax / amp if amp > 0 else 0.0, "has_rotations": False},
                      "energy": {"share": share, "known": len(en), "top": [
                          {"member": mids[j], "share": share[j],
                           "axial_part": ms["energy_axial"][j] / ms["energy"][j] if ms["energy"][j] else 0.0} for j in top]}})
        pm = part_motion(obj["parts"], obj["ledger"], design, pn)
        modes[-1]["parts"] = {"t": common.pack_f32([c for v in pm["t"] for c in v], [len(pm["t"]), 3]),
                              "w": common.pack_f32([c for v in pm["w"] for c in v], [len(pm["w"]), 3]),
                              "c": common.pack_f32([c for v in pm["c"] for c in v], [len(pm["c"]), 3]), "rel": pm["rel"]}
        if wheels is None:
            wheels = wheels_of(design, pn)
            obj["part_nodes"] = pm["nodes"]
        modes[-1]["wheels"] = [[float(c) for c in pn.get(w["node"], [0.0, 0.0, 0.0])] if w["node"] else [0.0, 0.0, 0.0]
                               for w in wheels]
    if not modes:
        raise LookupError(f"{rec['_path']}: no mode_shape fields")
    obj["modes"] = modes
    obj["wheels"] = wheels or []
    obj["strain_method"] = STRAIN_METHOD
    frf = next((s for s in rec.get("series", []) or [] if s.get("channel", "").startswith("frequency_response")), None)
    if frf is not None:
        x = frf.get("x", {})
        note = next((k for k in effects if k.startswith("frequency response")), "")
        obj["frf"] = {"channel": frf["channel"], "x": common.pack_f32(x.get("values", [])),
                      "y": common.pack_f32(frf["values"]), "x_unit": x.get("unit", "Hz"),
                      "y_unit": frf.get("unit", "1"), "note": note}
    lines = []
    for q, lab in (("wheel_rotation_frequency", "wheel rotation"), ("motor_rotation_frequency", "motor rotation")):
        v = common.result(rec, q)
        if v is not None:
            lines.append({"label": lab, "f_Hz": v})
    obj["lines"] = lines
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        {"label": "excitation", "value": ", ".join(f"{l['label']} {l['f_Hz']:.2f} Hz" for l in lines) or "none in the record",
         "source": "the record's results (wheel_rotation_frequency, motor_rotation_frequency) at the case's speed"},
        {"label": "modes", "value": f"{len(modes)} mode shapes stored of {sum(1 for r in rec['results'] if r['quantity'].startswith('mode_'))} frequencies",
         "source": "record fields (mode_shape, per_node vec3 at the frame nodes)"},
    ]
    obj["notes"].append("モード形は大きさを持たない (相対値)。変形は表示用の倍率で、実寸ではない。")
    obj["notes"].append("ひずみは節点の並進だけから求めた推定 (曲げは推定、ねじりは入らない)、そのモードの最大を 1 とした相対値。")
    if missing:
        obj["notes"].append(f"{len(missing)} frame nodes have no mode vector in the record (drawn still): {', '.join(sorted(missing))}")
    obj["notes"].append("Motion: x(t) = node + scale x shape / |shape|max x sin(phase); colour = member strain / the mode's "
                        "largest (or, switched, |shape| / |shape|max at each node).")
    obj["notes"].append(PART_MOTION_NOTE)
    if obj["wheels"]:
        obj["notes"].append("タイヤはばね下の節点 (記録に車輪中心の節点 wc があればそれ、なければ支持点のフレーム節点) と一緒に動く。"
                            "ばねは K1 と同じく、その節点から地面の固定点 (支持点の位置 − 半径) まで。")
    return obj


if __name__ == "__main__":
    common.main("nvh", build, argv=sys.argv[1:])
