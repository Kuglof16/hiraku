"""frame_tubes: stresses of a tube frame on shells meshed tube by tube and joined by ties (fe.tubes), test R1.

For frames whose joints are not on a rail-and-cross-member grid (a space frame of round tubes): every
straight run of one section is a tube of MITC4 shells, a branch is cut where it meets its host's wall (a
saddle for an oblique round tube) and tied to it (fe.tubes.frame_shells). The quantity to judge is
max_hot_spot_stress: the largest IIW structural hot-spot stress along the tie lines, read by the rule of
frame_shell (k1_shell.stress.hot_spots: strips one wall thickness wide beside the weld line, the stress
square to it at 0.5 t and 1.5 t, extrapolated to the line; fe.tubes.hot_spots). max_stress, the largest
element stress, sits on the intersection line of a sharp unwelded joint, where the shell's stress is
singular (it grows as the elements shrink); it is kept as a reference.

Loads, the first that applies:
1. an upstream analysis with K2's mount forces as joint vectors at every support's suspension mounts
   ('<support>.<mount>', step_climb_dynamic): those forces at the hardpoints;
2. load case parameter load_source "k2": K2's own load state on this design, standing still after
   settling, or with step_height the step push's state nearest the lift-off (fe.tubes.k2_states), with
   K2's d'Alembert loads of every sprung item (fe.tubes.body_loads);
3. otherwise K0 rigid-body statics (support_loads: at rest, or step_height on step_supports): each wheel's
   ground force and its unsprung weight on the knuckle, carried through the double wishbone at its design
   height to the mounts (k2_vehicle.kinematics.corner_mount_forces at travel 0).
In 1 and 3 the sprung items' weights are added, and what the forces leave unbalanced is taken by inertia
relief (the rigid-body acceleration of the sprung body); the shell is held 3-2-1 at three mounts, so the
holds take only what does not balance (load_balance_residual). With K2's mount force series upstream
(frame_static.upstream_mount_series) the shell is solved again at each instant (one factorisation) into
field.frames per member and a series of the largest hot spot.
"""

from __future__ import annotations

import math

import numpy as np

from ..fe import tubes
from ..k1_shell.stress import CORNER_SKIP_T, HOT_SPOT_READ_T, hot_spot_factors
from .base import (G_FACTOR, LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, check_record, mesh_of,
                   on_board, param_list, param_text, param_value, result, run_param)
from .frame_static import STEP_HEIGHT, STEP_SUPPORTS, add_frames, front_supports, g_factor, upstream_mount_series

STANDARD_GRAVITY = 9.80665
ELEMENT_SIZE_RANGE_MM = (5.0, 40.0)  # HIRAKU's choice: past 40 mm a 34 mm tube has the fewest facets (8)
ELEMENT_SIZE = Param("element_size", "mm", f"target edge of the shell elements; {ELEMENT_SIZE_RANGE_MM[0]:g} to "
                     f"{ELEMENT_SIZE_RANGE_MM[1]:g} mm (the weld strips beside the tie lines are one wall thick "
                     "whatever the size)", default=tubes.ELEMENT_SIZE_MM)
LOAD_SOURCE = Param("load_source", None, "'k0': K0 rigid-body statics carried through the suspension to its mounts; "
                    "'k2': K2's load state on this design (standing still, or at the step's lift-off with "
                    "step_height). An upstream analysis with K2's mount forces is used first in either case",
                    default="k0", choices=("k0", "k2"))
LABEL_MEMBER = "シェルの von Mises (部材ごとの最大、管ごとのシェルを tie でつないだモデル)"
LABEL_HOT_SPOT = "構造的ホットスポット応力 (tie の線に直角、部材ごとの絶対値の最大)"
LABEL_FRAMES = "シェルの von Mises (部材ごとの最大、上流の取付点の力の時刻ごと)"
SOURCES = {
    "upstream": "K2's mount forces from the upstream analysis {lc} (joint vectors)",
    "k2 rest": "K2's load state on this design standing still after settling (t {t:.3f} s)",
    "k2 step": "K2's load state at the step push's lift-off, step {step:g} mm (t {t:.3f} s)",
    "K0 rest": "K0 rigid-body statics at rest, carried through each double wishbone at its design height",
    "K0 step": "K0 rigid-body statics with the step {step:g} mm on {sup}, carried through each double wishbone at its "
               "design height",
}
TIE_ASSUMPTION = ("管は 1 本ずつ中面のシェル (MITC4) で、枝の管は相手の管の壁の中面で切り (丸い管が斜めに当たるところは"
                  "鞍形)、切り口の輪を相手の壁に tie (並進と回転) で貼る。溶接の肉は無く、相手の壁は枝の下でも穴の無い"
                  "まま。並んで当たる枝どうしは貼らない (fe.tubes の仮定)")
HOT_SPOT_ASSUMPTION = (
    "判定に使う量は max_hot_spot_stress (IIW の構造的ホットスポット応力): tie の線 (枝の切り口) の両側に幅 t の要素の帯を"
    " 2 列置き、線から {a:g} t と {b:g} t の要素の中心の面の応力の、線に直角な成分を線まで外挿した値 ({fa:g} σ({a:g}t) "
    "{fb:+g} σ({b:g}t))。外と内の面の両方。枝の側は切り口に沿って全部、相手の管の側は足跡の 4 つの端 (管に沿って前後、"
    "管のまわりに両側。丸い管では冠と鞍の点) の外で読む。角の管の枝は足跡の角から {skip:g} t より近い所を読まない。"
    "frame_shell (k1_shell.stress.hot_spots) と同じ決まり。出典: A. F. Hobbacher, Recommendations for Fatigue Design of "
    "Welded Joints and Components, 2nd ed., Springer 2016 (このリポジトリでは確かめていない)。疲労のための構造応力を"
    "静的な強さの判定に使う (局所の降伏を許さない、安全側の扱い)")
PEAK_ASSUMPTION = ("max_stress は切り口の線の上の要素の値で、鋭い入隅のシェルでは特異 (要素を細かくするほど大きくなる)。参考と"
                   "して残し、判定には使わない")
MOUNT_ASSUMPTION = ("取付点の力は、取付点の節から相手の管の壁の、管の半径の範囲の節へ RBE3 で分けて入れる (金具の大きさとして"
                    "仮定、確かめていない)。その範囲の要素はホットスポットを読まない")
K0_ASSUMPTION = ("K0 の剛体の釣り合いの輪の力は、ばね下の重さと一緒にナックルにかけ、ダブルウィッシュボーンの設計の高さ "
                 "(ストローク 0) の腕と減衰器とタイロッドで取付点に分けた (kinematics.corner_mount_forces)。ばねがたわんだ"
                 "位置の幾何は使っていない")
UPSTREAM_ASSUMPTION = ("上流の joint のベクトル (世界の軸) を設計の軸のまま使い、重力は真下とした。車体の傾きが数度なら、"
                       "その差は慣性の釣り合いに入る")


def _vec(v, nd: int = 1) -> list[float]:
    return [round(float(x), nd) for x in v]


def upstream_joint_loads(upstream: dict | None, design: Record):
    """(load case id, {support: {mount: force N}}, inertia N or None) from the first upstream analysis with a
    joint vector at every suspension mount of every support (at '<support>.<mount>'); None when none has."""
    need = {(s["id"], k) for s in design.get("supports", [])
            for k in tubes.MOUNT_KEYS if k in ((s.get("suspension") or {}).get("hardpoints_mm") or {})}
    for lc_id, ana in (upstream or {}).items():
        if not isinstance(ana, dict) or ana.get("status") != "ok":
            continue
        got: dict = {}
        inertia = None
        for v in ana.get("vectors", []):
            if v.get("kind") == "joint" and "." in str(v.get("at")):
                sid, mount = v["at"].split(".", 1)
                got.setdefault(sid, {})[mount] = np.asarray(v["vector_N"], float)
            elif v.get("kind") == "inertia" and v.get("at") == "cg":
                inertia = np.asarray(v["vector_N"], float)
        if need and all(m in got.get(sid, {}) for sid, m in need):
            return lc_id, got, inertia
    return None


def k0_mount_loads(design: Record, g: float, step_mm, step_supports) -> tuple[dict, str]:
    """{support: {mount: force N}} (design axes) from K0's rigid-body statics: each support's ground vectors and
    its unsprung weight on the knuckle, through corner_mount_forces at travel 0; and the source key."""
    from ..k0_handcalc.vehicle import mass_summary
    from ..k1_shell.frame import _ground
    from ..k2_vehicle import kinematics
    from ..k2_vehicle.model import build_vehicle

    summ = mass_summary(design)
    ground, _, source = _ground(design, summ, g, step_mm, step_supports, None)
    veh = build_vehicle(design)
    unsprung = tubes.unsprung_items(design)
    out = {}
    for c in veh.corners:
        if c.table is None:
            raise LoadCaseError(f"support {c.id} is not on a K&C table with hardpoints: no suspension to carry its "
                                f"ground force to the frame (frame_tubes needs double wishbones on frame nodes)")
        s = next(x for x in design["supports"] if x["id"] == c.id)
        centre = np.asarray(s["position_mm"], float)
        F, M = np.zeros(3), np.zeros(3)
        for _, p, f in ground[c.id]["vectors"]:
            F += f
            M += np.cross(np.asarray(p, float) - centre, f)
        for m, cg, sid in unsprung:
            if sid == c.id:
                w = np.array([0.0, 0.0, -m * STANDARD_GRAVITY * g])
                F += w
                M += np.cross(cg - centre, w)
        got = kinematics.corner_mount_forces(c.table, c.side, 0.0, F, centre / 1000.0, M / 1000.0)
        out[c.id] = {k: np.asarray(f, float) for k, (_, f) in got.items()}
    return out, source


class FrameTubes(BuiltinTool):
    name = "frame_tubes"
    stage = 3  # shell FE
    applies_to = ("space_frame_utv",)
    title = "フレームの静的 (シェル: 管ごとの板を tie でつなぐ、立体フレーム)"
    domain = "strength"
    base = "K1"
    tests = ["R1"]
    formulas = ["frame_static.bending_stress", "mass_props.static_wheel_loads"]
    load_case_params = (LOADING, G_FACTOR, STEP_HEIGHT, STEP_SUPPORTS, LOAD_SOURCE)
    run_params = (ELEMENT_SIZE,)
    predicts = (
        Predicts("max_hot_spot_stress", "frame_static.bending_stress", "max_stress",
                 "M / Z of the member with the largest beam moment at a joint (a rect tube's formula; a round tube's "
                 "Z = pi (D^4 - d^4) / (32 D)); the shell adds the joint's stress concentration, which the formula "
                 "does not"),
    )
    upstream = ("an analysis with K2's mount forces as joint vectors at every suspension mount (step_climb_dynamic): "
                "those forces load the frame; with its mount force series, the frame is solved at each instant")
    needs_mesh = True
    applies_to = "space_frame_utv"  # the kind of design (hiraku.designgen ARCHETYPES key) the tool is for
    stage = 3  # contract tool_spec.stage: shell and solid finite elements

    def known_answers(self) -> list[Record]:
        return tube_known_answers()

    def describe(self) -> Record:
        return dict(super().describe(), applies_to=self.applies_to, stage=self.stage)

    def spec(self) -> Record:
        """The tool_spec; applies_to and stage only when the contract's tool_spec has those fields (it allows no
        others)."""
        from hiraku import contract

        out = super().spec()
        fields = contract.schema().get("$defs", {}).get("tool_spec", {}).get("properties", {})
        if "applies_to" in fields:
            out["applies_to"] = [self.applies_to]
        if "stage" in fields:
            out["stage"] = self.stage
        return out

    def applies(self, design: Record) -> str | None:
        """None for a design this tool is for: every support on a double wishbone (a K&C table with hardpoints)
        whose six body-side pickups are frame nodes, as space_frame_utv builds them; else why not, saying what
        the tool is for and what to use instead."""
        nodes = [np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])]
        missing = []
        for s in design.get("supports", []):
            hp = (s.get("suspension") or {}).get("hardpoints_mm") or {}
            for k in tubes.MOUNT_KEYS:
                if k not in hp or not any(float(np.linalg.norm(p - np.asarray(hp[k], float))) < 1e-6 for p in nodes):
                    missing.append(f"{s.get('id')}.{k}")
        if not design.get("supports") or missing:
            where = f" (not at a frame node: {', '.join(missing[:4])}{', ...' if len(missing) > 4 else ''})" if missing else ""
            return ("frame_tubes は立体フレーム (space_frame_utv) の設計に使います。この設計のサスペンションの取付点はフレームの"
                    f"節にありません{where}。梯子フレームなら frame_shell を使ってください")
        return None

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        why = self.applies(design)
        if why:
            raise LoadCaseError(why)
        design = on_board(design, load_case)
        g = g_factor(load_case)
        size = run_param(params, ELEMENT_SIZE)
        lo, hi = ELEMENT_SIZE_RANGE_MM
        if not lo <= size <= hi:
            raise LoadCaseError(f"run parameter element_size must be {lo:g} to {hi:g} mm, got {size:g} mm")
        step = param_value(load_case, "step_height", "mm")
        sup = None
        if step is not None:
            sup = param_list(load_case, "step_supports") or front_supports(design)
        up = upstream_joint_loads(params.get("upstream_analyses"), design)
        assumptions = [TIE_ASSUMPTION, MOUNT_ASSUMPTION]
        try:
            if up is not None:
                lc_id, mounts, _ = up
                loads = tubes.relieved_loads(design, mounts, np.array([0.0, 0.0, -STANDARD_GRAVITY * g]))
                src = SOURCES["upstream"].format(lc=lc_id)
                assumptions.append(UPSTREAM_ASSUMPTION)
                t_s = None
            elif param_text(load_case, "load_source", "k0") == "k2":
                st = tubes.k2_states(design, step if step is not None else 100.0)
                key = "lift" if step is not None else "static"
                loads = tubes.body_loads(design, st["veh"], st[key])
                t_s = float(st[key]["t"])
                src = SOURCES["k2 step" if step is not None else "k2 rest"].format(step=step or 0.0, t=t_s)
            else:
                mounts, source = k0_mount_loads(design, g, step, sup)
                loads = tubes.relieved_loads(design, mounts, np.array([0.0, 0.0, -STANDARD_GRAVITY * g]))
                src = SOURCES[source].format(step=step or 0.0, sup=",".join(sup or []))
                assumptions.append(K0_ASSUMPTION)
                t_s = None
            r = tubes.analyse_loads(design, {"case": loads}, size)
        except (tubes.TubeMeshError, ValueError) as e:
            raise LoadCaseError(str(e)) from None
        c = r["cases"]["case"]
        if c["status"] != "solved":
            raise LoadCaseError("the shell model was not solved: " + "; ".join(c["reasons"]))
        out = outputs(design, r, loads, src)
        out["assumptions"] = assumptions + out.get("assumptions", [])
        mesh, why = mesh_of(design, params)
        if mesh is not None:
            why = (f"mesh {mesh.get('mesh_id')} is the version's mesh, not the tube-by-tube mesh solved here; the "
                   f"stresses are given per member")
        if why:
            out["effects"].append({"name": why})
        series = upstream_mount_series(params.get("upstream_analyses"), design)
        if series:
            add_frames(out, tube_frames(design, r, series))
        if t_s is not None:
            out["effects"].append({"name": f"K2 state time t {t_s:.3f} s", "value": t_s, "unit": "s"})
        return out


def outputs(design: Record, r: dict, loads: list, src: str) -> Record:
    c = r["cases"]["case"]
    fs = r["shells"]
    hs = c["hot_spots"]
    top = tubes.hot_spot_max(hs)
    yields = {m["id"]: float(design["materials"][m["material"]]["yield_MPa"]) for m in design["members"]}
    peak = c["von_mises_max"]
    results, effects, assumptions = [], [], [PEAK_ASSUMPTION]
    if top is not None:
        v = abs(top["value"])
        results.append(result("max_hot_spot_stress", v, "MPa", top["member"]))
        sf = min(yields[h["member"]] / abs(h["value"]) for h in hs if abs(h["value"]) > 0)
        results.append(result("min_safety_factor", sf, "1", top["member"]))
        fa, fb = hot_spot_factors()
        a, b = HOT_SPOT_READ_T
        effects.append({"name": (f"structural hot spot max {v:.1f} MPa (the quantity to judge): joint {top['joint']}, "
                                 f"{top['side']} side ({top['place']}), {top['member']}, "
                                 f"{'outer' if top['surface'] == 'top' else 'inner'} surface, at "
                                 f"{_vec(top['position_mm'])} mm on the tie line; {top['sigma_05t']:.1f} MPa at {a:g} t and "
                                 f"{top['sigma_15t']:.1f} MPa at {b:g} t (t {top['t_mm']:g} mm), extrapolated {fa:g} x "
                                 f"{top['sigma_05t']:.1f} {fb:+g} x {top['sigma_15t']:.1f} = {top['value']:+.1f} MPa"),
                        "value": v, "unit": "MPa"})
        effects.append({"name": f"hot spots read: {len(hs)} (pairs x 2 surfaces) at {len({h['joint'] for h in hs})} joints",
                        "value": len(hs), "unit": "1"})
        a_ = HOT_SPOT_READ_T[0]
        assumptions.insert(0, HOT_SPOT_ASSUMPTION.format(a=a_, b=HOT_SPOT_READ_T[1], fa=fa, fb=fb, skip=CORNER_SKIP_T))
    else:
        effects.append({"name": "no structural hot spot read (no tie line with weld strips outside the mounts)"})
    results += [
        result("max_stress", peak["MPa"], "MPa", f"{peak['tube']} near {peak['joint']}"),
        result("frame_mass", r["shell_mass_kg"], "kg"),
        result("load_balance_residual", c["load_balance_residual"], "1"),
        result("equilibrium_residual", c["equilibrium_residual"], "1"),
    ]
    effects.append({"name": (f"shell max {peak['MPa']:.1f} MPa (max_stress, a reference: on the intersection line of a sharp "
                             f"unwelded joint, singular) in {peak['tube']}, {peak['distance_to_joint_mm']:.0f} mm from "
                             f"node {peak['joint']}"), "value": peak["MPa"], "unit": "MPa"})
    effects.append({"name": src})
    effects.append({"name": (f"shell model: {r['n_nodes']} nodes, {r['n_elements']} elements, {r['n_ties']} ties; host stubs "
                             f"past corner nodes {r['stub_mass_kg']:.2f} kg (not in the beam model)"),
                    "value": r["n_elements"], "unit": "1"})
    per_member: dict[str, float] = {}
    hot_member: dict[str, float] = {}
    from ..fe.shell import shell_stresses

    sol = r["solutions"]["case"]
    for tb in fs.tubes.values():
        vm = shell_stresses(sol, tb.group).von_mises("max")
        cen = shell_stresses(sol, tb.group).centres
        for k in range(len(vm)):
            m = tubes.member_at(tb, fs, cen[k])
            per_member[m] = max(per_member.get(m, 0.0), float(vm[k]))
    for h in hs:
        hot_member[h["member"]] = max(hot_member.get(h["member"], 0.0), abs(h["value"]))
    fields = [{"kind": "stress", "label": LABEL_MEMBER, "unit": "MPa",
               "per_member": {m: round(v, 2) for m, v in per_member.items()}}]
    if hot_member:
        fields.append({"kind": "stress", "label": LABEL_HOT_SPOT, "unit": "MPa",
                       "per_member": {m: round(v, 2) for m, v in hot_member.items()}})
    vectors = [{"kind": "joint", "at": l.where, "origin_mm": _vec(l.point_mm, 3), "vector_N": _vec(l.force_N, 3)}
               for l in loads if l.kind == "mount"]
    return {"results": results, "fields": fields, "vectors": vectors, "effects": effects, "assumptions": assumptions}


def tube_frames(design: Record, r: dict, series) -> Record:
    """frame_tubes over the instants of upstream_mount_series, on r's model (one factorisation)."""
    from ..fe.model import StaticSolver
    from ..fe.shell import shell_stresses

    lc_id, ana_id, instants = series
    fs = r["shells"]
    solver = StaticSolver(fs.model)
    mt = tubes.member_tube_map(fs)
    per_member, hot, at = [], [], []
    for inst in instants:
        mounts = {sid: {m: f for m, _, f in ml["vectors"]} for sid, ml in inst["mount_loads"].items()}
        loads = tubes.relieved_loads(design, mounts, np.asarray(inst["gravity_mps2"], float))
        sol = solver.solve(tubes.shell_load_vector(fs, loads, mt))
        pm: dict[str, float] = {}
        for tb in fs.tubes.values():
            ss = shell_stresses(sol, tb.group)
            vm = ss.von_mises("max")
            for k in range(len(vm)):
                m = tubes.member_at(tb, fs, ss.centres[k])
                pm[m] = max(pm.get(m, 0.0), float(vm[k]))
        per_member.append({m: round(v, 2) for m, v in pm.items()})
        top = tubes.hot_spot_max(tubes.hot_spots(fs, sol, r["excluded_nodes"]))
        hot.append(abs(top["value"]) if top else 0.0)
        at.append(f"joint {top['joint']} ({top['member']})" if top else "")
    t = [inst["t_s"] for inst in instants]
    k = int(np.argmax(hot))
    frames = {"t_s": t, "per_member": per_member, "max": [round(max(p.values()), 2) for p in per_member]}
    if ana_id:
        frames["source_analysis_id"] = ana_id
    return {"fields": [{"kind": "stress", "label": LABEL_FRAMES, "unit": "MPa", "frames": frames}],
            "series": [{"channel": "max_hot_spot_stress", "unit": "MPa", "t_s": t, "values": [round(v, 3) for v in hot]}],
            "results": [result("max_hot_spot_stress_over_time", hot[k], "MPa", f"{at[k]} at t {t[k]:.3f} s")],
            "effects": [{"name": f"shell solved at {len(t)} instants of {lc_id}'s mount forces: largest hot spot "
                                 f"{hot[k]:.1f} MPa at {at[k]}, t {t[k]:.3f} s", "value": hot[k], "unit": "MPa"}]}

# ---------------------------------------------------------------- known answers

T_JOINT_P_N = 100.0
T_SIZE_MM = 10.0


def t_joint_design() -> Record:
    """k1_shell's T joint (tests of the hot spot): a 40x80x2.3 tube A-M-B (600 mm) and a 40x40x2.3 tube M-C ending
    square on its side wall."""
    from .fixtures import STEEL

    return {"design_version": 1, "name": "t joint", "variables": {}, "supports": [], "parts": [],
            "nodes": [{"id": "A", "xyz_mm": [0.0, 0.0, 0.0]}, {"id": "M", "xyz_mm": [300.0, 0.0, 0.0]},
                      {"id": "B", "xyz_mm": [600.0, 0.0, 0.0]}, {"id": "C", "xyz_mm": [300.0, -250.0, 0.0]}],
            "members": [{"id": "r1", "node_a": "A", "node_b": "M", "section": "rail", "material": "st"},
                        {"id": "r2", "node_a": "M", "node_b": "B", "section": "rail", "material": "st"},
                        {"id": "br", "node_a": "M", "node_b": "C", "section": "cross", "material": "st"}],
            "sections": {"rail": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.3}},
                         "cross": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.3}}},
            "materials": {"st": STEEL}}


def t_joint_tubes(size: float = T_SIZE_MM, P: float = T_JOINT_P_N):
    """The T joint on fe.tubes (ties), held at both ends of the through tube, P down at the branch's end."""
    from ..fe.connect import rigid_link
    from ..fe.model import StaticSolver

    d = t_joint_design()
    fs = tubes.frame_shells(d, size)
    m = fs.model
    host, br = fs.tubes["r1+r2"], fs.tubes["br"]
    m.fix(np.r_[host.ids[0], host.ids[-1]], "all")
    tip = m.add_node([300.0, -250.0, 0.0])
    m.add(rigid_link(tip, br.ids[-1], m.nodes))
    F = np.zeros((len(m.nodes), 6))
    F[tip, 2] = -P
    sol = StaticSolver(m).solve(F)
    return fs, sol, tubes.hot_spots(fs, sol)


def t_joint_k1(size: float = T_SIZE_MM, P: float = T_JOINT_P_N) -> list:
    """The same T joint on k1_shell (shared nodes, weld strips): its hot spots."""
    from ..k1_shell import stress
    from ..k1_shell.mesh import build_mesh
    from ..k1_shell.solve import ShellModel, StaticSolver, dofs_of

    d = t_joint_design()
    ms = build_mesh(d, size)
    model = ShellModel.from_mesh(ms, d)
    tip = model.add_node((300.0, -250.0, 0.0))
    model.links += [(int(n), tip) for n in ms.end_rings[("br", "b")]]
    F = np.zeros(model.n_dof)
    F[6 * tip + 2] = -P
    fixed = dofs_of(np.concatenate([ms.end_rings[("r1", "a")], ms.end_rings[("r2", "b")]]))
    U = StaticSolver(model, fixed).solve(F)
    return stress.hot_spots(ms, stress.element_stresses(model, U))


def branch_bending(fs, sol, P: float = T_JOINT_P_N) -> tuple[float, float, float]:
    """(shell, beam theory, s) for the branch of the T joint: the outer-surface axial stress of its top wall's
    element nearest the middle of the branch, against M c / I of the mid-line section (the shell's own: t
    times the mid-line rectangle), M = P x (x from the load), c to the outer surface."""
    from ..fe.shell import shell_stresses

    br = fs.tubes["br"]
    ss = shell_stresses(sol, br.group)
    ey, ez = br.axes()
    up = ez if abs(ez[2]) > 0.99 else ey
    hy, hz = (br.dims["h"] - br.t) / 2.0, (br.dims["b"] - br.t) / 2.0
    hu, hs = (hz, hy) if up is ez else (hy, hz)  # half sizes along the bending direction and across
    t = br.t
    I = t * (2 * (2 * hu) ** 3 / 12.0 + 2 * (2 * hs) * hu ** 2)
    w = ss.centres - br.p0
    s = w @ br.d
    top = np.nonzero((w @ up) * np.sign(up[2]) > hu - 1e-6)[0]
    k = int(top[np.argmin(np.abs(s[top] - br.L / 2.0))])
    x = br.L - float(s[k])
    expected = P * x * (hu + t / 2.0) / I
    got = float(ss.in_plane(br.d, "top")[k, 0])
    return got, expected, float(s[k])


def tube_known_answers() -> list[Record]:
    from ..fe import checks

    out = []
    case = (f"T joint of rect tubes (40x80x2.3 through tube held at both ends, 40x40x2.3 branch square on its side "
            f"wall), {T_JOINT_P_N:g} N down at the branch's end, {T_SIZE_MM:g} mm elements")
    fs, sol, hs = t_joint_tubes()
    for size, tol, what in ((T_SIZE_MM, 1e-4, "at this size every ring node falls on a host node, so the joint is "
                                              "shared nodes as k1_shell's: a check of the read-out rule on the same mesh"),
                            (5.0, 0.03, "at 5 mm 10 of the ring's 48 nodes are tied: a cross-check of the tie, not a "
                                        "closed form")):
        c = case if size == T_SIZE_MM else case.replace(f"{T_SIZE_MM:g} mm elements", f"{size:g} mm elements")
        h_t = hs if size == T_SIZE_MM else t_joint_tubes(size)[2]
        ref = t_joint_k1(size)
        for side in ("host", "branch"):
            a = tubes.hot_spot_max([h for h in h_t if h["side"] == side])
            b = tubes.hot_spot_max([h for h in ref if h["side"] == side])
            out.append(check_record(c, f"max_hot_spot_stress ({side} side)", abs(b["value"]),
                                    abs(a["value"]) if a else None, "MPa", tol,
                                    "k1_shell.stress.hot_spots on the same joint meshed with shared nodes (frame_shell's "
                                    "rule and mesh); " + what))
    got, exp, s = branch_bending(fs, sol)
    out.append(check_record(case, f"branch top wall outer axial stress at s = {s:.0f} mm", exp, got, "MPa", 0.03,
                            "beam theory M c / I (I of the shell's mid-line section)"))
    for ang in (60.0, 45.0):
        a = checks.oblique_round_t(ang, 16)
        tip, gap = _oblique_tie(ang)
        c = f"oblique round T at {ang:g} deg (fe check 9: 32 x 2 tube, 17 x 2 branch, 16 facets on the circle)"
        out.append(check_record(c, "tip deflection (z)", float(a["tip"][2]), tip, "mm", 0.01,
                                "fe.checks.oblique_round_t (the saddle cut and tie built by hand); rows rounded vs ceiled"))
        out.append(check_record(c, "largest tie gap (saddle on the facets)", float(a["max_gap"]), gap, "mm", 1e-6,
                                "fe.checks.oblique_round_t"))
    return out


def _oblique_tie(ang: float, n_c: int = 16, R: float = 30.0, r: float = 15.0, t: float = 2.0, L: float = 400.0,
                 Lb: float = 200.0, P: float = 500.0) -> tuple[float, float]:
    from ..fe.checks import STEEL
    from ..fe.connect import rigid_link
    from ..fe.model import StaticSolver

    a = math.radians(ang)
    dd = np.array([math.cos(a), math.sin(a), 0.0])
    nodes = {"A": (-L / 2, 0, 0), "O": (0, 0, 0), "B": (L / 2, 0, 0), "C": tuple(Lb * dd)}
    d = {"nodes": [{"id": k, "xyz_mm": [float(x) for x in v]} for k, v in nodes.items()],
         "members": [{"id": m, "node_a": p, "node_b": q, "section": s, "material": "st"}
                     for m, p, q, s in (("h1", "A", "O", "h"), ("h2", "O", "B", "h"), ("br", "O", "C", "b"))],
         "sections": {"h": {"shape": "round_tube", "dims_mm": {"d": 2 * R + t, "t": t}},
                      "b": {"shape": "round_tube", "dims_mm": {"d": 2 * r + t, "t": t}}},
         "materials": {"st": STEEL}}
    fs = tubes.frame_shells(d, 2 * math.pi * R / n_c, facet=False, weld_strips=False)
    m = fs.model
    host, br = fs.tubes["h1+h2"], fs.tubes["br"]
    m.fix(np.r_[host.ids[0], host.ids[-1]], "all")
    tip = m.add_node(Lb * dd)
    m.add(rigid_link(tip, br.ids[-1], m.nodes))
    F = np.zeros((len(m.nodes), 6))
    F[tip, 2] = -P
    sol = StaticSolver(m).solve(F)
    return float(sol.displacements[tip, 2]), float(max(p["gap"] for c in fs.ties for p in c.projection))


__all__ = ["FrameTubes", "k0_mount_loads", "t_joint_design", "t_joint_k1", "t_joint_tubes", "tube_known_answers",
           "upstream_joint_loads"]
