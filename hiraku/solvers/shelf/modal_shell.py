"""modal_shell (N1): natural frequencies and mode shapes of the frame on its springs, on the shell mesh of the
tube walls (k1_shell.modal), next to the beam model's (modal, k1_frame.modal) and the wheel and motor rates.

A tool of its own rather than a run option of modal: modal is a light (stage 1) check run on every design
version, the shell's factorisation is heavy (stage 3, as frame_shell beside frame_static), and the two are
judged on the same criterion (first_frame_mode), so the stages decide when the heavier one is run.

Results: mode_<i> (Hz, field_index for the modes with fields), first_body_mode, first_frame_mode (the
first mode that bends the frame itself, FLEX_LIMIT on the mesh nodes of the frame's elements, as modal on its
nodes), first_rollbar_mode, the wheel and motor rotation frequencies and excitation_gap (as modal), and the
beam model's first_frame_mode_beam / first_body_mode_beam for the comparison. With suspension linkage also
first_wheel_hop_mode and first_frame_involving_mode (N1's criterion: the lowest elastic mode that is neither a body
mode nor a wheel mode and whose kinetic energy share in the frame, roll bar included, is FRAME_SHARE or more; which
mode it is, its kind and its share in the effects). Fields (on the version's shell
mesh when it is the mesh solved here): per mode of the first FIELD_MODES a mode_shape per_mesh_node (vec3,
largest = 1) and a stress field per_element: the von Mises of the mode's stress over its largest in the mode
(unit 1).
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import units
from ..k0_handcalc.vehicle import DesignError, mass_summary
from ..k1_frame.modal import modes as beam_modes
from ..k1_shell import modal as smodal
from ..k1_shell.mesh import ELEMENT_SIZE_MM, build_mesh
from ..k1_shell.solve import ShellModel
from . import fixtures
from .base import LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, check_record, mesh_of, on_board, \
    param_value, result, run_param
from .frame_shell import same_mesh
from .frame_static import PATH_FORMULA, PATH_PREDICTS, lost_load_outputs
from .modal import FIELD_MODES, FLEX_LIMIT

# 20 mm: the demo frame's shell in a few seconds; 10 mm (the version's mesh) in about a minute (measured on the
# development PC, see the tool's notes in the record's timings_s); finer than 10 mm is not offered (the
# factorisation of K - sigma M grows past the memory budget of the shell tools). HIRAKU's choice.
ELEMENT_SIZE_RANGE_MM = (10.0, 20.0)
ELEMENT_SIZE = Param("element_size", "mm", "target edge of the shell elements (the version's mesh record has the default "
                     f"size; another size is solved but not drawn on it); {ELEMENT_SIZE_RANGE_MM[0]:g} to "
                     f"{ELEMENT_SIZE_RANGE_MM[1]:g} mm", default=ELEMENT_SIZE_MM)
SUSPENSION = Param("suspension", None, "how the wheels hold the frame: spring (each wheel centre rigid (RBE2) on its "
                   "wheel mount, on the beam model's vertical spring, with stiff in-plane holds) or linkage (the arms, "
                   "tie rod, knuckle and wheel, coil spring and tyre of k1_shell.linkage; the vehicle on its tyres)",
                   default="spring", choices=("spring", "linkage"))
TYRE_STIFFNESS = Param("tyre_stiffness", "N/mm", "linkage only: the tyre's vertical rate (without it: the design "
                       "variable tyre_stiffness, else 60 N/mm)")
N_MODES_MAX = 40
# N1's frame-involving mode: at least this share of the mode's kinetic energy in the frame (the shell and the mount
# nodes, the roll bar included). The practitioner's decision for N1 (2026-11-06), as linkage_kinds' frame / body line.
FRAME_SHARE = 0.5
LABEL_SHAPE ="モードの形 (シェルの節の変位、いちばん大きい所を 1)"
LABEL_SHAPE_LINKAGE = ("モードの形 (シェルの節とサスペンションの点 (per_node: 腕・タイロッドの節、車輪の中心と接地点、"
                       "フレームの側の取付点) の変位、全部でいちばん大きい所を 1)")
LABEL_STRAIN = ("モードのひずみ (要素の中心の von Mises 応力 = E x ひずみ、外と内の面の大きい方、そのモードの最大を 1。"
                "大きさは相対で、応力の値ではない)")
MASS_ASSUMPTION = ("質量: シェルの要素は集中質量 (rho t 面積の 1/4 を各節の並進に、その t^2/12 倍を 3 つの回転に。面内の回転にも)。"
                   "部品は梁のモードの模型 (modal) と同じもの・同じ所: 節に載る部品は carried_by の節ごとに等分した点の質量、"
                   "支え (車輪・サスペンション) に載る部品は車輪の中心の節の質量。節の点は、つなぎ目の"
                   "シェルの節の並進に最小二乗の剛体の動きでつなぐ (RBE3 の形: 剛さは足さない)。部品自身の回転の慣性は"
                   "入れていない (梁と同じ)。ガセットと通す形の節の部品はシェルの要素が持つので部品として足さない")
SPRING_ASSUMPTION = ("支え: 車輪の中心の節を、車輪の取付 (frame_node を通る管の壁の ± h) の節に剛な棒 (RBE2) でつなぐ "
                     "(金具と車軸を剛とみる仮定、確かめていない。梁の模型の硬いつなぎと同じ)。サスペンションのばね "
                     "(stiffness_N_per_mm) をその節の上下と地面の間に入れる (梁のモードの模型と同じばね・同じ車輪の中心)。面内は梁と同じく、最初の支えの frame_node の"
                     "点を x と y、x でいちばん離れた支えの点を y で、硬いばね ({k:g} N/mm、ペナルティ、確かめていない) で止める")
EIGEN_ASSUMPTION = ("固有値: scipy の eigsh を shift-invert (sigma = (2 pi {s:g} Hz)^2) で、K - sigma M の分解 (SuperLU) を渡して"
                    "解く。つなぎ目の剛な棒 (RBE2) は K と M の両方に同じ縮約 T をかける。モードは M で正規直交かと残差を確かめた")
KIND_ASSUMPTION = ("モードの分け方は modal と同じ: シェルの節の並進の、最小二乗の剛体の動きからのずれが {f:g} 以上ならフレームが"
                   "曲がるモード (フレームの部材の要素の節だけでも {f:g} 以上なら first_frame_mode、そうでなければロールバーなど)")
LINKAGE_ASSUMPTIONS = (
    "サスペンション (linkage): 下と上の腕 (A 形の 2 本の脚、下の腕はダンパーの下の取付を通す横の管つき) とタイロッドを丸管 "
    "{d:g} x {t:g} mm ({mat}) の梁 (Euler-Bernoulli、脚ごとに {n} 要素) にした。腕の内側の軸はブッシュ (軸に直角 "
    "{kr:g} N/mm、軸方向 {ka:g} N/mm: K2 の値、K2 の仮定) の並進のばねで、軸まわりの回転は自由 (ブッシュのねじりの剛さは K2 に"
    "無いので入れていない)。フレームの側は、取付点を持つ設計の節 (mount_carriers: 点にある節、なければ金具の carried_by、"
    "なければ支えの frame_node) の壁の節に剛な棒 (RBE2) でつないだ金具の節で、取付点はその節に剛につく",
    "ナックル・車輪・ブレーキは 1 つの剛体 (部品の質量と慣性の和、平行軸の項つき) で、腕の外の端とタイロッドの外の端に"
    "ボールジョイント (並進の硬いばね {kj:g} N/mm、ペナルティ、回転は自由) でつなぐ。タイロッドの内の端もボールジョイントで、"
    "棒の方向だけ棒と直列に K2 のタイロッドの剛さ {ktr:g} N/mm になるばね。2 つの球の継手の間の棒は自分の軸まわりに"
    "自由に回るので、その回転だけ内の端でねじりのばね {ks:g} N mm/rad で止めた (ステアリングは止めてある)",
    "ばね: コイルばねを damper_lower から damper_upper への軸方向のばね 1 本にし、その剛さは、車輪の中心の上下の剛さ "
    "(フレームを止め、タイヤを外して計算) が K2 のホイールレート stiffness_N_per_mm になるように決めた (レバー比は "
    "腕の形から計算)。ダンパーは減衰だけで剛さが無いので、減衰の無い固有値の解析には入らない。静的な荷重 (ばねの"
    "予荷重) による幾何剛性は入れていない",
    "タイヤ: 接地点 (車輪の中心から半径だけ下) と地面の間の上下のばね {kt:g} N/mm ({kt_from})。K2 には横と前後の"
    "静的なタイヤの剛さが無い (コーナリングと縦の剛さは滑りに対する力) ので横と前後は自由: 車は前後・横・ヨーに "
    "0 Hz の剛体のモードを持ち、それは数えて分けた (zero_modes)。ほかに支えのばねやペナルティの止めは無い",
    "質量 (linkage): 腕・タイロッドの部品の質量を梁の節に長さの割合で (回転の慣性も小さく入れる)、ボールジョイントは腕の"
    "外の節、ダンパーは半分ずつ両端、ドライブシャフトなど他の部品は半分を剛体、半分をフレーム (支えの frame_node) に",
    "モードの種類 (linkage): 運動エネルギーの割合で分けた。フレーム (シェルと金具の節) が 0.5 以上なら、シェルが"
    "剛体のように動けば車体のモード (上下・ピッチ・ロールは剛体の動きの大きい成分で)、曲がればフレームのモード (0.8 未満は"
    "coupled)。そうでなければ、車輪の剛体が腕より大きければ車輪のモード (前・後の軸、左右が同じ向きなら hop、逆なら tramp)、"
    "腕が大きければ腕の局所のモード",
)


class ModalShell(BuiltinTool):
    name = "modal_shell"
    stage = 3  # shell FE (heavy), after the light stages
    applies_to = ("ladder_4w",)  # the shell mesh is built on a ladder's rails and cross members (as frame_shell)
    title = "固有振動数とモードの形 (シェル: 管の壁の板)"
    domain = "nvh"
    base = "K1"
    tests = ["N1"]
    formulas = ["modal.spring_mass", "modal.quarter_car", "modal.ladder_twist_mode", "modal.simple_beam_first_mode",
                "modal.rotation_excitation", "modal.beam_frame_mode", PATH_FORMULA]
    load_case_params = (
        Param("speed", "km/h", "driving speed for the wheel and motor rotation frequencies "
              "(without it: the design variable max_speed)"),
        Param("n_modes", "1", f"number of modes to compute (1 to {N_MODES_MAX})", default=20.0),
        LOADING,
    )
    run_params = (ELEMENT_SIZE, SUSPENSION, TYRE_STIFFNESS)
    predicts = (
        Predicts("first_body_mode", "modal.quarter_car", "f", "the body bouncing on suspension and tyre springs"),
        Predicts("first_body_mode", "modal.spring_mass", "f", "k = all suspension springs together, m = the sprung mass"),
        Predicts("first_frame_mode", "modal.ladder_twist_mode", "f",
                 "a ladder frame's twist; inputs from hiraku.solvers.k0_handcalc.frame_inputs.ladder_twist_inputs("
                 "design, loading=...)"),
        Predicts("first_frame_mode", "modal.simple_beam_first_mode", "f1",
                 "one rail as a simply supported beam on the axles, without the masses it carries"),
        Predicts("wheel_rotation_frequency", "modal.rotation_excitation", "f_wheel"),
        Predicts("motor_rotation_frequency", "modal.rotation_excitation", "f_motor", "i = the design variable gear_ratio"),
        Predicts("first_frame_involving_mode", "modal.beam_frame_mode", "f",
                 "suspension linkage only: the beam model's first frame mode (k1_frame.modal; no linkage, no roll bar "
                 "share), no hand estimate"),
        PATH_PREDICTS,
    )
    needs_mesh = True

    def known_answers(self) -> list[Record]:
        out = []
        # a cantilever plate strip 500 x 50 x 2 mm of round steel values (test values, no source), 40 x 4 elements
        L, b, t, E, rho = 500.0, 50.0, 2.0, 200000.0, 7.85e-9
        f = cantilever_strip_modes(L, b, t, E, 0.3, rho, 40, 4, 4)
        exact = 1.875104 ** 2 / (2 * math.pi * L * L) * math.sqrt(E * b * t ** 3 / 12.0 / (rho * b * t))
        case = "cantilever plate strip 500 x 50 x 2 mm, E 200 GPa, 7850 kg/m^3, 40 x 4 MITC4, lumped mass"
        out.append(check_record(case, "first_frequency", exact, float(f.frequencies[0]), "Hz", 0.02,
                                "Euler-Bernoulli cantilever: f1 = (1.8751^2 / 2 pi L^2) sqrt(E I / rho A) = "
                                f"{exact:.3f} Hz (the strip's plate stiffening and shear: within 2 %)"))
        exact2 = exact * (4.694091 / 1.875104) ** 2
        f2 = min(f.frequencies, key=lambda x: abs(x - exact2))
        out.append(check_record(case, "second_bending_frequency", exact2, float(f2), "Hz", 0.03,
                                f"Euler-Bernoulli cantilever: f2 = f1 (4.6941 / 1.8751)^2 = {exact2:.3f} Hz (the bending "
                                f"mode nearest it among the first four; within 3 %)"))
        lad = fixtures.ladder_design(payload_kg=400.0)
        lad["materials"]["st"] = dict(lad["materials"]["st"], E_MPa=205000.0 * 1e3, density_kg_m3=1e-3)
        for s in lad["supports"]:
            s["suspension"] = {"type": "spring", "stiffness_N_per_mm": 20.0}
        r = smodal.modes(lad, 4, element_size_mm=20.0)
        want = math.sqrt(4 * 20.0e3 / 400.0) / (2 * math.pi)
        bounce = min(r["frequencies"], key=lambda fi: abs(fi - want))
        out.append(check_record("400 kg on a nearly rigid ladder shell (20 mm), four 20 N/mm springs", "bounce_frequency",
                                want, float(bounce), "Hz", 1e-3, "rigid body bounce: f = (1/2 pi) sqrt(4 k / m)"))
        return out

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params. Run params: mesh (the version's shell mesh record: the fields
        go on it when it is the mesh solved here) and run_params."""
        design = on_board(design, load_case)
        n = int(param_value(load_case, "n_modes", "1"))
        if not 1 <= n <= N_MODES_MAX:
            raise LoadCaseError(f"n_modes must be between 1 and {N_MODES_MAX}")
        v = param_value(load_case, "speed", "km/h")
        if v is None:
            q = design.get("variables", {}).get("max_speed")
            if q is None:
                raise LoadCaseError("give the load case parameter speed (km/h) or the design variable max_speed")
            v = units.to_unit(q, "km/h", "max_speed")
        size = run_param(params, ELEMENT_SIZE)
        lo, hi = ELEMENT_SIZE_RANGE_MM
        if not lo <= size <= hi:
            raise LoadCaseError(f"run parameter element_size must be {lo:g} to {hi:g} mm, got {size:g} mm")
        susp = run_param(params, SUSPENSION)
        tyre = run_param(params, TYRE_STIFFNESS)
        if tyre is not None and susp != "linkage":
            raise LoadCaseError("run parameter tyre_stiffness is for suspension linkage only")
        if tyre is not None and not tyre > 0.0:
            raise LoadCaseError(f"run parameter tyre_stiffness must be > 0, got {tyre:g} N/mm")
        r = smodal.modes(design, n, element_size_mm=size, suspension=susp, tyre_stiffness_N_per_mm=tyre)
        mm, sol = r["model"], r["solve"]
        res = float(np.max(sol.residuals[r["keep"]]))
        if sol.orthonormality > smodal.ORTHO_TOL or res > smodal.RESIDUAL_TOL:
            raise DesignError(f"the shell modes did not pass their checks (M-orthonormality {sol.orthonormality:.1e}, "
                              f"largest residual {res:.1e}); the model is badly held")
        f, flex, lflex = r["frequencies"], r["flexibility"], r["ladder_flexibility"]
        if susp == "linkage":
            nz = len(r["zero_modes"]["frequencies"])
            if nz != 3:
                raise DesignError(f"the vehicle on its tyres has {nz} modes below {smodal.ZERO_HZ:g} Hz, not the 3 "
                                  "rigid ones (fore-aft, sideways, yaw): a mechanism in the linkage or a part not held")
            if len(f) < n:
                raise DesignError(f"only {len(f)} elastic modes were found of the {n} asked")
        n_fields = min(FIELD_MODES, len(f))

        def mode_row(quantity: str, i: int) -> Record:
            row = result(quantity, float(f[i]), "Hz")
            if i < n_fields:
                row["field_index"] = 2 * i  # the mode_shape field; its strain field follows it
            return row

        if susp == "linkage":
            kinds = linkage_kinds(r, design)
        else:
            kinds = [("body on springs" if x < FLEX_LIMIT else "frame" if lx >= FLEX_LIMIT else "roll bar")
                     for x, lx in zip(flex, lflex)]
        results = [mode_row(f"mode_{i + 1}", i) for i in range(len(f))]
        body = [i for i, k in enumerate(kinds) if k.startswith("body")]
        ladder = [i for i, k in enumerate(kinds) if k.startswith("frame")]
        other = [i for i, k in enumerate(kinds) if k.startswith("roll bar")]
        if body:
            results.append(mode_row("first_body_mode", body[0]))
        if not ladder:
            raise DesignError(f"no frame bending mode among the first {n} shell modes; ask for more modes (n_modes)")
        results.append(mode_row("first_frame_mode", ladder[0]))
        if other:
            results.append(mode_row("first_rollbar_mode", other[0]))
        hop = [i for i, k in enumerate(kinds) if k.startswith("wheel hop")]
        if hop:
            results.append(mode_row("first_wheel_hop_mode", hop[0]))
        involving = None
        if susp == "linkage":
            involving = first_frame_involving(kinds, r["energies"])
            if involving is None:
                raise DesignError(f"no frame-involving mode (frame kinetic share {FRAME_SHARE:g} or more, not a body "
                                  f"or wheel mode) among the first {n} modes; ask for more modes (n_modes)")
            results.append(mode_row("first_frame_involving_mode", involving))
        driven =[s for s in design["supports"] if s["actuation"]["drive"]] or design["supports"]
        radius = float(np.mean([s["radius_mm"] for s in driven]))
        f_wheel = (v / 3.6) / (2 * math.pi * radius / 1000.0)
        results.append(result("wheel_rotation_frequency", f_wheel, "Hz"))
        exc = {"wheel": f_wheel}
        gear = design.get("variables", {}).get("gear_ratio")
        if gear is not None:
            exc["motor"] = f_wheel * units.to_unit(gear, "1", "gear_ratio")
            results.append(result("motor_rotation_frequency", exc["motor"], "Hz"))
        frame = [float(f[i]) for i, k in enumerate(kinds) if k.startswith(("frame", "roll bar"))]
        gaps = {k: min(abs(fr - fe) / fe for fr in frame) * 100.0 for k, fe in exc.items() if fe > 0}
        if gaps:
            nearest = min(gaps, key=gaps.get)
            results.append(result("excitation_gap", gaps[nearest], "%", nearest))
        results.append(result("vehicle_mass", mm.total_mass_kg, "kg"))
        results.append(result("frame_mass", mm.shell_mass_kg, "kg"))

        cmp_results, cmp_effects = beam_comparison(design, r, kinds, n)
        results += cmp_results
        effects = mode_effects(r, kinds, n_fields) + cmp_effects + model_effects(mm, sol, r)
        if susp == "linkage":
            effects += linkage_effects(r, kinds)
            effects.append(involving_effect(r, kinds, involving))
        fields = []
        mesh, why = mesh_of(design, params)
        if mesh is not None:
            why = same_mesh({"mesh": mm.mesh}, mesh)
            if why is None:
                fields = mode_fields(r, kinds, n_fields, mesh["mesh_id"])
        if why:
            effects.append({"name": why})
        if not fields:  # no field: the results carry no field_index
            for row in results:
                row.pop("field_index", None)
        if susp == "linkage":
            from ..k1_shell import linkage as lk

            c0 = next(iter(mm.linkage["corners"].values()))
            ARM_D_MM, ARM_T_MM = c0["arm_mm"]  # the first corner's arm tube (arm_parts.arm_section)
            t_from = next(x for x in mm.linkage["notes"] if x.startswith("tyre "))
            s_ = MASS_ASSUMPTION.split("。")  # the shell's lumped mass, the RBE3 node points, the gussets
            assumptions = ["。".join([s_[0], s_[2], s_[4], LINKAGE_ASSUMPTIONS[4]])] + [
                a.format(d=ARM_D_MM, t=ARM_T_MM, mat=mm.linkage["notes"][1], n=lk.N_SEG, kr=c0["bushings"]["radial"],
                         ka=c0["bushings"]["axial"], kj=lk.JOINT_STIFFNESS, ktr=c0["bushings"]["tie_rod"],
                         ks=lk.SPIN_STIFFNESS, kt=mm.linkage["tyre_N_per_mm"], kt_from=t_from.split(" from ", 1)[1])
                for a in LINKAGE_ASSUMPTIONS[:4]] + [LINKAGE_ASSUMPTIONS[5]] + [
                n_ for n_ in mm.linkage["notes"][2:]]
            assumptions += [EIGEN_ASSUMPTION.format(s=smodal.SIGMA_HZ), KIND_ASSUMPTION.format(f=FLEX_LIMIT)]
        else:
            assumptions = [MASS_ASSUMPTION, SPRING_ASSUMPTION.format(k=smodal.HOLD_STIFFNESS),
                           EIGEN_ASSUMPTION.format(s=smodal.SIGMA_HZ), KIND_ASSUMPTION.format(f=FLEX_LIMIT)]
        # the path check reads unsupported_load on the analysis that decides: every part's mass goes on its
        # carried_by nodes or its support's wheel centre (k1_shell.modal; a node no member uses is refused)
        lost, said = lost_load_outputs()
        results.append(lost)
        effects.append(said)
        timings = {f"shell_modal_{k[:-2]}": float(val) for k, val in r["timings"].items() if k.endswith("_s")}
        return {"results": results, "fields": fields, "effects": effects, "assumptions": assumptions,
                "timings_s": timings}


def cantilever_strip_modes(L, b, t, E, nu, rho, nx, ny, n_modes):
    """The lowest modes of a flat strip L x b x t (x along L) clamped at x = 0, nx x ny MITC4 elements, lumped mass."""
    xs, ys = np.linspace(0.0, L, nx + 1), np.linspace(0.0, b, ny + 1)
    nodes = np.array([[x, y, 0.0] for y in ys for x in xs])
    el = np.array([[j * (nx + 1) + i, j * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i]
                   for j in range(ny) for i in range(nx)])
    ne = len(el)
    model = ShellModel(nodes, el, np.full(ne, t), np.full(ne, E), np.full(ne, nu), np.full(ne, rho))
    clamped = np.nonzero(nodes[:, 0] == 0.0)[0]
    fixed = (6 * clamped[:, None] + np.arange(6)[None, :]).ravel()
    return smodal.solve_modes(model.stiffness(), model.mass(), nodes, [], n_modes, fixed)


def beam_comparison(design: Record, r: dict, kinds: list[str], n: int) -> tuple[list[Record], list[Record]]:
    """The beam model's modes (k1_frame.modal.modes, same loading and masses) beside the shell's: its
    first frame and body modes as results, and for each shell mode the beam mode of largest MAC on the design
    nodes' translations (the shell's: the mean of each joint's mesh nodes) as effects."""
    bm = beam_modes(design, n)
    bf, bflex, blflex = bm["frequencies"], bm["flexibility"], bm["ladder_flexibility"]
    bkinds = [("body on springs" if x < FLEX_LIMIT else "frame" if lx >= FLEX_LIMIT else "roll bar")
              for x, lx in zip(bflex, blflex)]
    results = []
    bi = next((i for i, k in enumerate(bkinds) if k == "frame"), None)
    if bi is not None:
        results.append(result("first_frame_mode_beam", float(bf[bi]), "Hz"))
    bb = next((i for i, k in enumerate(bkinds) if k == "body on springs"), None)
    if bb is not None:
        results.append(result("first_body_mode_beam", float(bf[bb]), "Hz"))
    ms = r["model"].mesh
    frame_nodes = list(bm["shapes"][0].keys())
    B = np.array([np.concatenate([s[nid] for nid in frame_nodes]) for s in bm["shapes"]]).T
    S = np.array([smodal.node_mesh_shape(ms, u, frame_nodes).ravel() for u in r["shapes"]]).T
    M = smodal.mac(S, B)  # shell rows, beam columns
    effects = []
    for i in range(len(r["frequencies"])):
        j = int(np.argmax(M[i]))
        fs, fb = float(r["frequencies"][i]), float(bf[j])
        effects.append({"name": (f"shell mode {i + 1} ({kinds[i]}) {fs:.3f} Hz vs beam mode {j + 1} ({bkinds[j]}) "
                                 f"{fb:.3f} Hz, MAC {M[i, j]:.2f} on the design nodes (value: shell / beam - 1)"),
                        "value": fs / fb - 1.0 if fb > 0 else 0.0, "unit": "1"})
    if bi is not None:
        fi = next(i for i, k in enumerate(kinds) if k.startswith("frame"))
        effects.append({"name": (f"first frame mode: shell {r['frequencies'][fi]:.3f} Hz (mode {fi + 1}), beam "
                                 f"{bf[bi]:.3f} Hz (mode {bi + 1}); value: shell / beam - 1"),
                        "value": float(r["frequencies"][fi] / bf[bi] - 1.0), "unit": "1"})
    return results, effects


def mode_effects(r: dict, kinds: list[str], n_fields: int) -> list[Record]:
    return [{"name": f"mode {i + 1} ({kinds[i]}): share of the shape (mesh node translations) that bends the frame",
             "value": float(r["flexibility"][i]), "unit": "1"} for i in range(len(r["frequencies"]))]


def _wheel_motion(r: dict, i: int, sid: str) -> tuple[np.ndarray, np.ndarray]:
    """(translation at the wheel centre, rotation) of support sid's wheel body in mode i (the solver's scale)."""
    from ..k1_shell.linkage import offset_rows

    mm = r["model"]
    info = mm.linkage["corners"][sid]["info"]
    u6 = r["phi"][:, i].reshape(-1, 6)[info.body]
    node, d = mm.linkage["asm"].points[f"{sid}.wheel_center"]
    return offset_rows(d) @ u6, u6[3:]


def linkage_kinds(r: dict, design: Record) -> list[str]:
    """The kind of each elastic mode of the linkage model from its kinetic energy shares (LINKAGE_ASSUMPTIONS)."""
    from ..k1_frame.analyze import rigid_fit

    mm = r["model"]
    X = mm.mesh.nodes
    c = X.mean(axis=0)
    sup = design["supports"]
    pos = {s["id"]: np.asarray(s["position_mm"], float) for s in sup}
    xs = np.array(list(pos.values()))
    half_wb = (float(xs[:, 0].max() - xs[:, 0].min()) / 2.0) or 1.0
    half_tr = (float(xs[:, 1].max() - xs[:, 1].min()) / 2.0) or 1.0
    front = {k for k, p in pos.items() if p[0] > float(xs[:, 0].mean())}
    out = []
    for i, en in enumerate(r["energies"]):
        ke = en["kinetic"]
        fr = ke.get("frame", 0.0)
        wheels = {k.split(":", 1)[1]: v for k, v in ke.items() if k.startswith("wheel:")}
        arms = {k.split(":", 1)[1]: v for k, v in ke.items() if k.startswith("arms:")}
        w_all, a_all = sum(wheels.values()), sum(arms.values())
        if fr >= 0.5:
            if r["flexibility"][i] < FLEX_LIMIT:
                t, th = rigid_fit(list(X - c), list(r["shapes"][i]))
                comps = {"bounce": abs(t[2]), "roll": abs(th[0]) * half_tr, "pitch": abs(th[1]) * half_wb,
                         "fore-aft": abs(t[0]), "sideways": abs(t[1]), "yaw": abs(th[2]) * half_wb}
                k = "body " + max(comps, key=comps.get)
            else:
                k = "frame" if r["ladder_flexibility"][i] >= FLEX_LIMIT else "roll bar"
            if fr < 0.8:
                k += " (coupled with the wheels)" if w_all >= a_all else " (coupled with the arms)"
        elif w_all >= a_all:
            sid = max(wheels, key=wheels.get)
            share_f = sum(v for s, v in wheels.items() if s in front) / (w_all or 1.0)
            axle = "front" if share_f > 0.75 else "rear" if share_f < 0.25 else "both axles"
            t, th = _wheel_motion(r, i, sid)
            radius = mm.linkage["corners"][sid]["info"].radius
            if float(np.linalg.norm(th)) * radius > float(np.linalg.norm(t)):
                ax = int(np.argmax(np.abs(th)))
                k = f"wheel {('camber', 'spin / wind-up', 'steer')[ax]} rotation ({axle})"
            else:
                ax = int(np.argmax(np.abs(t)))
                if ax == 2:
                    mates = [s for s in wheels if (s in front) == (sid in front) and s != sid]
                    phase = ""
                    if mates and wheels[mates[0]] > 0.2 * wheels[sid]:
                        z2 = _wheel_motion(r, i, mates[0])[0][2]
                        phase = ", in phase" if z2 * t[2] > 0 else ", anti-phase (tramp)"
                    k = f"wheel hop ({axle}{phase})"
                else:
                    k = f"wheel {('fore-aft', 'sideways')[ax]} ({axle})"
            if fr >= 0.2:
                k += " (coupled with the frame)"
        else:
            k = f"arm local ({max(arms, key=arms.get)})"
            if fr >= 0.2:
                k += " (coupled with the frame)"
        out.append(k)
    return out


def first_frame_involving(kinds: list[str], energies: list[dict]) -> int | None:
    """N1's frame-involving mode of the linkage model: the index of the lowest elastic mode that is not a body mode
    (bounce / pitch / roll / the rigid sways) nor a wheel mode (hop, tramp, wind-up, steer, camber, ...) and whose
    kinetic energy share in the frame (shell and mount nodes, the roll bar included) is FRAME_SHARE or more; None when
    no mode is. kinds: linkage_kinds; energies: k1_shell.modal's per mode {"kinetic": {"frame": share, ...}}."""
    for i, (k, en) in enumerate(zip(kinds, energies)):
        if k.startswith(("body", "wheel")):
            continue
        if float(en["kinetic"].get("frame", 0.0)) >= FRAME_SHARE:
            return i
    return None


def involving_effect(r: dict, kinds: list[str], i: int) -> Record:
    """Which mode first_frame_involving_mode is: its number, kind, frequency and the frame's kinetic share."""
    share = float(r["energies"][i]["kinetic"].get("frame", 0.0))
    return {"name": (f"first frame-involving mode (N1): mode {i + 1} ({kinds[i]}) {float(r['frequencies'][i]):.3f} Hz, "
                     f"frame kinetic energy share {share:.3f} (at least {FRAME_SHARE:g}; body and wheel modes left out, "
                     f"the roll bar counted) (value: the share)"),
            "value": share, "unit": "1"}


def linkage_effects(r: dict, kinds: list[str]) -> list[Record]:
    """Energy shares per mode, the rigid modes set apart, and each corner's linkage values."""
    out = []
    for i, en in enumerate(r["energies"]):
        ke, se = en["kinetic"], en["strain"]
        wheels = sum(v for k, v in ke.items() if k.startswith("wheel:"))
        arms = sum(v for k, v in ke.items() if k.startswith("arms:"))
        per = ", ".join(f"{k.split(':')[1]} {v:.2f}" for k, v in sorted(ke.items()) if k.startswith("wheel:"))
        strain = ", ".join(f"{k} {v:.3f}" for k, v in se.items())
        out.append({"name": (f"mode {i + 1} ({kinds[i]}) {float(r['frequencies'][i]):.3f} Hz: kinetic energy frame "
                             f"{ke.get('frame', 0.0):.3f}, wheels {wheels:.3f} ({per}), arms {arms:.3f}; strain energy "
                             f"{strain} (value: the frame's kinetic share)"),
                    "value": float(ke.get("frame", 0.0)), "unit": "1"})
    z = r["zero_modes"]
    out.append({"name": (f"{len(z['frequencies'])} rigid modes below the elastic ones (free on the tyres fore-aft, "
                         f"sideways and in yaw), set apart; their frequencies (Hz): "
                         + ", ".join(f"{float(x):.2e}" for x in z["frequencies"])), "value": float(len(z["frequencies"])),
                "unit": "1"})
    for sid, c in r["model"].linkage["corners"].items():
        info = c["info"]
        J = np.diag(info.body_inertia_kg_mm2) / 1e6
        out.append({"name": (f"{sid}: coil spring {info.spring_rate:.3f} N/mm along the damper for the wheel rate "
                             f"{info.wheel_rate:g} N/mm (motion ratio {info.motion_ratio:.4f}, linkage compliance "
                             f"{c['linkage_compliance']:.2e} mm/N); wheel body {info.body_mass_kg:.3f} kg, inertia "
                             f"about its centre of mass x / y / z {J[0]:.4f} / {J[1]:.4f} / {J[2]:.4f} kg m^2 "
                             f"(parts: {', '.join(c['parts'].ids.get('rigid', []))}); tyre {info.tyre_rate:g} N/mm; "
                             f"mounts on frame nodes {c['carriers']} (value: the coil spring rate)"),
                    "value": float(info.spring_rate), "unit": "N/mm"})
    return out


def model_effects(mm, sol, r: dict) -> list[Record]:
    ms = mm.mesh
    beam_frame = mass_summary(mm.design)["frame_mass_kg"]
    tm = mm.translational_mass_kg()
    return [
        {"name": (f"shell model: {ms.n_nodes} nodes, {ms.n_elements} elements (edge {ms.element_size_mm:g} mm, weld "
                  f"strips {'on' if ms.weld_strips else 'off'}), {sol.n_free} free DOF after the rigid links"),
         "value": float(sol.n_free), "unit": "1"},
        {"name": (f"mass of the model {mm.total_mass_kg:.2f} kg: shell elements (rho t area) {mm.shell_mass_kg:.2f} kg "
                  f"(beam model: density A L {beam_frame:.2f} kg), parts {mm.part_mass_kg:.2f} kg; rigid translation "
                  f"x / y / z through M: {tm[0]:.3f} / {tm[1]:.3f} / {tm[2]:.3f} kg"), "value": mm.total_mass_kg,
         "unit": "kg"},
        {"name": ("springs at the wheel centres (N/mm): " if mm.suspension == "spring" else
                  "coil spring rates along the damper axes (N/mm, for K2's wheel rates): ")
                 + ", ".join(f"{k} {v:.4g}" for k, v in mm.springs.items())},
        {"name": "largest |Phi^T M Phi - I| of the shell modes (M-orthonormality)", "value": sol.orthonormality,
         "unit": "1"},
        {"name": "largest relative residual |K phi - omega^2 M phi| / |K phi| of the shell modes",
         "value": float(np.max(sol.residuals)), "unit": "1"},
    ]


def mode_fields(r: dict, kinds: list[str], n_fields: int, mesh_id: str) -> list[Record]:
    """Per mode of the first n_fields: its mode_shape per_mesh_node and its relative strain per_element."""
    out = []
    mm = r["model"]
    for i in range(n_fields):
        fi = float(r["frequencies"][i])
        u = r["shapes"][i]
        row = {"kind": "mode_shape", "label": f"mode {i + 1} ({kinds[i]}): {LABEL_SHAPE}", "unit": "1",
               "frequency_Hz": fi, "mesh_id": mesh_id}
        if "point_shapes" in r:  # linkage: mesh and linkage points on one scale (the largest of them all = 1)
            u = u * (r["mesh_scale"][i] / r["scale"][i])
            row["label"] = f"mode {i + 1} ({kinds[i]}): {LABEL_SHAPE_LINKAGE}"
            row["per_node"] = {k: [round(float(x), 4) + 0.0 for x in v] for k, v in r["point_shapes"][i].items()}
        # the design nodes (where the parts' masses sit, carried_by): the mean of their joint's mesh nodes, on the
        # mesh's scale, so a screen can move each part with its nodes as the model carries its mass (RBE3)
        per = row.setdefault("per_node", {})
        for n, ids in mm.mesh.joints.items():
            if n not in per and len(ids):
                per[n] = [round(float(x), 4) + 0.0 for x in np.asarray(u)[np.asarray(ids, int)].mean(axis=0)]
        row["per_mesh_node"] = [[round(float(x), 4) + 0.0 for x in v] for v in u]
        out.append(row)
        strain = smodal.mode_strain(mm, r["phi"][:, i])
        out.append({"kind": "stress", "label": f"mode {i + 1} ({kinds[i]}): {LABEL_STRAIN}", "unit": "1",
                    "frequency_Hz": fi, "mesh_id": mesh_id, "surface": "max",
                    "per_element": [round(float(x), 4) for x in strain]})
    return out


def make_mesh_record(design: Record, element_size_mm: float = ELEMENT_SIZE_MM) -> Record:
    """The contract mesh record of the shell solved at element_size_mm (as hiraku.solvers.frame_mesh at its
    default size), without mesh_id: for a standalone run at another size."""
    rec = build_mesh(design, element_size_mm).record("mesh-0000", int(design.get("design_version", 1)))
    del rec["mesh_id"]
    return rec
