"""Load cases for the tests the analysis department's tools take (K1, K3, K4):
C1 crash, N1 modal, R3 fatigue, P4 drive_energy, T1 and T2 thermal, H1, H3 and
H5 assembly; and R4 (the roll bar), judged by K2's rollover_beam (R4-rollover). R7 and R8 (load_path)
are in load_path.py, H6 (spray_reach) in spray.py, joined to BUILDERS and PLANS here.

Next to each builder: its tolerance_rel and why, and PLAN (the scripted way to
predict). A prediction's inputs_from works out by code the values its from.<name>
refs need that are not plain reads of the design.
"""

from __future__ import annotations

import copy
import math

from hiraku.catalog.builders.common import BuildError, Record, design_var, entry, loading, number, q, text, tolerance
from hiraku.catalog.loading import LOADING_JA

# ---------------------------------------------------------------- C1 crash

# The hand calculation takes the front as one linear spring (the crush zone);
# the tool runs the bumper stroke first and the crush zone after it, which
# lets the front travel further and shapes the pulse. Same mass and energy,
# a different spring path: 15 %.
C1_TOL = 0.15


# C1 is judged by K2's crash_beam (the frame as beams that yield, fold and break), two load cases from the same run:
# the least room to the occupants, and no piece of the frame breaking off. The largest share of the mass a piece
# broken off the seats' side may have: crash_beam.SPLIT_STOP_SHARE (3 %, HIRAKU's choice, no source).
C1_SPLIT_ID = "C1-split"
C1_SPLIT_LIMIT = 0.03
C1_BREAK_BAND_DEG = 1.0  # HIRAKU's choice: how far up the break angle is moved (one run more) to find the edge


def c1_crash(spec: Record, req: Record, design: Record) -> list[Record]:
    load, keys = loading(req, design)
    if entry(req, "Q65"):
        v, vkey = number(req, "Q65", "km/h"), "Q65"
    else:  # the crash speed is the top speed unless the requirements say otherwise
        v, vkey = number(req, "Q50", "km/h"), "Q50"
    # break_band: crash_beam makes the run again with its break angle 1 deg higher, so the record says when
    # C1's verdict sits on the break angle's edge (a member that breaks or not by the last digits of the arithmetic)
    common = {"test_id": "C1", "domain": spec["domain"],
              "params": {"speed": q(v, "km/h"), "loading": load, "break_band": q(C1_BREAK_BAND_DEG, "deg")},
              "requirement_keys": [vkey, *keys] + (["Q31"] if entry(req, "Q31") else []),
              "tolerance_rel": tolerance(spec, C1_TOL)}
    room = dict(copy.deepcopy(common), load_case_id=f"C1-{v:g}kmh",
                title=f"{v:g} km/h で壁に正面から当たる ({LOADING_JA[load]})",
                criterion={"quantity": "occupant_space_margin", "op": ">=", "limit": q(0.0, "mm"),
                           "basis": "C1: no structure reaches into the occupants' space (the least room to their part "
                                    "boxes over the crash)",
                           "requirement_keys": [vkey]})
    split = dict(copy.deepcopy(common), load_case_id=C1_SPLIT_ID,
                 title=f"{v:g} km/h で壁に正面から当たって、骨組みがちぎれないか ({LOADING_JA[load]})",
                 criterion={"quantity": "frame_split_fraction", "op": "<=", "limit": q(C1_SPLIT_LIMIT, "1"),
                            "basis": f"C1: no piece with more than {C1_SPLIT_LIMIT:.0%} of the mass breaks off the frame "
                                     "(HIRAKU's limit, no source)",
                            "requirement_keys": [vkey]})
    return [room, split]


def c1_split_calc(design: Record, load_case: Record) -> Record:
    """The rail behind the crush zone as a straight column under the crush zone's force (crash.rail_column_split):
    one rail's force, the column's section and length, and the share of the mass ahead of the crush zone's end."""
    import numpy as np

    from hiraku.contract.loading import loading_of, on_board
    from hiraku.solvers.k0_handcalc.library_crash import crush_zone, rail_behind, tube_area, tube_least_I
    from hiraku.solvers.k2_vehicle.crash_beam import build_mesh
    from hiraku.solvers.shelf.crash import occupant_space

    loaded = on_board(design, loading_of(load_case))
    cz = crush_zone(loaded)
    member = rail_behind(loaded, cz["x_end_mm"])
    if member is None:
        raise BuildError("no rail runs back from the crush zone's end")
    sec, mat = loaded["sections"][member["section"]], loaded["materials"][member["material"]]
    if occupant_space(loaded) is None:
        raise BuildError("no occupant on board, so nothing can come into their space")
    nodes = {n["id"]: n["xyz_mm"] for n in loaded["nodes"]}
    length = abs(float(nodes[member["node_a"]][0]) - float(nodes[member["node_b"]][0]))  # the rail member behind
    mesh = build_mesh(loaded)
    ahead = mesh.x0[:, 0] * 1000.0 > cz["x_end_mm"] + 1e-6
    return {"F": q(max(r["force_N"] for r in cz["rails"]), "N"), "A": q(tube_area(sec), "mm^2"),
            "f_y": q(float(mat["yield_MPa"]), "MPa"), "E": q(float(mat["E_MPa"]), "MPa"),
            "I": q(tube_least_I(sec), "mm^4"), "L": q(max(length, 1.0), "mm"),
            "share": q(float(mesh.mass[ahead].sum() / mesh.mass.sum()), "1")}


def c1_calc(design: Record, load_case: Record) -> Record:
    from hiraku.contract.loading import loading_of, on_board
    from hiraku.solvers.shelf.crash import occupant_space

    from hiraku.solvers.k0_handcalc.library_crash import crush_zone

    loaded = on_board(design, loading_of(load_case))
    space = occupant_space(loaded)
    if space is None:
        raise BuildError("no occupant on board, so there is no occupant space to keep")
    # the crush zone's mean crushing force from the front frame's tubes (library_crash.crush_zone), as the tool takes it
    return {"space": q(space, "mm"), "crush_force": q(crush_zone(loaded)["force_N"], "N")}


_C1_ASSUME = ["前のつぶれる所は、枠の管が畳まれてつぶれる平均の力 (剛塑性、戻らない) とみる (バンパーはばね)",
              "つぶれる所が使える長さは、その長さの 70 % (仮定)", "壁は動かない"]
_C1_IN = {"m": "load.mass", "v": "lc.speed", "F_m": "from.crush_force", "k_b": "design.var.bumper_stiffness",
          "stroke": "design.var.bumper_stroke"}
# judged by K2's crash_beam (B); the hand calculation is the straight crush of the light crash tool (A)
C1_PLAN = {
    "tool": "crash_beam",
    "say": "前の枠の管が畳まれてつぶれる平均の力から、乗員の前に残る長さを先に見積もります (判定は梁の骨組みの衝突で)",
    "predictions": [
        {"quantity": "occupant_space_margin", "formula_id": "crash.plastic_occupant_space_margin", "output": "margin",
         "assumptions": _C1_ASSUME + ["つぶれは車の前の端から後ろへ進む", "塔の柱やダッシュが後ろへ曲がって足もとに入ることは見ない"],
         "inputs": dict(_C1_IN, s="from.space"), "inputs_from": c1_calc},
    ],
    "expected_mismatch": {
        "quantity": "occupant_space_margin",
        "source": ("the K2 department's crash_beam runs at 40 km/h, two_cargo: demo v1 -163 mm, the scripted run 150416 "
                   "versions 2-4 -74 to -75 mm (the frame splits at the tower posts), version 5 +57 mm, the real run 160725 "
                   "version 7 -137 mm, against the straight crush's several hundred mm, 2026-11-06"),
        "cause": ("手計算は、前の管がまっすぐ畳まれて平均の力でつぶれ、つぶれは前の端から後ろへ進むとみる。梁の骨組み (B) では、"
                  "レールは塔とアームの取付の所で曲がって折れ、塔の柱とダッシュが後ろへ倒れて、足もとの箱に入る。手計算は"
                  "この曲がりを持たないので、乗員の前の余裕をずっと大きく出す"),
        "fix": ("骨組みが曲がって倒れる形を手で解く式は無い。手計算はまっすぐつぶれる場合の値として残し、合否は梁の骨組みの"
                "値 (乗員の箱までのいちばん小さい余裕) で出す。直すなら、レールを厚く、塔の根元を強く、つぶれる箱を塔より前に"),
        "tool_decides": True,
        "tool_decides_reason": ("骨組みが曲がって倒れる形は手で解けない。手計算 (まっすぐなつぶれ) は値に届かないので、力の道を"
                                "手計算で確かめたうえで、梁の骨組みの値で決める"),
    },
}
C1_SPLIT_PLAN = {
    "tool": "crash_beam",
    "say": "つぶれる所の後ろのレールを、つぶれの力を受けるまっすぐな柱とみて、折れて前がちぎれるかを先に見積もります",
    "predictions": [
        {"quantity": "frame_split_fraction", "formula_id": "crash.rail_column_split", "output": "fraction",
         "assumptions": ["レールはまっすぐな柱で、つぶれの力を軸の向きに受ける (両端はピン)",
                         "塔やアームの取付がレールを曲げる力は入れない"],
         "inputs": {k: f"from.{k}" for k in ("F", "A", "f_y", "E", "I", "L", "share")}, "inputs_from": c1_split_calc},
    ],
    # No expected mismatch: with the pieces touching each other and the wheels meeting the wall through their tyres
    # (crash_beam on claude/k2-crash-contact), no piece of 3 % breaks off on demo v1, the scripted run 150416's
    # versions 1-5 or the real run 160725's version 7 at 40 km/h (tyre 60 N/mm; demo v1 150 N/mm), as the straight
    # column says (0). Without them (182f148f) the tower posts broke and 4-6 % came off; the hand calculation has no
    # bending of the rails by the towers and the arm brackets, so a design that splits stops the plan at the mismatch.
}

# ---------------------------------------------------------------- N1 modal

# The hand calculation gives the bounce of the whole vehicle on its springs;
# the lowest mode of the frame model may be pitch or roll, which sit near the
# bounce when the springs are at the axles: 15 %.
N1_TOL = 0.15
# The frame's lowest mode is kept this far above the wheels' turning rate at top speed: HIRAKU's assumption
# for the video, not checked as a general rule. The criterion is on the frequency, not on the gap: the gap's
# relative error is the frequency's times f / (f - f_wheel), about 2.3 on the demo vehicle.
N1_MARGIN = 0.20


def n1_modal(spec: Record, req: Record, design: Record) -> Record:
    import math as _m

    load, keys = loading(req, design)
    v = number(req, "Q50", "km/h")
    r = float(n1_calc(design, {})["driven_wheel_radius"]["value"])
    f_wheel = (v / 3.6) / (2.0 * _m.pi * r / 1000.0)
    return {
        "load_case_id": f"N1-{v:g}kmh",
        "test_id": "N1",
        "domain": spec["domain"],
        "title": f"固有振動数と、{v:g} km/h での車輪とモーターの回る速さ ({LOADING_JA[load]})",
        "params": {"speed": q(v, "km/h"), "loading": load},
        "requirement_keys": ["Q50", *keys],
        "criterion": {"quantity": "first_frame_mode", "op": ">=", "limit": q((1.0 + N1_MARGIN) * f_wheel, "Hz"),
                      "basis": (f"フレームのいちばん低い固有振動数を、Q50 {v:g} km/h での車輪の回転 ({f_wheel:.2f} Hz) より "
                                f"{N1_MARGIN:.0%} 上に離す (幅は HIRAKU の仮定で、一般の目安としては確かめていない。"
                                f"路面の凹凸の加振は入れていない)"),
                      "requirement_keys": ["Q50"]},
        "tolerance_rel": tolerance(spec, N1_TOL),
    }


def n1_calc(design: Record, load_case: Record) -> Record:
    sup = design["supports"]
    driven = [s for s in sup if s["actuation"]["drive"]] or sup
    return {"spring_rate_total": q(sum(float(s["suspension"].get("stiffness_N_per_mm") or 0.0) for s in sup), "N/mm"),
            "driven_wheel_radius": q(sum(s["radius_mm"] for s in driven) / len(driven), "mm")}


def n1_twist(design: Record, load_case: Record) -> Record:
    from hiraku.contract.loading import loading_of
    from hiraku.solvers.k0_handcalc.frame_inputs import ladder_twist_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError

    try:
        return ladder_twist_inputs(design, loading=loading_of(load_case))["inputs"]
    except (DesignError, KeyError, ValueError) as e:
        raise BuildError(f"no ladder frame for the twist hand calculation: {e}") from None


def joint_wall_calc(design: Record, load_case: Record) -> Record:
    """The inputs of frame_static.joint_wall_bending (frame_inputs.joint_wall_inputs) for a step load case (R1s):
    the joint off the wheel mounts where the side wall's plate bending is largest, at the load case's loading,
    step_height and step_supports (default the front-most supports). Quantities named as the formula's inputs."""
    from hiraku.contract.loading import loading_of
    from hiraku.solvers.k0_handcalc import units
    from hiraku.solvers.k0_handcalc.frame_inputs import joint_wall_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError
    from hiraku.solvers.shelf.frame_static import front_supports

    params = load_case.get("params") or {}
    step = units.to_unit(params["step_height"], "mm", "step_height") if "step_height" in params else None
    sup = [s.strip() for s in params["step_supports"].split(",")] if params.get("step_supports") else None
    if step is not None and sup is None:
        sup = front_supports(design)
    try:
        return joint_wall_inputs(design, loading=loading_of(load_case), step_mm=step, step_supports=sup)["inputs"]
    except (DesignError, KeyError, ValueError) as e:
        raise BuildError(f"no ladder joint for the joint wall hand calculation: {e}") from None


_TWIST = ("E", "nu", "J_r", "J_c", "n_c", "w", "L", "I0", "r_x", "k_f", "k_r", "t_f", "t_r", "d_f", "d_r")
N1_PLAN = {
    "tool": "modal",
    "say": ("車全体が 4 本のばねの上で上下に揺れる振動数と、はしご形のフレームが左右のレールを逆に動かしてねじれる "
            "振動数を、車輪とモーターの回る速さとともに先に出します"),
    "predictions": [
        {"quantity": "first_frame_mode", "formula_id": "modal.ladder_twist_mode", "output": "f",
         "assumptions": ["フレームのいちばん低い揺れは、はしご形のねじれ (左右のレールが逆に動く)",
                         "ねじれの角は前後に直線とみる (レイリー法。高めに出る)", "部品の重さは載せている節に等分"],
         "inputs": {k: f"from.{k}" for k in _TWIST}, "inputs_from": n1_twist},
        {"quantity": "first_body_mode", "formula_id": "modal.spring_mass", "output": "f",
         "assumptions": ["車全体が 4 本のばねの上で上下に揺れるとみる", "タイヤは硬いとみる (フレームのモデルと同じ)"],
         "inputs": {"k": "from.spring_rate_total", "m": "load.mass"}, "inputs_from": n1_calc},
        {"quantity": "wheel_rotation_frequency", "formula_id": "modal.rotation_excitation", "output": "f_wheel",
         "assumptions": ["車輪は滑らずに回る"],
         "inputs": {"v": "lc.speed", "r": "from.driven_wheel_radius", "i": "design.var.gear_ratio"}, "inputs_from": n1_calc},
        {"quantity": "motor_rotation_frequency", "formula_id": "modal.rotation_excitation", "output": "f_motor",
         "assumptions": ["車輪は滑らずに回る"],
         "inputs": {"v": "lc.speed", "r": "from.driven_wheel_radius", "i": "design.var.gear_ratio"}, "inputs_from": n1_calc},
    ],
}

# ---------------------------------------------------------------- R3 fatigue

# Miner damage goes with the cube of the stress range (S-N slope 3), so 15 %
# on the stress is about 50 % on the damage.
R3_TOL = 0.5


def r3_fatigue(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    d = number(req, "Q90", "km")
    cls = text(req, "Q12", tuple("ABCDEFGH"))
    v = number(req, "Q50", "km/h")  # the top speed: rougher input per metre, the safe side
    return {
        "load_case_id": f"R3-{d:g}km-{cls}",
        "test_id": "R3",
        "domain": spec["domain"],
        "title": f"ISO 8608 の {cls} 級の悪路を {v:g} km/h で {d:g} km 走ったときの溶接の疲労 ({LOADING_JA[load]})",
        "params": {"distance": q(d, "km"), "road_class": cls, "speed": q(v, "km/h"), "loading": load},
        "requirement_keys": ["Q90", "Q12", "Q50", *keys],
        "criterion": {"quantity": "fatigue_damage", "op": "<", "limit": q(1.0, "1"),
                      "basis": f"Miner's rule: the welds last Q90 {d:g} km while the damage stays below 1",
                      "requirement_keys": ["Q90"]},
        "tolerance_rel": tolerance(spec, R3_TOL),
    }


def _quarter_car(design: Record, summ: Record, loads: Record, sid: str) -> tuple[float, float, float, float]:
    """(static load N, unsprung mass kg, wheel rate N/mm, damping ratio) of the quarter car at a support."""
    import math as _m

    from hiraku.solvers.k0_handcalc.units import STANDARD_GRAVITY

    W = float(loads[sid]["force_N"][2])
    m_u = sum(it.mass_kg for it in summ["items"] if it.carrier == "support" and it.carried_by[0] == sid)
    s = next(x for x in design["supports"] if x["id"] == sid)
    k = float(s["suspension"].get("stiffness_N_per_mm") or 0.0)
    c = float(s["suspension"].get("damping_Ns_per_mm") or 0.0)
    m_s = W / STANDARD_GRAVITY - m_u
    if k <= 0 or m_s <= 0:
        raise BuildError(f"support {sid} has no spring or carries no sprung mass")
    return W, m_u, k, c * 1e3 / (2.0 * _m.sqrt(k * 1e3 * m_s))


_LEG = ("dx", "dy", "dz", "y_p", "b_l", "t_l", "b_x", "t_x", "E", "nu", "k_x", "k_y", "k_z", "theta_front", "theta_rear")
_QC = ("W_f", "m_uf", "k_sf", "zeta_f", "W_r", "m_ur", "k_sr", "zeta_r", "L", "G_d")
_DAMAGE_VARS = {"k_t": ("tyre_stiffness", "N/mm"), "FAT": ("fatigue_fat_class", "MPa"), "m": ("fatigue_slope", "1"),
                "N_knee": ("fatigue_knee_cycles", "1")}


def _r3_common(design: Record, load_case: Record) -> Record:
    """The quarter cars of the left wheels (static load, unsprung mass, spring, damping ratio), the wheelbase and
    the road class's roughness."""
    from hiraku.contract.loading import loading_of, on_board
    from hiraku.solvers.k0_handcalc.vehicle import mass_summary, support_loads
    from hiraku.solvers.shelf.fatigue import ROAD_CLASSES

    on = on_board(design, loading_of(load_case))
    summ = mass_summary(on)
    loads = support_loads(on, summary=summ)["supports"]
    sup = design["supports"]
    x_rear = min(s["position_mm"][0] for s in sup)
    rear_a = max((s for s in sup if abs(s["position_mm"][0] - x_rear) < 1e-6), key=lambda s: s["position_mm"][1])["id"]
    front_a = max((s for s in sup if abs(s["position_mm"][0] - x_rear) > 1e-6), key=lambda s: s["position_mm"][1])["id"]
    qc = {}
    for tag, sid in (("f", front_a), ("r", rear_a)):
        W, m_u, k, zeta = _quarter_car(design, summ, loads, sid)
        qc.update({f"W_{tag}": q(W, "N"), f"m_u{tag}": q(m_u, "kg"), f"k_s{tag}": q(k, "N/mm"), f"zeta_{tag}": q(zeta, "1")})
    x = {s["id"]: float(s["position_mm"][0]) for s in sup}
    qc["L"] = q(abs(x[front_a] - x[rear_a]), "mm")
    qc["G_d"] = q(ROAD_CLASSES[load_case["params"]["road_class"]] * 1e9, "mm^3")
    return qc


def _hand_damage(design: Record, load_case: Record, formula_id: str, inputs: Record, qc: Record) -> float:
    from hiraku.solvers import formula_registry

    reg = formula_registry()
    s = reg.evaluate(formula_id, inputs)["outputs"]
    p = load_case["params"]
    return reg.evaluate("fatigue.road_damage_four_wheels", {
        **{k: s[k] for k in ("sigma_ra", "sigma_rb", "sigma_fa", "sigma_fb")}, **qc,
        **{k: design["variables"][name] for k, (name, _) in _DAMAGE_VARS.items()},
        "v": p["speed"], "d": p["distance"]})["outputs"]["D"]["value"]


def r3_legs(design: Record, load_case: Record) -> list[tuple[float, Record]]:
    """Each U-frame's leg under the rails (frame_inputs.mount_leg_inputs, its left leg) with its hand damage:
    [(damage, the leg's record)], the most damaged first."""
    from hiraku.contract.loading import loading_of
    from hiraku.solvers.k0_handcalc.frame_inputs import mount_leg_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError

    try:
        legs = mount_leg_inputs(design, loading=loading_of(load_case))
    except (DesignError, KeyError, ValueError) as e:
        raise BuildError(f"no lower arm legs for the hand calculation: {e}") from None
    if not legs:
        raise BuildError("the design has no U-frame of legs and a lower cross member under the rails (lower_cross 0?), "
                         "so the leg hand calculation has nothing to look at")
    qc = _r3_common(design, load_case)
    out = [(_hand_damage(design, load_case, "frame_static.mount_leg_stress", leg["inputs"], qc), leg) for leg in legs]
    return sorted(out, key=lambda r: -r[0])


def r3_places(design: Record, load_case: Record) -> list[tuple[float, str]]:
    """The welds the plan compares, with their hand damage: the most damaged leg and the end cross member."""
    from hiraku.contract.loading import loading_of
    from hiraku.solvers.k0_handcalc.frame_inputs import end_cross_member_inputs

    D_leg, leg = r3_legs(design, load_case)[0]
    ec = end_cross_member_inputs(design, loading=loading_of(load_case))
    D_end = _hand_damage(design, load_case, "frame_static.end_cross_member_stress", ec["inputs"],
                         _r3_common(design, load_case))
    return [(D_leg, leg["leg"]), (D_end, ec["end_cross_member"])]


def r3_calc(design: Record, load_case: Record) -> Record:
    """The plan's from-values: the rear overhang and its end cross member (frame_inputs.end_cross_member_inputs),
    the leg whose hand damage is the largest (r3_legs; its inputs as leg_<name>), the quarter cars, the wheelbase
    and the road's roughness."""
    from hiraku.contract.loading import loading_of
    from hiraku.solvers.k0_handcalc.frame_inputs import end_cross_member_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError

    try:
        ec = end_cross_member_inputs(design, loading=loading_of(load_case))
    except (DesignError, KeyError, ValueError) as e:
        raise BuildError(f"no rear overhang with an end cross member for the hand calculation: {e}") from None
    leg = r3_legs(design, load_case)[0][1]
    return {**ec["inputs"], **{f"leg_{k}": v for k, v in leg["inputs"].items()}, **_r3_common(design, load_case)}


_END = {k: f"from.{k}" for k in ("e", "a", "w", "L1", "E", "nu", "b", "h", "t", "b_c", "h_c", "t_c", "b_j", "h_j", "t_j",
                                 "misfit_rear", "misfit_front", "give_per_kNm")}
_END_ASSUME = ["輪はレールより外にあり、輪の力はレールをそのずれの分ねじる",
               "左右そろった分は、後ろの継ぎ手で 1 回だけモーメントを分ける",
               "左右逆向きの分は、はしごの左右逆向きの半分を梁の鎖 (レールのねじりと逆向きの曲げ、横の部材の S 字の曲げとねじり。"
               "回す力は質量の慣性が受ける) で解く。端の横の部材が曲がらないとしたときのレールの端のずれを、その部材とフレームの"
               "残りが直列のばねのように分けて受ける",
               "端の横の部材は上下の曲げだけを数え、横の曲げ (慣性の横向きの力による) は入れない",
               "レールに無い節 (ロールバー) にかかる重さと慣性は、部材でつながるレールの節へ、ずれのモーメントごと剛に移す"]
_WHEELS = (("rear_a", "sigma_ra", "左の後輪"), ("rear_b", "sigma_rb", "右の後輪"), ("front_a", "sigma_fa", "左の前輪"),
           ("front_b", "sigma_fb", "右の前輪"))
# Why the end cross member (the analysis department's check of the demo vehicle, 2026-11-03): the fatigue tool's
# worst weld is at cross_E, and its stress per kN at each wheel (K1 with inertia relief) splits into the wheel
# patterns as warp 7.7, roll 3.7, heave 2.4 and pitch 2.1 MPa of spread (9.1 in all, class C at 40 km/h): left
# and right differ, not the body's heave and pitch. The wheels stand 200 mm outboard of the rails, so their
# forces twist the rails, and cross_E holds the overhang's rails by bending. The earlier plan (one quarter car
# at the rail joint) gave 0.0003 against 0.080. With the ladder's twist from a warping-torsion beam the plan
# gave 0.057 against 0.080 (version 1) and 0.081 against 0.134 (track 1220 mm with 4.5 mm rails): cross_E's
# antisymmetric moment came out low and fell as the rails stiffened, while K1's stayed. In K1 that moment is
# the redundant of the misfit at the rail ends against cross_E's double-curvature give in series with the
# rest of the frame's give, which is as large as the overhang's own (the joint's cross member bending, the
# ladder ahead warping), and where a moment enters matters: as a turn of the rails' own axes (the wheels'
# offset, the masses' inertia at the nodes) or as a chord couple. A beam chain of the ladder's antisymmetric
# half carries both, and gives 0.084 against 0.080 (version 1), 0.096 against 0.094 (4.5 mm rails), 0.133
# against 0.117 (track 1220 mm) and 0.140 against 0.134 (both). Per kN on one wheel at the weld corner the
# symmetric part is within 1 % (10 % low with 4.5 mm rails), the antisymmetric part 5-7 % low at the rear and
# 16-19 % high at the front: the gap is cross_E's sideways bending from the masses' sideways inertia (0.8-1.0
# MPa, the same at the rear and the front), which the hand calculation leaves out.
# The values above were summed with slope 22 below the S-N knee. Both sides now use 2 m - 1 = 5 there (IIW-1823-07
# (2008) sec. 4.3.1, p.110, for a spectrum): hand 0.151 against tool 0.126 (version 1) and 0.215 against 0.193
# (track 1220 mm, steering_x -200 mm, rail_t 4.5 mm, gusset_leg 40 mm, motor_peak_power 12 kW).
# The slope 2 m - 1 below the knee: Hobbacher, IIW document IIW-1823-07 (2008), sec. 4.3.1, as cited; not checked
# against that text in this repository (README, the table of values not checked, the fatigue row).


_LEG_ASSUME = ["はしごがねじれると左右のレールが上下逆に曲がり、脚の付く所で左右逆に前後へ傾く (反りのあるねじり、U 字が無いとき)",
               "脚と下の横の部材の U 字がその傾きを止め、横の部材はねじられ脚は曲げられる。U 字は中で左右逆の形 (半分を解く)",
               "レールは脚の下で 3 つの回るばね (ねじれ・前後の傾き・横の振れ)。U 字はレールを押し返して傾きを減らす",
               "左右そろった分 (左右のレールが鏡に回る) は入れない"]
_DAMAGE_ASSUME = ["4 輪それぞれ 1/4 車。溶接の応力は 4 輪の力のゆれに比例する", "左右の道は別、後輪は前輪と同じ道を後から通る",
                  "繰り返しはレイリー分布 (狭い帯域。広い帯域の実際より損傷を大きめに出す)",
                  "S-N の曲がり点より下は傾き 2m − 1 (IIW-1823-07 (2008) 4.3.1 節 p.110、振幅の変わる繰り返しの Miner の和)。"
                  "設計の変数 fatigue_slope_after_knee_constant_amplitude は一定の振幅のときの傾きなので使わない"]


# A known bias, not worked on (the analysis department's comparisons, 2026-11-03): on the revised version and
# the version with rail_t 4.5 mm the hand calculation of the end cross member comes out 26-31 % below the tool.
_END_BIAS = ("偏り (直していない): 直しの版と rail_t 4.5 mm の版では、端の横の部材の手計算がツールより 26〜31 % 小さく出る "
             "(解析部が比べた結果、2026-11-03)")


def _damage_from(prefix: str, place: str, extra: tuple[str, ...] = ()) -> Record:
    return {"quantity": f"fatigue_damage_{place}", "formula_id": "fatigue.road_damage_four_wheels", "output": "D",
            "assumptions": [*_DAMAGE_ASSUME, *extra],
            "inputs": {**{out: f"pred.{prefix}_per_kN_{tag}" for tag, out, _ in _WHEELS},
                       **{k: f"from.{k}" for k in _QC},
                       **{k: f"design.var.{name}" for k, (name, _) in _DAMAGE_VARS.items()},
                       "v": "lc.speed", "d": "lc.distance"},
            "inputs_from": r3_calc}


# Where the welds are worst (the analysis department's runs of the fatigue tool, 2026-11-03): with the suspension's
# mounts on the frame, the root of the front lower arm's rear leg (lower_leg_LA2) on v1; with rail_t 4.5 mm the rear
# overhang's end cross member (cross_E) again. The leg's stress per kN at each wheel (K1 with inertia relief) is mostly
# the ladder's twist (v1: front -20.7 / +17.0, rear +10.0 / -10.2 MPa for the left / right wheel): the rails pitch
# opposite ways at the leg's node, the lower cross member is twisted and the legs bend. The hand side here: front
# +-20.7, rear +-8.1. So the plan works out both welds and takes the larger damage, as the tool takes its worst point.
R3_PLAN = {
    "tool": "fatigue",
    "say": ("はしごがねじれると、左右のレールが脚の付く所で逆に傾き、脚と下の横の部材の U 字がそれを止めて脚の根元が曲げられます。"
            "輪はレールより外にあるので、輪の力は後ろの張り出しの端の横の部材も曲げます。4 輪それぞれ 1 kN あたりのその 2 か所の応力を出し、"
            "4 輪の 1/4 車が悪路で受ける力のゆれから、傷みの大きいほうの疲労の損傷を先に見積もります"),
    "predictions": [
        *({"quantity": f"mount_leg_stress_per_kN_{tag}", "formula_id": "frame_static.mount_leg_stress",
           "output": out, "assumptions": [f"{name}に 1 kN", *_LEG_ASSUME],
           "inputs": {k: f"from.leg_{k}" for k in _LEG}, "inputs_from": r3_calc}
          for tag, out, name in _WHEELS),
        _damage_from("mount_leg_stress", "mount_leg"),
        *({"quantity": f"end_cross_member_stress_per_kN_{tag}", "formula_id": "frame_static.end_cross_member_stress",
           "output": out, "assumptions": [f"{name}に 1 kN", *_END_ASSUME], "inputs": _END, "inputs_from": r3_calc}
          for tag, out, name in _WHEELS),
        _damage_from("end_cross_member_stress", "end_cross_member", (_END_BIAS,)),
        {"quantity": "fatigue_damage", "formula_id": "fatigue.worst_of_two_welds", "output": "D",
         "assumptions": ["フレームの寿命はいちばん傷む溶接で決まる (脚の根元か、後ろの張り出しの端の横の部材)",
                         "脚は、U 字の脚のうち手計算の損傷がいちばん大きいもの"],
         "inputs": {"D_a": "pred.fatigue_damage_mount_leg", "D_b": "pred.fatigue_damage_end_cross_member"}},
    ],
}

# ---------------------------------------------------------------- P4, T1, T2 on the forest loop

# The hand calculation leaves out the accelerations and braking, the
# auxiliary load and the battery's own resistance, which the K3 circuit has:
# 10 %.
P4_TOL = 0.10
# Thermal: one lumped temperature per part on both sides; the hand side takes
# a mean loss where the circuit follows the loss along the loop: 15 %.
T_TOL = 0.15


def _loop(spec: Record, req: Record, distance_key: str) -> tuple[Record, list[str]]:
    """The forest loop: the flat parts are driven at Q50. The steep grades are the test's
    param grade_high in the catalog (the grade driven up for a long way), else Q13; the gentle
    grades and the speed on the steep ones are the params grade_low and speed_low, else half
    of the steep grade and of Q50 (an assumption of this load case)."""
    from hiraku.solvers.k0_handcalc import units

    d = number(req, distance_key, "km")
    v = number(req, "Q50", "km/h")
    given = spec.get("params", {})

    def from_spec(name: str, unit: str) -> float | None:
        if name not in given:
            return None
        try:
            return units.to_unit(given[name], unit, name)
        except ValueError as e:
            raise BuildError(f"catalog params of {spec['id']}: {e}") from None

    keys = [distance_key, "Q50"]
    grade = from_spec("grade_high", "%")
    if grade is None:
        grade = number(req, "Q13", "%")
        keys.insert(1, "Q13")
    low = from_spec("grade_low", "%")
    slow = from_spec("speed_low", "km/h")
    params = {"distance": q(d, "km"), "grade_low": q(grade / 2.0 if low is None else low, "%"), "grade_high": q(grade, "%"),
              "speed_low": q(v / 2.0 if slow is None else slow, "km/h"), "speed_high": q(v, "km/h")}
    if not (0 <= params["grade_low"]["value"] <= grade and 0 < params["speed_low"]["value"] <= v):
        raise BuildError(f"{spec['id']}: grade_low must lie within 0..grade_high and speed_low within 0..Q50")
    return params, keys


def p4_drive_energy(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    params, loop_keys = _loop(spec, req, "Q54")
    d = params["distance"]["value"]
    return {
        "load_case_id": f"P4-{d:g}km",
        "test_id": "P4",
        "domain": spec["domain"],
        "title": f"林道の周回を {d:g} km 走って電池が残るか ({LOADING_JA[load]})",
        "params": {**params, "soc_start": q(100.0, "%"), "loading": load},  # a full battery: an assumption
        "requirement_keys": [*loop_keys, *keys],
        "criterion": {"quantity": "soc_end", "op": ">", "limit": q(0.0, "%"),
                      "basis": f"P4: charge is left at the end of Q54 {d:g} km", "requirement_keys": ["Q54"]},
        "tolerance_rel": tolerance(spec, P4_TOL),
    }


def p4_calc(design: Record, load_case: Record) -> Record:
    from hiraku.solvers.k3_power_thermal import forest_loop, repeat
    from hiraku.solvers.k0_handcalc import units

    p = load_case["params"]

    def val(name, unit):
        return units.to_unit(p[name], unit, name)

    segs = repeat(forest_loop(val("grade_low", "1"), val("grade_high", "1"), val("speed_low", "m/s"), val("speed_high", "m/s")),
                  val("distance", "m"))
    length = sum(s.length for s in segs)
    climb = sum(s.length * math.sin(math.atan(s.grade)) for s in segs if s.grade > 0)
    rms = math.sqrt(sum(s.length * s.speed**2 for s in segs) / length)
    eta = 1.0
    for name in ("drivetrain_efficiency", "motor_efficiency", "controller_efficiency"):
        eta *= design_var(design, name, "1")
    return {"loop_climb": q(climb, "m"), "loop_rms_speed": q(rms * 3.6, "km/h"), "drive_efficiency": q(eta, "1"),
            "battery_energy": q(design_var(design, "battery_voltage", "V") * design_var(design, "battery_capacity", "Ah") / 1000.0,
                                "kWh")}


P4_PLAN = {
    "tool": "drive_energy",
    "say": "転がり・空気・登りの仕事の和を効率で割って、周回で使う電力量を先に見積もります",
    "predictions": [
        {"quantity": quantity, "formula_id": "drive_energy.loop_energy", "output": output,
         "assumptions": ["一定の速さで走るとみる (加速・減速は入れない)", "補機の電力は入れない",
                         "空気抵抗は周回の速さの二乗平均で出す", "下りの位置エネルギーは regen_efficiency の分だけ戻る"],
         "inputs": {"m": "load.mass", "d": "lc.distance", "v": "from.loop_rms_speed", "C_rr": "design.var.rolling_resistance",
                    "C_d": "design.var.drag_coefficient", "A": "design.var.frontal_area", "H": "from.loop_climb",
                    "eta": "from.drive_efficiency", "eta_regen": "design.var.regen_efficiency", "E_batt": "from.battery_energy",
                    "rho": "design.var.air_density"},
         "inputs_from": p4_calc}
        for quantity, output in (("energy_used", "E"), ("soc_used", "soc_used"))
    ] + [
        {"quantity": "soc_end", "formula_id": "drive_energy.soc_end", "output": "soc_end",
         "assumptions": ["使える電力量は電圧 x 容量"],
         "inputs": {"E": "pred.energy_used", "E_batt": "from.battery_energy", "soc_start": "lc.soc_start"},
         "inputs_from": p4_calc},
    ],
}


# the case past the limit: an ambient where the hand-calculated part temperature is this far over its limit (HIRAKU's
# choice, no source). The parts' limits themselves and their sources are in hiraku/designgen (README, the table of
# values not checked, the parts' temperature row).
T_BEYOND_K = 5.0


def _hand_temperature(design: Record, load_case: Record, output: str) -> float:
    """thermal.loop_steady_temperature with the plan's inputs (_LOOP_INPUTS) for this load case."""
    from hiraku.solvers import formula_registry
    from hiraku.catalog.summary import design_summary

    vals = {}
    for name, ref in _LOOP_INPUTS.items():
        head, _, rest = ref.partition(".")
        if head == "load":
            vals[name] = design_summary(design)["loadings"][load_case["params"]["loading"]][rest]
        elif head == "lc":
            vals[name] = load_case["params"][rest]
        else:  # design.var.<name>
            vals[name] = design["variables"][rest.split(".", 1)[1]]
    return formula_registry().evaluate("thermal.loop_steady_temperature", vals)["outputs"][output]["value"]


def _thermal(test_id: str, what: str, quantity: str, limit_var: str, op: str, output: str):
    """T1 / T2: the loop at the hot end of Q19; the limit is the part's own (a design variable
    with its source in hiraku.designgen); without it, no criterion. Variant "beyond": the ambient
    where the hand calculation puts the part T_BEYOND_K over its limit (the loop's losses lift the
    part the same amount above any ambient)."""
    def build(spec: Record, req: Record, design: Record) -> Record:
        load, keys = loading(req, design)
        params, loop_keys = _loop(spec, req, "Q54")
        t_amb = number(req, "Q19", "degC")  # the hot end of the range
        d = params["distance"]["value"]
        out = {
            "load_case_id": f"{test_id}-{t_amb:g}C",
            "test_id": test_id,
            "domain": spec["domain"],
            "title": f"気温 {t_amb:g} ℃ で林道の周回を {d:g} km 走ったときの{what}の温度 ({LOADING_JA[load]})",
            "params": {**params, "ambient_temperature": q(t_amb, "degC"), "loading": load},
            "requirement_keys": ["Q19", *loop_keys, *keys],
            "tolerance_rel": tolerance(spec, T_TOL),
        }
        limit = design_var(design, limit_var, "degC")
        if limit is not None:
            out["criterion"] = {"quantity": quantity, "op": op, "limit": q(limit, "degC"),
                                "basis": f"{test_id}: the design variable {limit_var} (the part's limit; its source is "
                                         f"in the design generator), against the lumped temperature of the part"}
        if spec.get("variant") == "beyond":
            if limit is None:
                raise BuildError(f"{test_id} has no case past the limit without the design variable {limit_var}")
            rise = _hand_temperature(design, out, output) - t_amb
            hot = limit - rise + T_BEYOND_K
            out.update({
                "load_case_id": f"{test_id}-{hot:.0f}C-beyond",
                "title": (f"限界の外: 気温 {hot:.1f} ℃ (仮定。{what}の温度が手計算で限界 {limit:g} ℃ を {T_BEYOND_K:g} K 超える"
                          f"気温) で林道の周回を {d:g} km 走ったときの{what}の温度 ({LOADING_JA[load]})"),
                "params": {**out["params"], "ambient_temperature": q(hot, "degC")},
                "requirement_keys": [k for k in out["requirement_keys"] if k != "Q19"],
            })
        return out
    build.__name__ = f"{test_id.lower()}_thermal"
    return build


t1_thermal = _thermal("T1", "電池", "temp_battery_max", "battery_max_temperature", "<=", "T_battery")
# T2 is the motor and the controller: one load case per part, each judged on its own limit (a load
# case has one criterion), ids T2-<ambient>C-motor and T2-<ambient>C-controller.
T2_PARTS = (("motor", "モーター", "motor_max_temperature", "<", "T_motor"),
            ("controller", "制御器", "controller_max_temperature", "<", "T_controller"))


def t2_thermal(spec: Record, req: Record, design: Record) -> list[Record]:
    out = []
    for part, what, limit_var, op, output in T2_PARTS:
        lc = _thermal("T2", what, f"temp_{part}_max", limit_var, op, output)(spec, req, design)
        head, beyond, tail = lc["load_case_id"].partition("-beyond")
        lc["load_case_id"] = f"{head}-{part}{beyond}{tail}"
        out.append(lc)
    return out

_LOOP_INPUTS = {
    "m": "load.mass", "C_rr": "design.var.rolling_resistance", "C_d": "design.var.drag_coefficient",
    "A": "design.var.frontal_area", "rho": "design.var.air_density", "eta_d": "design.var.drivetrain_efficiency",
    "eta_m": "design.var.motor_efficiency", "eta_c": "design.var.controller_efficiency",
    "eta_regen": "design.var.regen_efficiency", "P_aux": "design.var.aux_power", "V": "design.var.battery_voltage",
    "R_b": "design.var.battery_resistance", "d": "lc.distance", "grade_low": "lc.grade_low", "grade_high": "lc.grade_high",
    "v_low": "lc.speed_low", "v_high": "lc.speed_high", "T_amb": "lc.ambient_temperature",
    **{f"{k}_{part}": f"design.var.{part}_{name}" for part in ("battery", "motor", "controller")
       for k, name in (("R", "thermal_resistance"), ("C", "heat_capacity"))},
}
_T_ASSUME = ["周回の区間ごとに目標の速さで一定に走るとみる (加速・減速は入れない)", "損失は走る時間の平均",
             "部品は温度 1 つの熱容量と、空気への熱抵抗"]


def _t_plan(what: str, parts: tuple[tuple[str, str], ...]) -> Record:
    """parts: (quantity, output) of each part temperature predicted, then drive_time."""
    return {
        "tool": "thermal",
        "say": f"周回を区間ごとの一定の速さで走るとみて損失を出し、{what}の温度の上がり方を先に見積もります",
        "predictions": [
            {"quantity": quantity, "formula_id": "thermal.loop_steady_temperature", "output": output,
             "assumptions": _T_ASSUME, "inputs": dict(_LOOP_INPUTS)}
            for quantity, output in (*parts, ("drive_time", "t"))
        ],
    }


T1_PLAN = _t_plan("電池", (("temp_battery_max", "T_battery"),))
# one plan for both T2 load cases (the plans are per test): it predicts both parts
T2_PLAN = _t_plan("モーターと制御器", tuple((f"temp_{part}_max", output) for part, _, _, _, output in T2_PARTS))

# ---------------------------------------------------------------- H1, H3, H5 assembly

# The hand calculation and the tool read the same boxes; the tool finds the
# steer angle by bisection over the travel: 5 %.
H_TOL = 0.05


# The joints' 2 mm gap: the program management's figure, HIRAKU's choice, no source.
H1_JOINTS_TITLE = "つながりが形の上で触れているか (つながる点 at_mm が両方の形の上、つながりの無い部品が無い)"
H1_JOINTS_CRITERION = {"quantity": "joint_gap_count", "op": "<=", "limit": {"value": 0.0, "unit": "1"},
                       "basis": "H1: every mechanical joint a part's connects_to names touches (gap at most 2 mm: the "
                                "program management's figure; wires and hoses not counted), says where (at_mm) on both "
                                "shapes within 2 mm, and no part is joined to nothing"}


def h1_interference(spec: Record, req: Record, design: Record) -> list[Record]:
    """Stage-0 cases, each judged on its own: at rest (the driver's seat also at both ends of its slide), in motion
    (the wheels' travel and the steering's range), the joints touching, the modules' spaces (a design split into
    modules) and the steering's free angle (a design with max_steer_angle)."""
    load, keys = loading(req, design)
    base = {"test_id": "H1", "domain": spec["domain"], "params": {"loading": load}, "requirement_keys": keys,
            "tolerance_rel": tolerance(spec, H_TOL)}
    out = [
        {**base, "load_case_id": "H1-interference",
         "title": "止まっているときの干渉 (座席を滑りの両端に動かしたときも)",
         "criterion": {"quantity": "interference_count", "op": "<=", "limit": q(0.0, "1"),
                       "basis": "H1: no two bodies overlap at rest, the driver's seat at either end of its slide too, "
                                "beyond what the rule allows (welded joints, bolted mounts up to MOUNT_MM, the rest "
                                "TOUCH_MM: k4_geometry.model.allowance)"}},
        {**base, "load_case_id": "H1-motion",
         "title": "車輪が上下しハンドルを切ったときの干渉",
         "criterion": {"quantity": "motion_interference_count", "op": "<=", "limit": q(0.0, "1"),
                       "basis": "H1: through the wheels' travel and the steering's range nothing of a corner meets "
                                "what it is not mounted to"}},
    ]
    # judged again since the generator joins every part in shape (designgen kit/joints.py, kit/arms.py)
    out.append({**base, "load_case_id": "H1-joints", "title": H1_JOINTS_TITLE,
                "criterion": copy.deepcopy(H1_JOINTS_CRITERION)})
    if design.get("modules"):
        out.append({**base, "load_case_id": "H1-envelope",
                    "title": "部品と部材がモジュールの空間に収まっているか",
                    "criterion": {"quantity": "envelope_outside_count", "op": "<=", "limit": q(0.0, "1"),
                                  "basis": "H1: every part and member inside its module's space (design.modules "
                                           "envelope, given by the packager)"}})
    steer = design_var(design, "max_steer_angle", "deg")
    if steer:
        out.append({**base, "load_case_id": "H1-steer",
                    "title": "ハンドルを止めまで切れるか (前輪がフレームに当たらない)",
                    "criterion": {"quantity": "free_steer_angle", "op": ">=", "limit": q(steer, "deg"),
                                  "basis": f"H1: the front wheels turn to the design's max_steer_angle ({steer:g} deg) "
                                           "without touching the frame"}})
    return out


def h1_overlap_calc(design: Record, load_case: Record) -> Record:
    """assembly.overlap_count's inputs: hiraku.solvers.k0_handcalc.assembly_inputs.overlap_inputs."""
    from hiraku.solvers.k0_handcalc.assembly_inputs import overlap_inputs
    from hiraku.solvers.shelf.base import on_board

    got = overlap_inputs(on_board(design, load_case))
    return {"n_rest": q(got["n_rest"], "1"), "n_slide": q(got["n_slide"], "1")}


def h1_joint_calc(design: Record, load_case: Record) -> Record:
    """assembly.joint_count's inputs: hiraku.solvers.k0_handcalc.assembly_inputs.joint_inputs."""
    from hiraku.solvers.k0_handcalc.assembly_inputs import joint_inputs
    from hiraku.solvers.shelf.base import on_board

    got = joint_inputs(on_board(design, load_case))
    return {"n_gap": q(got["n_gap"], "1"), "n_none": q(got["n_none"], "1")}


def h1_envelope_calc(design: Record, load_case: Record) -> Record:
    """assembly.envelope_count's input: hiraku.solvers.k0_handcalc.assembly_inputs.envelope_inputs."""
    from hiraku.solvers.k0_handcalc.assembly_inputs import envelope_inputs
    from hiraku.solvers.shelf.base import on_board

    return {"n_out": q(envelope_inputs(on_board(design, load_case))["n_out"], "1")}


def h1_motion_calc(design: Record, load_case: Record) -> Record:
    """assembly.motion_count's inputs: hiraku.solvers.k0_handcalc.assembly_inputs.motion_inputs."""
    from hiraku.solvers.k0_handcalc.assembly_inputs import motion_inputs
    from hiraku.solvers.shelf.base import on_board

    got = motion_inputs(on_board(design, load_case))
    return {"n_wheel": q(got["n_wheel"], "1"), "n_rod": q(got["n_rod"], "1")}


def h1_calc(design: Record, load_case: Record) -> Record:
    """geometry.free_steer_angle's inputs: hiraku.solvers.k0_handcalc.scene_inputs.steer_room_inputs."""
    from hiraku.solvers.k0_handcalc.scene_inputs import steer_room_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError

    try:
        got = steer_room_inputs(design)["inputs"]
    except DesignError as e:
        raise BuildError(str(e)) from None
    return {"steer_w": got["w"], "steer_l": got["l"], "steer_c": got["c"]}


H1_PLAN = {
    "tool": "assembly",
    "say": "前輪を上から見た長方形とみて、内側の角がレールに届くまでのハンドルの角度を先に出します",
    "predictions": [
        {"quantity": "free_steer_angle_geometric", "formula_id": "geometry.free_steer_angle", "output": "theta",
         "assumptions": ["車輪は上から見て直径 x 幅の長方形", "車輪の中心を通る縦の軸で回る", "レールの面は前後に真っすぐ"],
         "inputs": {"w": "from.steer_w", "l": "from.steer_l", "c": "from.steer_c"}, "inputs_from": h1_calc},
        {"quantity": "free_steer_angle", "formula_id": "geometry.free_steer_angle_limited", "output": "theta",
         "assumptions": ["ハンドルは max_steer_angle で止まる (組み立ての道具も、そこより先は回さない)"],
         "inputs": {"theta_free": "pred.free_steer_angle_geometric", "theta_stop": "design.var.max_steer_angle"}},
    ],
}
H1_OVERLAP_PLAN = {
    "tool": "assembly",
    "say": "部品と部材を軸に揃えた箱とみて、止まっているときに重なる組を先に数えます",
    "predictions": [
        {"quantity": "interference_count", "formula_id": "assembly.overlap_count", "output": "n",
         "assumptions": ["回してある部品と斜めの部材は数えない (軸に揃えた箱が実物よりずっと大きくなるため。見落とすことはあっても、余分には数えない)",
                         "重なってよい深さは、ツールと同じ決まり (溶接の継ぎ目、ボルトの取付は MOUNT_MM まで、ほかは TOUCH_MM)"],
         "inputs": {"n_rest": "from.n_rest", "n_slide": "from.n_slide"}, "inputs_from": h1_overlap_calc},
    ],
}
H1_JOINT_PLAN = {
    "tool": "assembly",
    "say": "部品と相手を軸に揃えた箱とみて、connects_to の相手と 2 mm より離れているつながりと、つながりの無い部品を先に数えます",
    "predictions": [
        {"quantity": "joint_gap_count", "formula_id": "assembly.joint_count", "output": "n",
         "assumptions": ["箱どうしの離れは形どうしの離れより大きくならない (見落とすことはあっても、余分には数えない)",
                         "電線とホースは間を通るので数えない"],
         "inputs": {"n_gap": "from.n_gap", "n_none": "from.n_none"}, "inputs_from": h1_joint_calc},
    ],
}
H1_ENVELOPE_PLAN = {
    "tool": "assembly",
    "say": "部品と部材を軸に揃えた箱とみて、自分のモジュールの空間の箱に収まらない物を先に数えます",
    "predictions": [
        {"quantity": "envelope_outside_count", "formula_id": "assembly.envelope_count", "output": "n",
         "assumptions": ["回してある部品は回した角を囲む箱、斜めの部材は両端を断面の大きい辺の半分だけ広げた箱"],
         "inputs": {"n_out": "from.n_out"}, "inputs_from": h1_envelope_calc},
    ],
}
H1_MOTION_PLAN = {
    "tool": "assembly",
    "say": "車輪の箱とタイロッドの棒を、ハンドルの範囲で 1 度ずつ回し上下に動かして、当たる組を先に数えます",
    "predictions": [
        {"quantity": "motion_interference_count", "formula_id": "assembly.motion_count", "output": "n",
         "assumptions": ["動くのは車輪とタイロッドだけ (ツールはナックルやブレーキも動かす)",
                         "車輪は支えの位置を通る縦の軸で回り、上下に行程の半分ずつ動く"],
         "inputs": {"n_wheel": "from.n_wheel", "n_rod": "from.n_rod"}, "inputs_from": h1_motion_calc},
    ],
}


def h3_people(spec: Record, req: Record, design: Record) -> list[Record]:
    """Two cases: the large person's knees clear the part ahead (knee_room), and the driver's pedal knee in the
    comfortable range for both body sizes (knee_angle_room), each seated at their best place in the slide."""
    # Outside values in the bases (the numbers are k4_geometry.people's): the AIST body sizes (J5, the 5th / 95th
    # percentiles), AIST human body dimensions database 1991-92, not checked (README, the table of values not
    # checked); the knee range 99-138 deg, Porter and Gyi 1998 as Schmidt et al. 2014 (Applied Ergonomics 45,
    # Table 1, p. 251) reproduce it: the review read there, the original not checked (people.KNEE_COMFORT_SOURCE).
    load, keys = loading(req, design)
    body = {
        "load_case_id": "H3-body-size",
        "test_id": "H3",
        "domain": spec["domain"],
        "title": f"体格の範囲の両端で座れるか・膝と頭が当たらないか ({LOADING_JA[load]})",
        "params": {"loading": load},
        "requirement_keys": (["Q31"] if entry(req, "Q31") else []) + keys,
        "criterion": {"quantity": "knee_room", "op": ">=", "limit": q(0.0, "mm"),
                      "basis": "H3: the knees of a young male 95th percentile (AIST J5 buttock-knee length) "
                               "clear the part ahead"},
        "tolerance_rel": tolerance(spec, H_TOL),
    }
    knee = dict(body, load_case_id="H3-knee-angle",
                title=f"運転者のペダルを踏む膝の角度が、体格の両端で楽な範囲にあるか ({LOADING_JA[load]})",
                criterion={"quantity": "knee_angle_room", "op": ">=", "limit": q(0.0, "mm"),
                           "basis": ("H3: the pedal knee of a female 5th and a male 95th percentile, each at their best "
                                     "place in the seat's slide, within 99-138 deg (Porter and Gyi 1998, read in "
                                     "Schmidt et al. 2014; the original not checked)")})
    return [body, knee]


def h3_calc(design: Record, load_case: Record) -> Record:
    """occupant.knee_room's inputs: hiraku.solvers.k0_handcalc.scene_inputs.knee_space_inputs."""
    from hiraku.contract.loading import loading_of
    from hiraku.solvers.k0_handcalc.scene_inputs import knee_space_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError

    try:
        got = knee_space_inputs(design, loading_of(load_case))["inputs"]
    except DesignError as e:
        raise BuildError(str(e)) from None
    return {"knee_length": got["L"], "buttock_knee": got["L_bk"]}


def h3_knee_calc(design: Record, load_case: Record) -> Record:
    """occupant.knee_angle_room's inputs: hiraku.solvers.k0_handcalc.scene_inputs.knee_angle_inputs."""
    from hiraku.contract.loading import loading_of
    from hiraku.solvers.k0_handcalc.scene_inputs import knee_angle_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError

    try:
        return knee_angle_inputs(design, loading=loading_of(load_case))["inputs"]
    except DesignError as e:
        raise BuildError(str(e)) from None


H3_PLAN = {
    "tool": "assembly",
    "say": "座席の背から前の部品までの長さから、膝の長さを引いて、膝の前の余裕を先に出します",
    "predictions": [
        {"quantity": "knee_angle_room", "formula_id": "occupant.knee_angle_room", "output": "room",
         "assumptions": ["脚は膝で曲がる 2 本の棒 (横から見て)", "膝の角度は K4 の座らせ方 (スライドの中のいちばん良い位置) から"],
         "inputs": {k: f"from.{k}" for k in ("l1_big", "l2_big", "theta_big", "l1_small", "l2_small", "theta_small",
                                              "theta_lo", "theta_hi")},
         "inputs_from": h3_knee_calc},
        {"quantity": "knee_room", "formula_id": "occupant.knee_room", "output": "room",
         "assumptions": ["背もたれに付けて座る", "膝の長さは AIST の若年男性 95 パーセンタイル (J5)"],
         "inputs": {"L": "from.knee_length", "L_bk": "from.buttock_knee"}, "inputs_from": h3_calc},
    ],
}


NOT_ASSEMBLED = ("occupant", "cargo")  # people and load ride in, they are not assembled (as in k4_geometry)
# Parts welded into the frame (gussets, through pieces, the roll bar's part) are in place before
# assembly, as the frame is (k4_geometry.model.WELDED): not assembly steps either.
NOT_ASSEMBLED += ("frame", "gusset", "rollbar", "bracket")


def _assembled(design: Record) -> int:
    return sum(1 for p in design.get("parts", []) if p["kind"] not in NOT_ASSEMBLED)


# The assembly tool's hands' way (two hands and a short tool straight in from outside to the fastening, within
# REACH_MM) is its own rule with no source, so the steps it finds without that way are shown, not judged: every
# assembly analysis has steps_without_tool_clearance and steps_out_of_reach among its results, and no load case
# judges them until the rule is checked against a source (a load case would need a prediction for the tool to run,
# and there is no K0 formula for them).
H5_HANDS_NOTE = ("the steps without the hands' way and out of reach (steps_without_tool_clearance, steps_out_of_reach) "
                 "are shown in the results, not judged: the assembly tool's hands' way is its own rule with no source")


def h5_assembly(spec: Record, req: Record, design: Record) -> list[Record]:
    """H5-order: no part is shut in, judged on blocked_parts (a count of the version itself; a part count taken
    from the version the load cases were built on stayed at that version's number)."""
    load, keys = loading(req, design)
    return [{"load_case_id": "H5-order", "test_id": "H5", "domain": spec["domain"],
             "title": "どの部品も、どこかの向きから入れられるか", "params": {"loading": load},
             "requirement_keys": (["Q82"] if entry(req, "Q82") else []) + keys,
             "criterion": {"quantity": "blocked_parts", "op": "<=", "limit": q(0.0, "1"),
                           "basis": "H5: every part of the design goes in, along some direction in some order (none "
                                    "shut in by what is there before it); " + H5_HANDS_NOTE},
             "tolerance_rel": tolerance(spec, H_TOL)}]


def h5_calc(design: Record, load_case: Record) -> Record:
    """assembly.blocked_count's inputs: hiraku.solvers.k0_handcalc.assembly_inputs.blocked_inputs."""
    from hiraku.solvers.k0_handcalc.assembly_inputs import blocked_inputs
    from hiraku.solvers.shelf.base import on_board

    return {"n_blocked": q(blocked_inputs(on_board(design, load_case))["n_blocked"], "1")}


H5_PLAN = {
    "tool": "assembly",
    "say": "部品を箱とみて、枠と溶接の物だけを相手に、6 方向のどれからも入らない部品を先に数えます",
    "predictions": [
        {"quantity": "blocked_parts", "formula_id": "assembly.blocked_count", "output": "n",
         "assumptions": ["部品を止めるのは枠 (部材と溶接の物) だけ。先に入れた部品は数えない (ツールは順番も、先に滑らせることも試す)",
                         "部品は 1 つの軸に沿ってまっすぐ入る。止まっているときに既に重なっている物は H1-interference のもので、止めには数えない"],
         "inputs": {"n_blocked": "from.n_blocked"}, "inputs_from": h5_calc},
    ],
}


# ---------------------------------------------------------------- R4 roll-over protective structure (ROPS)

# The loads are those of the OECD static test of tractor roll-over protective structures, borrowed for this
# vehicle (the code is for tractors of at least 600 kg unballasted, section 2.1; this is not a certification):
# source: OECD Standard Code for the Official Testing of Protective Structures on Agricultural and Forestry
# Tractors (Static Test), Code 4, February 2026 edition, read from
# https://www.oecd.org/content/dam/oecd/en/topics/policy-sub-issues/tractors/04-Code4-EN.pdf on 2026-11-03:
# 3.5.2.1 longitudinal loading E_IL1 = 1.4 M, 3.5.3.1 side loading E_IS = 1.75 M, 3.6 crushing F = 20 M (M the
# reference mass, kg; E in J, F in N), 3.5.2 the longitudinal load on the uppermost transverse member at one
# sixth of the width of the top inwards from the outside corner, 3.5.3 the side load at the upper extremity,
# 3.6.1 the crushing force across the rear uppermost members with its resultant in the median plane, 3.5.2.2 and
# 3.5.3.2 the loading stopped when the structure infringes on the clearance zone. The loads (1.4 M, 1.75 M, 20 M)
# and the one sixth: checked against that text on 2026-11-03 (README, the table of values checked). The 600 kg of
# 2.1 and the clause 3.8 the criterion's basis names are not in that row.
R4_CODE4 = {"rear": 1.4, "side": 1.75, "crush": 20.0}  # J/kg, J/kg, N/kg
# Reference mass: 500 kg, the mass for which the catalog's values (side 875 J, rear 700 J, from above 10 kN) are
# the Code 4 formulas (875 / 1.75 = 700 / 1.4 = 10 000 / 20 = 500). Not the demo vehicle's mass; Code 4 sets M
# from the tractor (an assumption of this catalog, kept as given).
R4_REFERENCE_MASS_KG = 500.0
# Code 4 3.5.2: one sixth of the top's width in from the outside corner. Here the width is taken as the posts'
# centre-to-centre span (assumption: Code 4 measures it to the outside of the structure).
R4_REAR_FRACTION = 1.0 / 6.0
# A tool's crushed roll bar against the hand calculation's roll bar on its feet (portal_pushover): the deflection at
# the required energy is long on the plastic plateau and sensitive to the collapse load and to how the feet are
# held, and the clearance is a difference of that deflection and the gap: 25 % (assumption).
R4_TOL = 0.25

# R4 is judged by K2's rollover_beam (stage 3): the vehicle tipped over on the tilt table to the roll bar's first
# touch (the rigid body with its ground contact), then the frame as yielding beams landing on the ground in that state.
# The one load case is R4-rollover (R4_ROLLOVER_ID). The static push of Code 4 (the former R4-rops) is not built: no
# tool on the shelf loads the roll bar that way, and a tool for it made in a run (tests R4, giving
# rops_occupant_clearance at a lighter stage) would set R4's stage below rollover_beam's (shelf.load_case_stage takes the
# lowest stage of the tools covering the test family and the quantity). Its formulas stay as the hand calculation.
# The hand calculation is R4_PREDICTIONS (after the path check), inputs read off the design by r4_inputs
# (hiraku.solvers.k0_handcalc.rops_inputs.rops_inputs: frame_inputs.portal_frame_inputs's "side", "rear" and "top"
# dicts as from.side_<k>, from.rear_<k>, from.top_<k>, and the gaps g_side, g_rear, g_top), in this order:
#   frame_static.portal_frame_side  E_in = lc.side_energy           -> delta_E (sideways at the top)
#   frame_static.portal_frame_rear  E_in = lc.rear_energy, f = lc.rear_fraction -> delta_E (along the vehicle)
#   frame_static.portal_frame_top   F = lc.crush_force               -> delta (down at mid-span)
#   frame_static.rops_occupant_clearance  g_*, s_*, d0_* = from.*, d_* = pred.rops_*_deflection
#                                         -> rops_occupant_clearance = min(s_*)
# (s_*: the shortest distance between the roll bar's members and k4_geometry.occupant_spaces, as
# k4_geometry.occupant_clearance measures it, with the top corners moved by each loading's motion at the deflection
# d0_* the portal formulas give, which must equal d_*; g_*: the travel to contact along the motion; rops_inputs).
R4_DEFLECTIONS = (("side", "frame_static.portal_frame_side", "delta_E", {"E_in": "lc.side_energy"}),
                  ("rear", "frame_static.portal_frame_rear", "delta_E", {"E_in": "lc.rear_energy", "f": "lc.rear_fraction"}),
                  ("top", "frame_static.portal_frame_top", "delta", {"F": "lc.crush_force"}))


def _r4_portal_inputs() -> dict:
    from hiraku.solvers.k0_handcalc.library_portal import COMMON
    return {"side": COMMON, "rear": COMMON, "top": COMMON + ("f_y",)}


# The portal formulas' inputs (library_portal.COMMON_INPUTS: the roll bar, its outriggers and the rail pieces to
# where the rail is held, as frame_inputs.portal_frame_inputs reads them); the top adds the posts' yield stress.
R4_PORTAL_INPUTS = _r4_portal_inputs()


def r4_inputs(design: Record, load_case: Record) -> Record:
    """R4_PREDICTIONS' from.<name> values: the portal formulas' inputs off the roll bar, the gaps to the occupants'
    space of the load case's loading, and the shortest distances to it at the portal formulas' deflections
    (rops_inputs.rops_inputs; the rear motion's corner shares from portal_frame_rear at the load case's rear_energy).
    A param the load case lacks is taken from Code 4 and the reference mass, as r4_rops sets it."""
    from hiraku.contract.loading import loading_of
    from hiraku.solvers.k0_handcalc import units
    from hiraku.solvers.k0_handcalc.rops_inputs import rops_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError

    p = load_case.get("params") or {}

    def param(name: str, unit: str, default: float) -> float:
        return units.to_unit(p[name], unit, name) if name in p else default

    M = R4_REFERENCE_MASS_KG
    try:
        return rops_inputs(design, loading=loading_of(load_case),
                           rear_fraction=param("rear_fraction", "1", R4_REAR_FRACTION),
                           rear_energy_J=param("rear_energy", "J", R4_CODE4["rear"] * M),
                           side_energy_J=param("side_energy", "J", R4_CODE4["side"] * M),
                           crush_force_N=param("crush_force", "N", R4_CODE4["crush"] * M))["inputs"]
    except (DesignError, ValueError) as e:
        raise BuildError(str(e)) from None


_R4_ASSUME = ["ロールバーは柱 2 本と上の梁の骨組で、足は張り出しとレール (車輪の所まで) に立つ。塑性ヒンジを一つずつ入れて押し、"
              "横と後ろはエネルギーを吸うまで、上は力まで (力の前に崩れれば柱の長さ)",
              "横・後ろ・上の荷重はそれぞれ組み立てたままの枠から (前の荷重の残ったたわみは入れない)",
              "運転席が前後にスライドする設計 (seat_slide) では、運転者の体の部分の箱は、H3 が大きい人 (AIST の若年男性 95 パーセンタイル) を座らせたスライドの中の位置にある。その位置で数が決まる"]
R4_PREDICTIONS = [
    {"quantity": f"rops_{d}_deflection", "formula_id": fid, "output": out, "assumptions": list(_R4_ASSUME),
     "inputs": {**{k: f"from.{d}_{k}" for k in R4_PORTAL_INPUTS[d]}, **extra}, "inputs_from": r4_inputs}
    for d, fid, out, extra in R4_DEFLECTIONS
] + [
    {"quantity": "rops_occupant_clearance", "formula_id": "frame_static.rops_occupant_clearance",
     "output": "rops_occupant_clearance",
     "assumptions": _R4_ASSUME + ["隙間は、荷重ごとに上の角をたわみの分だけ動かした (柱は足のまわりにまっすぐ回る) ロールバーと、"
                                  "乗員の空間のいちばん近い距離"],
     "inputs": {**{f"{x}_{d}": f"from.{x}_{d}" for d, *_x in R4_DEFLECTIONS for x in ("g", "s", "d0")},
                **{f"d_{d}": f"pred.rops_{d}_deflection" for d, *_x in R4_DEFLECTIONS}},
     "inputs_from": r4_inputs},
]


R4_ROLLOVER_ID = "R4-rollover"
# R4-rollover's title and basis say what it is (the tool's record says the same, rollover_beam.NOT_A_STANDARD)
R4_NOT_A_STANDARD = "HIRAKU 独自の計算の確かめ。規格の試験ではない"


def r4_rops(spec: Record, req: Record, design: Record) -> Record:
    """R4: the vehicle tipped over on the tilt table past its limit lands on its roll bar (rollover_beam); the frame
    must stay out of the occupants' space. The params carry OECD Code 4's side, rear and crushing loads (borrowed)
    for the hand calculation (R4_PREDICTIONS, the static push; the landing does not read them). A catalog param
    side_energy, rear_energy, crush_force or rear_fraction wins over the value from the code and the reference mass.
    Q64 (ROPS) and Q31 (the body sizes the space is made for) are listed when the requirements have them; Q64 saying
    there is none is an error."""
    from hiraku.solvers.k0_handcalc import units

    load, keys = loading(req, design)
    q64 = entry(req, "Q64")
    if q64 is not None and str(q64.get("value")).strip().lower() in ("なし", "no", "none", "false", "0"):
        raise BuildError(f"requirement Q64 says there is no roll-over protective structure ({q64.get('value')!r}); "
                         "R4 does not apply")
    given = spec.get("params", {})

    def param(name: str, unit: str, default: float) -> float:
        if name not in given:
            return default
        try:
            return units.to_unit(given[name], unit, name)
        except ValueError as e:
            raise BuildError(f"catalog params of {spec['id']}: {e}") from None

    M = R4_REFERENCE_MASS_KG
    side = param("side_energy", "J", R4_CODE4["side"] * M)
    rear = param("rear_energy", "J", R4_CODE4["rear"] * M)
    crush = param("crush_force", "N", R4_CODE4["crush"] * M)
    frac = param("rear_fraction", "1", R4_REAR_FRACTION)
    if not 0.0 <= frac <= 0.5 or min(side, rear, crush) <= 0:
        raise BuildError(f"{spec['id']}: the energies and the force must be positive and rear_fraction within 0..0.5")
    req_keys = (["Q64"] if q64 is not None else []) + (["Q31"] if entry(req, "Q31") else [])
    return {
        "load_case_id": R4_ROLLOVER_ID,
        "test_id": "R4",
        "domain": spec["domain"],
        "title": (f"横転: 傾斜台を倒れる角度より先まで傾けて車を床に落とし、ロールバーと骨組みが乗員の空間に入らないか "
                  f"({R4_NOT_A_STANDARD}。手計算は OECD Code 4 の静的試験の形を借りる: 横 {side:g} J・後ろ {rear:g} J・"
                  f"上 {crush / 1000:g} kN、{LOADING_JA[load]})"),
        "params": {"side_energy": q(side, "J"), "rear_energy": q(rear, "J"), "crush_force": q(crush, "N"),
                   "rear_fraction": q(frac, "1"), "loading": load},
        "requirement_keys": req_keys + keys,
        "criterion": {"quantity": "rops_occupant_clearance", "op": ">=", "limit": q(0.0, "mm"),
                      "basis": ("R4: in a rollover (the tilt table past the angle the vehicle tips at; it falls onto "
                                "the floor and lands on its roll bar) no frame member enters the occupants' space; the "
                                "smallest gap over the landing between the frame's members (the roll bar and the rest) "
                                "and the space of k4_geometry.occupant_spaces (the occupant box, widened to AIST D2 and "
                                "raised to AIST I1 of a young male 95th percentile) stays at 0 mm or more. "
                                f"{R4_NOT_A_STANDARD}. OECD Code 4 (2.1: tractors of at least 600 kg, as read on "
                                "2026-11-03, the 600 kg not in README's table of checked values) and ISO 3471 (about "
                                "700 kg and up: 確かめていない) are static tests for machines of 600-700 kg and up; "
                                "whether ANSI/ROHVA 1 has a dynamic test was not checked. Code 4 (3.5.2.2, 3.5.3.2) "
                                "stops its static loading at the same space; its loads are the hand calculation's, and "
                                f"R4's {crush / 1000:g} kN from above (Code 4's 20 N/kg at the reference mass "
                                f"{R4_REFERENCE_MASS_KG:g} kg gives 10 kN) is 「HIRAKU の仮定」"),
                      "requirement_keys": req_keys},
        "tolerance_rel": tolerance(spec, R4_TOL),
    }


from hiraku.catalog.builders.load_path import BUILDERS as _LOAD_PATH_BUILDERS  # noqa: E402  (R7, R8: load_path.py)
from hiraku.catalog.builders.load_path import PATH_PREDICTION as _PATH_PREDICTION  # noqa: E402
from hiraku.catalog.builders.load_path import PLANS as _LOAD_PATH_PLANS  # noqa: E402

# C1's two plans decide by the tool (tool_decides): the path check by hand comes first (judge.path_check reads it)
for _plan in (C1_PLAN, C1_SPLIT_PLAN):
    _plan["predictions"].insert(0, _PATH_PREDICTION)

# R4 judged by K2's rollover_beam (the tool decides, tool_decides): the path check first, then the static push of the
# roll bar as a portal frame (R4_PREDICTIONS: the three deflections and the gap at them)
R4_ROLLOVER_PLAN = {
    "tool": "rollover_beam",
    "say": ("ロールバーを柱 2 本と上の梁の門形とみて、OECD Code 4 の静的試験のエネルギーと力で押したときの乗員の空間までの"
            "隙間を先に見積もります (判定は、傾斜台で倒して骨組みが床に落ちる計算で)"),
    "predictions": [_PATH_PREDICTION] + R4_PREDICTIONS,
    "expected_mismatch": {
        "quantity": "rops_occupant_clearance",
        "source": ("the K2 department's rollover_beam on the video run 20261106-200134's version 5 (two_cargo): -167 mm "
                   "(the measure's floor at the space's middle: side_bar_R2 went 589 mm past the right occupant's "
                   "space's outer face, through its 495 mm width; the largest plastic hinge turn 47 deg, "
                   "side_post_RSG_1, and the roll bar top 991 mm off at the end) against the static push's +63.5 mm, 2026-11-06; the same NG with the break angle 45 or "
                   "90 deg, the floor friction 1, the mesh at K2's sprung mass and the ground contact at half or twice "
                   "its stiffness"),
        "cause": ("手計算は、ロールバーだけを足に立つ門形として、Code 4 の決まったエネルギーと力で静かに押し、そのたわみで乗員の"
                  "空間までの隙間を見る。倒れて床に落ちる計算では、車が自分の重さと回る勢いで床に当たり、ロールバーが大きく"
                  "倒れ (上の端が終わりに 1 m 近く動く)、まわりの骨組み (床に先に当たる側の横の棒と柱) も床に押されて乗員の空間に入る。手計算は横の棒も、"
                  "落ちる勢いも見ない"),
        "fix": ("倒れて落ちる形を手で解く式は無い。手計算はロールバーを静かに押す場合の値として残し、合否は落ちる計算の値 "
                "(骨組みの部材から乗員の空間までのいちばん小さい隙間) で出す。直すなら、床に当たる側の横の棒と柱を支える "
                "(斜めの支え)、ロールバーの柱の根元を強くする"),
        "tool_decides": True,
        "tool_decides_reason": ("倒れて床に落ちる形 (自分の重さと回る勢い、まわりの骨組み) は手で解けない。手計算 (門形の静的な"
                                "押し) は値に届かないので、力の道を手計算で確かめたうえで、落ちる計算の値で決める"),
    },
}
from hiraku.catalog.builders.spray import BUILDERS as _SPRAY_BUILDERS  # noqa: E402  (H6: spray.py)
from hiraku.catalog.builders.spray import PLANS as _SPRAY_PLANS  # noqa: E402
from hiraku.catalog.builders.entry import BUILDERS as _ENTRY_BUILDERS  # noqa: E402  (H7, proposed id: entry.py)
from hiraku.catalog.builders.entry import PLANS as _ENTRY_PLANS  # noqa: E402
from hiraku.catalog.builders.mounts_r9 import BUILDERS as _R9_BUILDERS  # noqa: E402  (R9: mounts_r9.py)
from hiraku.catalog.builders.mounts_r9 import FEEDS as _R9_FEEDS  # noqa: E402
from hiraku.catalog.builders.mounts_r9 import PLANS as _R9_PLANS  # noqa: E402

BUILDERS = {
    **_LOAD_PATH_BUILDERS,
    **_SPRAY_BUILDERS,
    **_ENTRY_BUILDERS,
    **_R9_BUILDERS,
    "C1": c1_crash,
    "N1": n1_modal,
    "R3": r3_fatigue,
    "R4": r4_rops,
    "P4": p4_drive_energy,
    "T1": t1_thermal,
    "T2": t2_thermal,
    "H1": h1_interference,
    "H3": h3_people,
    "H5": h5_assembly,
}
# H1 and H5 by load case (a test's own key would serve its other cases too)
PLANS = {"C1": C1_PLAN, C1_SPLIT_ID: C1_SPLIT_PLAN, R4_ROLLOVER_ID: R4_ROLLOVER_PLAN, "N1": N1_PLAN, "R3": R3_PLAN, "P4": P4_PLAN, "T1": T1_PLAN, "T2": T2_PLAN,
         "H1-interference": H1_OVERLAP_PLAN, "H1-motion": H1_MOTION_PLAN, "H1-steer": H1_PLAN,
         "H1-joints": H1_JOINT_PLAN, "H1-envelope": H1_ENVELOPE_PLAN,
         "H3": H3_PLAN, "H5-order": H5_PLAN, **_LOAD_PATH_PLANS, **_SPRAY_PLANS, **_ENTRY_PLANS,
         **_R9_PLANS}
FEEDS: dict[str, list[str]] = dict(_R9_FEEDS)  # R9 takes the K2 load cases' mount series (mounts_r9.py)
