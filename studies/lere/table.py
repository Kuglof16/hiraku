"""The table of DIR/*/result.json.

  python -m studies.lere.table DIR            -> DIR/table.md and DIR/table.json (sets in studies.lere.sets order,
                                                 then any others by name)
  python -m studies.lere.table DIR --modes NAME...   prints the modal digest of those sets"""
import json
import os
import sys

LIM = 143.3
HEAD = ("Step hit (K2 step_climb_dynamic 100 mm 10 km/h, mount_path footprint; criterion 143.3 MPa for the beam and "
        "the hot spots, 1.0 for R9) and shell modes (k1_shell.modal 10 mm, N1 load case; b body, r roll bar, F frame). "
        "Base = v005 + tower_b 35, tower_t 4.5, rail_b 40, gusset_ends 1, gusset_leg 120, gusset_thickness 4.5, "
        "mount_plate_t 9, mount_weld_leg 6.3, HIRAKU_LCA_FULL_WALL=1. Values: largest over the instants (t s). "
        "Mass = parts + members.\n\n")
COLS = ["set", "extra", "mass kg", "LB / RB MPa (t s)", "LE / RE MPa (t s)", "largest hot spot",
        "post base vM MPa (t s)", "beam MPa (where)",
        "R9 plate / weld worst", "post spare mm", "modes 1-8 Hz (kind)", "first frame Hz"]


def _load(d, n):
    with open(os.path.join(d, n, "result.json"), encoding="utf-8") as f:
        return json.load(f)


def row(s):
    """One set's result -> {column: text} plus raw numbers under "_"."""
    ex = ", ".join(f"{k} {v:g}" for k, v in s.get("extra", {}).items()) or "-"
    tyre = s.get("tyre_stiffness", 60)
    ex += f"; tyre {tyre:g}"
    if s.get("adds") is not None:
        ex += "; whole " + ("+".join(s["adds"]) or "(none)")
    raw = {"mass_kg": s.get("mass_kg"), "tyre": tyre, "adds": s.get("adds"), "model_mass_kg": s.get("model_mass_kg")}
    B = s.get("best")
    if B:
        f = lambda k: f"{B[k][0]:.1f} ({B[k][1]:.2f})" if k in B else "-"  # noqa: E731
        posts = [(k[5:], v) for k, v in B.items() if k.startswith("post ")]
        if posts:
            pk, pv = max(posts, key=lambda kv: kv[1][0])
            raw["post_base_MPa"] = pv[0]
            hm_post = f"{pv[0]:.1f} ({pk} t {pv[1]:.2f})"
        else:
            hm_post = "-"
        lbrb, lere = f"{f('hot LB')} / {f('hot RB')}", f"{f('hot LE')} / {f('hot RE')}"
        hm = f"{B['hot max'][0]:.1f} {B['hot max'][2]} t {B['hot max'][1]:.2f}" if "hot max" in B else "-"
        bm = s.get("beam_max_stress_over_time") or []
        beam = f"{bm[0]['value']:.1f} ({bm[0]['where']})" if bm else "-"
        pls = [(k, v) for k, v in B.items() if k.startswith("R9 plate")]
        wes = [(k, v) for k, v in B.items() if k.startswith("R9 weld")]
        if pls and wes:
            plk, pl = max(pls, key=lambda kv: kv[1][0])
            _, we = max(wes, key=lambda kv: kv[1][0])
            r9 = f"{pl[0]:.2f} ({plk[9:]} t {pl[1]:.2f}) / {we[0]:.2f} (t {we[1]:.2f})"
            raw.update(r9_plate=pl[0], r9_weld=we[0])
        else:
            r9 = "-"
        for k in ("hot LB", "hot RB", "hot LE", "hot RE", "hot max"):
            raw[k.replace(" ", "_")] = B[k][0] if k in B else None
        raw["beam_MPa"] = bm[0]["value"] if bm else None
    else:
        lbrb = lere = hm = beam = r9 = hm_post = "not run"
    m = s.get("modal")
    if m:
        k1 = {"body": "b", "frame": "F", "roll bar": "r"}
        modes = " ".join(f"{x:.2f}{k1[k]}" for x, k in zip(m["frequencies"][:8], m["kinds"][:8]))
        ff = f"{m['first_frame_mode']:.2f} (mode {m['first_frame_index']})" if m.get("first_frame_mode") else "-"
        raw.update(frequencies=m["frequencies"][:8], kinds=m["kinds"][:8], first_frame_Hz=m.get("first_frame_mode"))
    else:
        modes = ff = "not run"
    sp = s.get("post_spare_mm", {})
    raw["post_spare_mm"] = min(sp.values()) if sp else None
    vals = [s.get("name", "?"), ex, f"{s['mass_kg']:.1f}", lbrb, lere, hm, hm_post, beam, r9,
            f"{min(sp.values())}" if sp else "-", modes, ff]
    return {**dict(zip(COLS, vals)), "_": raw}


def modes(d, n):
    m = _load(d, n)["modal"]
    print("==", n, "first frame", m["first_frame_mode"], "mode", m["first_frame_index"])
    for i, g in enumerate(m["digest"]):
        print(f" {i + 1} {m['frequencies'][i]:.3f} {m['kinds'][i]:8s} xyz {g['motion_xyz_share']} groups {g['groups']}")
        if i >= 3:
            print("     strain", g["strain_share"][:6], "\n     motion", g["motion"][:4], "\n     hot", g["hot_member"],
                  g["hot_xyz"])


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    d = a[0]
    if len(a) > 1 and a[1] == "--modes":
        for n in a[2:]:
            modes(d, n)
        return
    from .sets import SETS
    found = sorted(n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n, "result.json")))
    order = [n for n in SETS if n in found] + [n for n in found if n not in SETS]
    rows = [row(_load(d, n)) for n in order]
    txt = "\n".join(["| " + " | ".join(COLS) + " |", "|" + "---|" * len(COLS)] +
                    ["| " + " | ".join(r[c] for c in COLS) + " |" for r in rows])
    print(txt)
    with open(os.path.join(d, "table.md"), "w", encoding="utf-8") as f:
        f.write(HEAD + txt + "\n")
    with open(os.path.join(d, "table.json"), "w", encoding="utf-8") as f:
        json.dump([{"set": r["set"], "doc": SETS.get(r["set"], {}).get("doc"), "columns": {c: r[c] for c in COLS},
                    "values": r["_"]} for r in rows], f, indent=1)


if __name__ == "__main__":
    main()
