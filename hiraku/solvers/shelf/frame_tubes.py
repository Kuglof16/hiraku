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

import hashlib
import math
from collections import OrderedDict

import numpy as np

from ..fe import tubes
from ..k1_shell.stress import CORNER_SKIP_T, HOT_SPOT_READ_T, hot_spot_factors
from . import hot_spots
from .base import (G_FACTOR, LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, check_record, mesh_of,
                   on_board, param_list, param_text, param_value, result, run_param)
from .frame_static import (PATH_FORMULA, PATH_PREDICTS, STEP_HEIGHT, STEP_SUPPORTS, add_frames, front_supports,
                           g_factor, lost_load_outputs, upstream_mount_series)

# Standard gravity, a defined value (3rd CGPM, 1901; ISO 80000-3), as k0_handcalc.units.STANDARD_GRAVITY. Not checked
# against those texts in this repository.
STANDARD_GRAVITY = 9.80665
ELEMENT_SIZE_RANGE_MM = (5.0, 40.0)  # HIRAKU's choice: past 40 mm a 34 mm tube has the fewest facets (8)
ELEMENT_SIZE = Param("element_size", "mm", f"target edge of the shell elements; {ELEMENT_SIZE_RANGE_MM[0]:g} to "
                     f"{ELEMENT_SIZE_RANGE_MM[1]:g} mm (the weld strips beside the tie lines are one wall thick "
                     "whatever the size)", default=tubes.ELEMENT_SIZE_MM)
HOT_SPOT_FACETS_RANGE = (tubes.MIN_ROUND_FACETS, 96)
HOT_SPOT_FACETS = Param("hot_spot_facets", "1", "the least facet count of a round tube, which the rows round each "
                        f"joint are spaced at too; a multiple of 4, {HOT_SPOT_FACETS_RANGE[0]} to "
                        f"{HOT_SPOT_FACETS_RANGE[1]}. Leave it at {tubes.HOT_SPOT_MIN_FACETS} (the hot spots are read "
                        "at that count): fewer only for a quick model whose values are not judged (a test that needs "
                        "the results' names); the hot spots then come out lower by an amount not measured",
                        default=float(tubes.HOT_SPOT_MIN_FACETS))
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
QUICK_FACET_ASSUMPTION = (
    "丸い管を {n} 角の多角形にした速い模型 (実行の入力 hot_spot_facets)。ホットスポット応力は {n0} 角の読みより低く出る。"
    "どれだけ低いかは測っていないので、この値で判定しない")
FACET_ASSUMPTION = (
    "丸い管は、要素の大きさにかかわらず {n} 角 (以上) の多角形の管にした。鞍と冠のホットスポットは角の数で決まり、丸い T "
    "(42.7×2.3 に 34×2.3、要素 14 mm) で測ると、{n} 角の値は 96 角より最大 6.6 % 低い (90° の枝の側。90° の相手の管の側 "
    "3.2 %、60° は相手の管の側 2.7 %・枝の側 2.4 % 低い。45° は相手の管の側 0.4 % 低く、枝の側 0.9 % 高い)。"
    "この値のホットスポット応力は、安全側ではない (最大 6.6 % 小さく出ている) ものとして読む。要素の大きさでは収束している"
    " (細かい 2 つの差 0.5 % 以内)")
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
    formulas = ["frame_static.bending_stress", "mass_props.static_wheel_loads", PATH_FORMULA]
    load_case_params = (LOADING, G_FACTOR, STEP_HEIGHT, STEP_SUPPORTS, LOAD_SOURCE)
    run_params = (ELEMENT_SIZE, HOT_SPOT_FACETS)
    predicts = (
        Predicts("max_hot_spot_stress", "frame_static.bending_stress", "max_stress",
                 "M / Z of the member with the largest beam moment at a joint (a rect tube's formula; a round tube's "
                 "Z = pi (D^4 - d^4) / (32 D)); the shell adds the joint's stress concentration, which the formula "
                 "does not"),
        PATH_PREDICTS,
    )
    upstream = ("an analysis with K2's mount forces as joint vectors at every suspension mount (step_climb_dynamic): "
                "those forces load the frame; with its mount force series, the frame is solved at each instant")
    needs_mesh = True
    applies_to = ["space_frame_utv"]  # the kinds of design (hiraku.designgen ARCHETYPES keys) the tool is for
    stage = 3  # contract tool_spec.stage: shell and solid finite elements

    def known_answers(self) -> list[Record]:
        return tube_known_answers()

    def describe(self) -> Record:
        return dict(super().describe(), applies_to=list(self.applies_to), stage=self.stage)

    def spec(self) -> Record:
        """The tool_spec; applies_to and stage only when the contract's tool_spec has those fields (it allows no
        others)."""
        from hiraku import contract

        out = super().spec()
        fields = contract.schema().get("$defs", {}).get("tool_spec", {}).get("properties", {})
        if "applies_to" in fields:
            out["applies_to"] = list(self.applies_to)
        if "stage" in fields:
            out["stage"] = self.stage
        return out

    def shape(self, design: Record) -> str | None:
        """None for a design this tool is for: every support on a double wishbone (a K&C table with hardpoints)
        whose six body-side pickups are frame nodes, as space_frame_utv builds them; else why not, saying what
        the tool is for and what to use instead (BuiltinTool.applies says it with the tool's name)."""
        nodes = [np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])]
        missing = []
        for s in design.get("supports", []):
            hp = (s.get("suspension") or {}).get("hardpoints_mm") or {}
            for k in tubes.MOUNT_KEYS:
                if k not in hp or not any(float(np.linalg.norm(p - np.asarray(hp[k], float))) < 1e-6 for p in nodes):
                    missing.append(f"{s.get('id')}.{k}")
        if not design.get("supports") or missing:
            where = f" (not at a frame node: {', '.join(missing[:4])}{', ...' if len(missing) > 4 else ''})" if missing else ""
            return ("立体フレーム (space_frame_utv) の設計に使います。この設計のサスペンションの取付点はフレームの"
                    f"節にありません{where}。梯子フレームなら frame_shell を使ってください")
        if any((s.get("suspension") or {}).get("type") != "kc_table" or not s["suspension"].get("kc_table")
               for s in design["supports"]):
            return ("立体フレーム (space_frame_utv) の設計に使います。どの支持もサスペンションが K&C 表 "
                    "(suspension.type kc_table と kc_table) であることが要ります")
        return None

    def make_mesh(self, design: Record) -> Record:
        """The tube-by-tube shell mesh of the design as a contract `mesh` record without mesh_id (mesher
        frame_tubes), at the default element size. The model behind it (mounts, holds, and the factorisation once
        solved) is kept in this process, so the version's other load cases reuse it (_PREPARED)."""
        why = self.applies(design)
        if why:
            raise LoadCaseError(why)
        try:
            p = _prepared_for(design, tubes.ELEMENT_SIZE_MM)
        except (tubes.TubeMeshError, ValueError) as e:
            raise LoadCaseError(str(e)) from None
        rec = tubes.mesh_record(p["fs"], design, self.name)
        _remember(rec, p)
        return rec

    def _mesh_to_solve_on(self, design: Record, params: dict, size: float):
        """(the passed mesh to draw on or None, the prepared model to solve on or None, why the mesh is not
        drawn on or None). The passed mesh is used when it is this tool's (mesher) at this element size: its
        model from this process's cache, else meshed again from the design and checked to be the same."""
        mesh, why = mesh_of(design, params)
        if mesh is None:
            return None, None, why
        if mesh.get("mesher") != self.name:
            return None, None, (f"mesh {mesh.get('mesh_id')} was made by {mesh.get('mesher') or 'frame_mesh'}, not by "
                                f"{self.name}; the stresses are given per member")
        if abs(float(mesh.get("element_size_mm", -1.0)) - size) > 1e-9:
            return None, None, (f"mesh {mesh.get('mesh_id')} has {mesh.get('element_size_mm')} mm elements, the run asks "
                                f"for {size:g} mm: solved on a mesh of that size, not drawn on {mesh.get('mesh_id')}")
        p = _PREPARED.get(_digest(mesh))
        if p is None:
            p = _prepared_for(design, size)
            again = tubes.mesh_record(p["fs"], design, self.name)
            if _digest(again) != _digest(mesh):
                return None, p, (f"mesh {mesh.get('mesh_id')} is not the tube-by-tube mesh of this design at {size:g} mm; "
                                 f"solved on that one, not drawn on {mesh.get('mesh_id')}")
            _remember(again, p)
        return mesh, p, None

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
        nf = run_param(params, HOT_SPOT_FACETS)
        flo, fhi = HOT_SPOT_FACETS_RANGE
        if not (flo <= nf <= fhi and nf == int(nf) and int(nf) % 4 == 0):
            raise LoadCaseError(f"run parameter hot_spot_facets must be a multiple of 4 from {flo} to {fhi}, got {nf:g}")
        facets = None if int(nf) == tubes.HOT_SPOT_MIN_FACETS else int(nf)
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
            if facets is None:
                mesh, prepared, why = self._mesh_to_solve_on(design, params, size)
                prepared = prepared or _prepared_for(design, size)
            else:  # the version's mesh is at the default facet count: solved on a mesh of its own, not drawn on
                given, why = mesh_of(design, params)
                if given is not None and given.get("mesher") != self.name:
                    why = (f"mesh {given.get('mesh_id')} was made by {given.get('mesher') or 'frame_mesh'}, not by "
                           f"{self.name}; the stresses are given per member")
                elif given is not None:
                    why = (f"solved on a quick mesh with {facets} facets per round tube (hot_spot_facets), not drawn "
                           f"on {given.get('mesh_id')}")
                mesh, prepared = None, _prepared_for(design, size, facets)
            r = tubes.analyse_loads(design, {"case": loads}, size, prepared=prepared)
        except (tubes.TubeMeshError, ValueError) as e:
            raise LoadCaseError(str(e)) from None
        c = r["cases"]["case"]
        if c["status"] != "solved":
            raise LoadCaseError("the shell model was not solved: " + "; ".join(c["reasons"]))
        out = outputs(design, r, loads, src, self, load_case)
        out["assumptions"] = (assumptions + out.get("assumptions", [])
                              + [FACET_ASSUMPTION.format(n=tubes.HOT_SPOT_MIN_FACETS) if facets is None
                                 else QUICK_FACET_ASSUMPTION.format(n=facets, n0=tubes.HOT_SPOT_MIN_FACETS)])
        if mesh is not None:
            out["fields"] += tube_mesh_fields(r, mesh)
        if why:
            out["effects"].append({"name": why})
        series = upstream_mount_series(params.get("upstream_analyses"), design)
        if series:
            add_frames(out, tube_frames(design, r, series))
        if t_s is not None:
            out["effects"].append({"name": f"K2 state time t {t_s:.3f} s", "value": t_s, "unit": "s"})
        return out


_PREPARED: "OrderedDict[str, dict]" = OrderedDict()  # mesh digest -> tubes.prepare() record, this process
CACHE_SIZE = 2  # versions kept (a frame's model is some hundreds of MB)


def _digest(mesh: Record) -> str:
    """A digest of a mesh record's geometry and element order (not its id, quality or notes)."""
    h = hashlib.sha1()
    h.update(np.asarray(mesh["nodes_mm"], float).round(4).tobytes())
    h.update(np.asarray(mesh["elements"], np.int64).tobytes())
    h.update("|".join(mesh["element_member"]).encode())
    h.update(repr(float(mesh.get("element_size_mm", 0.0))).encode())
    return h.hexdigest()


_BY_DESIGN: "OrderedDict[str, dict]" = OrderedDict()  # design content + element size -> tubes.prepare() record


def _prepared_for(design: Record, size: float, facets: int | None = None) -> dict:
    """tubes.prepare(design, size), kept in this process by the design's content and the size (CACHE_SIZE of
    them): a version's load cases, and a run without the version's mesh, do not mesh the frame again. facets:
    tubes.prepare's hot_spot_facets (None: the default)."""
    key = tubes._content_key(design, float(size)) if facets is None else tubes._content_key(design, float(size),
                                                                                              int(facets))
    p = _BY_DESIGN.get(key)
    if p is None:
        p = tubes.prepare(design, size, hot_spot_facets=facets)
        _BY_DESIGN[key] = p
    _BY_DESIGN.move_to_end(key)
    while len(_BY_DESIGN) > CACHE_SIZE:
        _BY_DESIGN.popitem(last=False)
    return p


def _remember(mesh: Record, prepared: dict) -> None:
    _PREPARED[_digest(mesh)] = prepared
    _PREPARED.move_to_end(_digest(mesh))
    while len(_PREPARED) > CACHE_SIZE:
        _PREPARED.popitem(last=False)


LABEL_MESH = {"max": "シェルの von Mises (外と内の面の大きい方、管ごとのシェル)",
              "top": "シェルの von Mises (外の面、管ごとのシェル)", "bottom": "シェルの von Mises (内の面、管ごとのシェル)"}


def tube_mesh_fields(r: dict, mesh: Record) -> list[Record]:
    """Shell von Mises per element (three surfaces) on the passed frame_tubes mesh, in its element order."""
    from ..fe.shell import shell_stresses

    fs, sol = r["shells"], r["solutions"]["case"]
    out = []
    for surface in ("max", "top", "bottom"):
        per = {tb.id: shell_stresses(sol, tb.group).von_mises(surface) for tb in fs.tubes.values()}
        out.append({"kind": "stress", "label": LABEL_MESH[surface], "unit": "MPa", "mesh_id": mesh["mesh_id"],
                    "surface": surface, "per_element": [round(v, 2) for v in tubes.element_values(fs, per)]})
    return out


def outputs(design: Record, r: dict, loads: list, src: str, tool=None, load_case: Record | None = None) -> Record:
    """results / fields / vectors / effects / assumptions of one frame_tubes analysis; with tool (and the load
    case), hot_spots too (tube_hot_spots)."""
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
    lost, said = lost_load_outputs()
    results.append(lost)
    effects.append(said)
    out = {"results": results, "fields": fields, "vectors": vectors, "effects": effects, "assumptions": assumptions}
    if tool is not None:
        hot = tube_hot_spots(tool, load_case, hs, per_member)
        if hot is not None:
            out["hot_spots"] = hot
    return out


def tube_hot_spots(tool, load_case: Record, hs: list, per_member: dict[str, float]) -> list[Record] | None:
    """analysis.hot_spots of the tube shells (hot_spots module), on the quantity the tool is judged on (frame_tubes
    has no judged_as, so the criterion's own):
    - max_stress (R1): one row per member, its largest element von Mises over all its tubes' elements (as
      max_stress is read: the mounts' elements included), where = the member;
    - max_hot_spot_stress (R1s): one row per weld line group, a member at a joint (the largest size of the hot
      spots read there), where = "<member>@<joint>", plus the element peak as a row of quantity max_stress with
      hot_spots.SHARP_NOTE (not the judged quantity there).
    The limit is the criterion's. None when the criterion is not a "<=" stress or is judged on another quantity."""
    judged = hot_spots.judged_limit(tool, load_case)
    if judged is None or judged[0] not in ("max_stress", "max_hot_spot_stress"):
        return None
    q, limit = judged
    if q == "max_stress":
        return hot_spots.top([hot_spots.row(m, q, v, limit) for m, v in per_member.items()])
    groups: dict[tuple[str, str], dict] = {}
    for h in hs:
        k = (h["member"], h["joint"])
        if k not in groups or abs(h["value"]) > abs(groups[k]["value"]):
            groups[k] = h
    rows = [hot_spots.row(f"{h['member']}@{h['joint']}", q, abs(h["value"]), limit,
                          note=(f"joint {h['joint']} ({h['kind']}), {h['side']} side, {h['place']}, tube {h['tube']}, "
                                f"{'outer' if h['surface'] == 'top' else 'inner'} surface"),
                          at_mm=h["position_mm"])
            for h in groups.values()]
    keep = []
    if per_member:
        m = max(per_member, key=per_member.get)
        keep.append(hot_spots.row(m, "max_stress", per_member[m], limit, note=hot_spots.SHARP_NOTE))
    return hot_spots.top(rows, keep=keep)


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
    secs = {k: v["dims_mm"] for k, v in t_joint_design()["sections"].items()}
    rail, cross = secs["rail"], secs["cross"]

    def case_ja(size: float) -> str:
        return (f"角パイプの T 継手 ({rail['b']:g}x{rail['h']:g}x{rail['t']:g} の通しの管を両端で止め、"
                f"{cross['b']:g}x{cross['h']:g}x{cross['t']:g} の枝をその横の壁に直角に当てた)、枝の先に "
                f"{T_JOINT_P_N:g} N を下向きに、要素の大きさ {size:g} mm")

    src_ja = ("同じ継手を節点を共有するメッシュにした k1_shell.stress.hot_spots の値 (frame_shell の読み方とメッシュ)。")
    fs, sol, hs = t_joint_tubes()
    fine = 5.0
    for size, tol, what, what_ja in (
            (T_SIZE_MM, 1e-4, "at this size every ring node falls on a host node, so the joint is "
                              "shared nodes as k1_shell's: a check of the read-out rule on the same mesh",
             "この大きさでは枝の切り口の輪の節点がすべて相手の管の節点に重なり、k1_shell と同じ節点を共有する継手になる。"
             "同じメッシュで読み方を確かめる"),
            (fine, 0.03, "at 5 mm 10 of the ring's 48 nodes are tied: a cross-check of the tie, not a "
                         "closed form",
             f"{fine:g} mm では輪の 48 個の節点のうち 10 個が tie でつながる。tie の確かめあわせで、式で出した答えではない")):
        c = case if size == T_SIZE_MM else case.replace(f"{T_SIZE_MM:g} mm elements", f"{size:g} mm elements")
        h_t = hs if size == T_SIZE_MM else t_joint_tubes(size)[2]
        ref = t_joint_k1(size)
        for side in ("host", "branch"):
            a = tubes.hot_spot_max([h for h in h_t if h["side"] == side])
            b = tubes.hot_spot_max([h for h in ref if h["side"] == side])
            out.append(check_record(c, f"max_hot_spot_stress ({side} side)", abs(b["value"]),
                                    abs(a["value"]) if a else None, "MPa", tol,
                                    "k1_shell.stress.hot_spots on the same joint meshed with shared nodes (frame_shell's "
                                    "rule and mesh); " + what,
                                    case_ja=case_ja(size),
                                    source_ja=src_ja + what_ja))
    got, exp, s = branch_bending(fs, sol)
    out.append(check_record(case, f"branch top wall outer axial stress at s = {s:.0f} mm", exp, got, "MPa", 0.03,
                            "beam theory M c / I (I of the shell's mid-line section)",
                            case_ja=case_ja(T_SIZE_MM),
                            source_ja="梁の式 M c / I (I はシェルの中面の断面のもの)"))
    n_c = 16
    for ang in (60.0, 45.0):
        a = checks.oblique_round_t(ang, n_c)
        tip, gap = _oblique_tie(ang)
        c = f"oblique round T at {ang:g} deg (fe check 9: 32 x 2 tube, 17 x 2 branch, 16 facets on the circle)"
        c_ja = (f"丸パイプが {ang:g}° で斜めに当たる T 継手 (fe の確かめ 9: 32 x 2 の管に 17 x 2 の枝、円を {n_c} 角に)")
        out.append(check_record(c, "tip deflection (z)", float(a["tip"][2]), tip, "mm", 0.01,
                                "fe.checks.oblique_round_t (the saddle cut and tie built by hand); rows rounded vs ceiled",
                                case_ja=c_ja,
                                source_ja="fe.checks.oblique_round_t (鞍形の切り口と tie を手で組んだもの)。"
                                          "輪の数を四捨五入するか切り上げるかが違う"))
        out.append(check_record(c, "largest tie gap (saddle on the facets)", float(a["max_gap"]), gap, "mm", 1e-6,
                                "fe.checks.oblique_round_t",
                                case_ja=c_ja,
                                source_ja="fe.checks.oblique_round_t (鞍形の切り口と tie を手で組んだもの)"))
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
