"""The study's sets: design variables over BASE (common.BASE on v005) and the tyre stiffness (N/mm).

Base (every set): v005 + tower_b 35, tower_t 4.5, rail_b 40, gusset_ends 1, gusset_leg 120, gusset_thickness 4.5,
mount_plate_t 9, mount_weld_leg 6.3, HIRAKU_LCA_FULL_WALL=1; tyre 60 N/mm unless the set says otherwise.

Step-hit sets (the rear rail-end joints LE / RE):
  A      the base re-run (rear tower posts centred on the rail, so rail_b 40 fits tower_b 35)
  A45    A at tyre 45 N/mm
  A90    A at tyre 90 N/mm
  B50    + tower brace (rear tower post to cross_B), its post end at 1/2 of the post's height, the tower's section
  B33    + tower brace, its post end at 1/3 of the post's height
  C32    cross members 3.2 mm wall
  C45    cross members 4.5 mm wall
  Ch60f  cross members 60 mm tall; roll bar feet 70 mm tall (v005's 40 x 60 feet clash at LS2 with cross_h 60)
  E1     rails 100 mm tall (v005: 40 x 80 x 4.5)
  E2     corner gusset legs 150 mm (base 120)
N1-aimed sets (the shell's first frame mode):
  S1g0   roll bar rear stays on, corner gussets off
  S2g0   roll bar rear stays 48.6 x 3.2, corner gussets off
  P2     roll bar tube 48.6 x 3.2
  O1     roll bar offset 200 mm
  H1     roll bar height 1000 mm
  M2     battery at x = -700 mm (mass placement)
Combinations ("D": the best of B with the best of C; the results of B and C decide which is best, so all four
pairs are here, and the cross_h one with each brace):
  D50c32  B50 + C32
  D50c45  B50 + C45
  D33c32  B33 + C32
  D33c45  B33 + C45
  D50h60  B50 + Ch60f
  D33h60  B33 + Ch60f
  DU      upper bound for N1: B50 + C45 + S2g0 + P2 (the strongest reasonable combination; corner gussets off
          because the rear stays run into gusset_LE_front_upper with them on)
"""

BR = {"tower_brace_b": 35, "tower_brace_t": 4.5}  # the tower's section
B50 = {**BR, "tower_brace_at": 0.5}
B33 = {**BR, "tower_brace_at": 0.333}
C32 = {"cross_t": 3.2}
C45 = {"cross_t": 4.5}
CH60F = {"cross_h": 60, "rollbar_foot_h": 70}
S2 = {"rollbar_stays": 1, "rollbar_stay_d": 48.6, "rollbar_stay_t": 3.2}
P2 = {"rollbar_d": 48.6, "rollbar_t": 3.2}


def _s(extra, doc, tyre=60.0):
    return {"extra": dict(extra), "tyre": float(tyre), "doc": doc}


SETS = {
    "A": _s({}, "the base re-run"),
    "A45": _s({}, "the base at tyre 45 N/mm", 45),
    "A90": _s({}, "the base at tyre 90 N/mm", 90),
    "B50": _s(B50, "tower brace, post end at 1/2 of the post's height"),
    "B33": _s(B33, "tower brace, post end at 1/3 of the post's height"),
    "C32": _s(C32, "cross members 3.2 mm wall"),
    "C45": _s(C45, "cross members 4.5 mm wall"),
    "Ch60f": _s(CH60F, "cross members 60 mm tall, roll bar feet 70 mm tall"),
    "E1": _s({"rail_h": 100}, "rails 100 mm tall"),
    "E2": _s({"gusset_leg": 150}, "corner gusset legs 150 mm"),
    "S1g0": _s({"rollbar_stays": 1, "gusset_ends": 0}, "roll bar rear stays on, corner gussets off"),
    "S2g0": _s({**S2, "gusset_ends": 0}, "roll bar rear stays 48.6 x 3.2, corner gussets off"),
    "P2": _s(P2, "roll bar tube 48.6 x 3.2"),
    "O1": _s({"rollbar_offset": 200}, "roll bar offset 200 mm"),
    "H1": _s({"rollbar_height": 1000}, "roll bar height 1000 mm"),
    "M2": _s({"battery_x": -700}, "battery at x -700 mm"),
    "D50c32": _s({**B50, **C32}, "B50 + C32"),
    "D50c45": _s({**B50, **C45}, "B50 + C45"),
    "D33c32": _s({**B33, **C32}, "B33 + C32"),
    "D33c45": _s({**B33, **C45}, "B33 + C45"),
    "D50h60": _s({**B50, **CH60F}, "B50 + Ch60f"),
    "D33h60": _s({**B33, **CH60F}, "B33 + Ch60f"),
    # rail_b 45 (the bracket sets' b90 width): the rear lca brackets' welded length on the rail's bottom wall is
    # about rail_b, so R9 plate / weld depend on it (b90 at rail_b 45: 0.81 / 0.63; A at rail_b 40: 1.00 / 0.80)
    "Ar45": _s({"rail_b": 45}, "the base with rails 45 mm wide"),
    "B50r45": _s({**B50, "rail_b": 45}, "B50 with rails 45 mm wide"),
    "D50c45r45": _s({**B50, **C45, "rail_b": 45}, "D50c45 with rails 45 mm wide"),
    # the recommendation from the 26 sets: tower brace + cross members 60 tall + rails 45 wide, at both ends of the tyre
    # range's upper side (60, the decided value, and 90)
    "REC": _s({**B50, **CH60F, "rail_b": 45}, "B50 + Ch60f + rails 45 mm wide"),
    "REC90": _s({**B50, **CH60F, "rail_b": 45}, "REC at tyre 90 N/mm", 90),
    "REC45": _s({**B50, **CH60F, "rail_b": 45}, "REC at tyre 45 N/mm", 45),
    "DU": _s({**B50, **C45, **S2, **P2, "gusset_ends": 0},
             "N1 upper bound: B50 + C45 + S2g0 (stays 48.6 x 3.2, gussets off) + roll bar 48.6 x 3.2"),
}


def variables(name):
    """BASE + the set's extra variables."""
    from .common import BASE
    return {**BASE, **SETS[name]["extra"]}


def main():
    """python -m studies.lere.sets [--regen] [NAMES...]: list the sets; with --regen regenerate each (light, no
    solve) and print mass / post spare, or the error."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--regen", action="store_true")
    a = ap.parse_args()
    names = a.names or list(SETS)
    if not a.regen:
        for n in names:
            s = SETS[n]
            print(f"{n:8s} tyre {s['tyre']:g}  {s['doc']}  {s['extra'] or ''}")
        return
    from .common import setup_env
    setup_env()
    from .common import regen, mass, spare
    for n in names:
        try:
            d = regen(variables(n))
            print(n, f"mass {mass(d):.2f}", spare(d), flush=True)
        except Exception as ex:
            print(n, "ERROR", type(ex).__name__, str(ex)[:300], flush=True)


if __name__ == "__main__":
    main()
