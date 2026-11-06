"""NVH (modal) data for the viewer: python -m viewer.src.nvh <run dir> [--out viewer/data]

Field keys added to the common object (see common.py):
    modes [{label, f_Hz, shape (packed f32, n x 3, in frame.node_ids order), amp_max, share_bending}]
        shape: the record's mode_shape per_node vectors (dimensionless, as stored);
        amp_max: the largest node |vector| of that mode (the page scales by it)
    frf {channel, x (packed), y (packed), x_unit, y_unit, note}
    lines [{label, f_Hz}]   excitation frequencies from the record's results (wheel, motor)
"""

from __future__ import annotations

import math
import sys

from viewer.src import common


def build(args) -> dict:
    vdir, rec = common.find_analysis(args.run, "modal", version=args.version, analysis_id=args.analysis)
    design = common.read_json(vdir / "design.json")
    obj = common.base_object("nvh", "NVH: frame modes and frequency response", args.run, vdir, rec, design)
    ids = obj["frame"]["node_ids"]
    effects = {e["name"]: e.get("value") for e in rec.get("effects", [])}
    modes = []
    missing = set()
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
        modes.append({"label": f.get("label", f"mode {n}"), "f_Hz": float(f["frequency_Hz"]),
                      "shape": common.pack_f32(flat, [len(ids), 3]), "amp_max": amp,
                      "share_bending": effects.get(f"mode {n}: share of the shape that bends the frame")})
    if not modes:
        raise LookupError(f"{rec['_path']}: no mode_shape fields")
    obj["modes"] = modes
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
    lc = common.load_case_of(vdir, rec["load_case_id"])
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        {"label": "excitation", "value": ", ".join(f"{l['label']} {l['f_Hz']:.2f} Hz" for l in lines) or "none in the record",
         "source": "the record's results (wheel_rotation_frequency, motor_rotation_frequency) at the case's speed"},
        {"label": "modes", "value": f"{len(modes)} mode shapes stored of {sum(1 for r in rec['results'] if r['quantity'].startswith('mode_'))} frequencies",
         "source": "record fields (mode_shape, per_node vec3 at the frame nodes)"},
    ]
    obj["not_computed"] = [
        "Beam modes only: the frame is drawn as straight members between its nodes; the shape between nodes is not stored.",
        "Mode shapes are normalised (no physical amplitude): the scale sets how far the largest node moves on screen.",
        "Part boxes are not drawn: the record holds mode shapes at the frame nodes only.",
    ]
    if missing:
        obj["notes"].append(f"{len(missing)} frame nodes have no mode vector in the record (drawn still): {', '.join(sorted(missing))}")
    obj["notes"].append(f"Motion: x(t) = node + scale x shape / |shape|max x sin(phase); colour = |shape| / |shape|max at each node.")
    return obj


if __name__ == "__main__":
    common.main("nvh", build, argv=sys.argv[1:])
