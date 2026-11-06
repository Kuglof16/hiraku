"""modal_shell (N1): natural frequencies and mode shapes of the frame on its springs, on the shell mesh of the
tube walls (k1_shell.modal), next to the beam model's (modal, k1_frame.modal) and the wheel and motor rates.

A tool of its own rather than a run option of modal: modal is a light (stage 1) check run on every design
version, the shell's factorisation is heavy (stage 3, as frame_shell beside frame_static), and the two are
judged on the same criterion (first_frame_mode), so the stages decide when the heavier one is run.

Results: mode_<i> (Hz, field_index for the modes with fields), first_body_mode, first_frame_mode (the
first mode that bends the frame itself, FLEX_LIMIT on the mesh nodes of the frame's elements, as modal on its
nodes), first_rollbar_mode, the wheel and motor rotation frequencies and excitation_gap (as modal), and the
beam model's first_frame_mode_beam / first_body_mode_beam for the comparison. Fields (on the version's shell
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
from .modal import FIELD_MODES, FLEX_LIMIT

# 20 mm: the demo frame's shell in a few seconds; 10 mm (the version's mesh) in about a minute (measured on the
# development PC, see the tool's notes in the record's timings_s); finer than 10 mm is not offered (the
# factorisation of K - sigma M grows past the memory budget of the shell tools). HIRAKU's choice.
ELEMENT_SIZE_RANGE_MM = (10.0, 20.0)
ELEMENT_SIZE = Param("element_size", "mm", "target edge of the shell elements (the version's mesh record has the default "
                     f"size; another size is solved but not drawn on it); {ELEMENT_SIZE_RANGE_MM[0]:g} to "
                     f"{ELEMENT_SIZE_RANGE_MM[1]:g} mm", default=ELEMENT_SIZE_MM)
N_MODES_MAX = 40
LABEL_SHAPE = "モードの形 (シェルの節の変位、いちばん大きい所を 1)"
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


class ModalShell(BuiltinTool):
    name = "modal_shell"
    stage = 3  # shell FE (heavy), after the light stages
    applies_to = ("ladder_4w",)  # the shell mesh is built on a ladder's rails and cross members (as frame_shell)
    title = "固有振動数とモードの形 (シェル: 管の壁の板)"
    domain = "nvh"
    base = "K1"
    tests = ["N1"]
    formulas = ["modal.spring_mass", "modal.quarter_car", "modal.ladder_twist_mode", "modal.simple_beam_first_mode",
                "modal.rotation_excitation"]
    load_case_params = (
        Param("speed", "km/h", "driving speed for the wheel and motor rotation frequencies "
              "(without it: the design variable max_speed)"),
        Param("n_modes", "1", f"number of modes to compute (1 to {N_MODES_MAX})", default=20.0),
        LOADING,
    )
    run_params = (ELEMENT_SIZE,)
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
        r = smodal.modes(design, n, element_size_mm=size)
        mm, sol = r["model"], r["solve"]
        if sol.orthonormality > smodal.ORTHO_TOL or float(np.max(sol.residuals)) > smodal.RESIDUAL_TOL:
            raise DesignError(f"the shell modes did not pass their checks (M-orthonormality {sol.orthonormality:.1e}, "
                              f"largest residual {float(np.max(sol.residuals)):.1e}); the model is badly held")
        f, flex, lflex = r["frequencies"], r["flexibility"], r["ladder_flexibility"]
        n_fields = min(FIELD_MODES, len(f))

        def mode_row(quantity: str, i: int) -> Record:
            row = result(quantity, float(f[i]), "Hz")
            if i < n_fields:
                row["field_index"] = 2 * i  # the mode_shape field; its strain field follows it
            return row

        kinds = [("body on springs" if x < FLEX_LIMIT else "frame" if lx >= FLEX_LIMIT else "roll bar")
                 for x, lx in zip(flex, lflex)]
        results = [mode_row(f"mode_{i + 1}", i) for i in range(len(f))]
        body = [i for i, k in enumerate(kinds) if k == "body on springs"]
        ladder = [i for i, k in enumerate(kinds) if k == "frame"]
        other = [i for i, k in enumerate(kinds) if k == "roll bar"]
        if body:
            results.append(mode_row("first_body_mode", body[0]))
        if not ladder:
            raise DesignError(f"no frame bending mode among the first {n} shell modes; ask for more modes (n_modes)")
        results.append(mode_row("first_frame_mode", ladder[0]))
        if other:
            results.append(mode_row("first_rollbar_mode", other[0]))
        driven = [s for s in design["supports"] if s["actuation"]["drive"]] or design["supports"]
        radius = float(np.mean([s["radius_mm"] for s in driven]))
        f_wheel = (v / 3.6) / (2 * math.pi * radius / 1000.0)
        results.append(result("wheel_rotation_frequency", f_wheel, "Hz"))
        exc = {"wheel": f_wheel}
        gear = design.get("variables", {}).get("gear_ratio")
        if gear is not None:
            exc["motor"] = f_wheel * units.to_unit(gear, "1", "gear_ratio")
            results.append(result("motor_rotation_frequency", exc["motor"], "Hz"))
        frame = [float(fi) for fi, x in zip(f, flex) if x >= FLEX_LIMIT]
        gaps = {k: min(abs(fr - fe) / fe for fr in frame) * 100.0 for k, fe in exc.items() if fe > 0}
        if gaps:
            nearest = min(gaps, key=gaps.get)
            results.append(result("excitation_gap", gaps[nearest], "%", nearest))
        results.append(result("vehicle_mass", mm.total_mass_kg, "kg"))
        results.append(result("frame_mass", mm.shell_mass_kg, "kg"))

        cmp_results, cmp_effects = beam_comparison(design, r, kinds, n)
        results += cmp_results
        effects = mode_effects(r, kinds, n_fields) + cmp_effects + model_effects(mm, sol, r)
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
        assumptions = [MASS_ASSUMPTION, SPRING_ASSUMPTION.format(k=smodal.HOLD_STIFFNESS),
                       EIGEN_ASSUMPTION.format(s=smodal.SIGMA_HZ), KIND_ASSUMPTION.format(f=FLEX_LIMIT)]
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
        fi = next(i for i, k in enumerate(kinds) if k == "frame")
        effects.append({"name": (f"first frame mode: shell {r['frequencies'][fi]:.3f} Hz (mode {fi + 1}), beam "
                                 f"{bf[bi]:.3f} Hz (mode {bi + 1}); value: shell / beam - 1"),
                        "value": float(r["frequencies"][fi] / bf[bi] - 1.0), "unit": "1"})
    return results, effects


def mode_effects(r: dict, kinds: list[str], n_fields: int) -> list[Record]:
    return [{"name": f"mode {i + 1} ({kinds[i]}): share of the shape (mesh node translations) that bends the frame",
             "value": float(r["flexibility"][i]), "unit": "1"} for i in range(len(r["frequencies"]))]


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
        {"name": "springs at the wheel centres (N/mm): " + ", ".join(f"{k} {v:g}" for k, v in mm.springs.items())},
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
        out.append({"kind": "mode_shape", "label": f"mode {i + 1} ({kinds[i]}): {LABEL_SHAPE}", "unit": "1",
                    "frequency_Hz": fi, "mesh_id": mesh_id,
                    "per_mesh_node": [[round(float(x), 4) + 0.0 for x in u] for u in r["shapes"][i]]})
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
