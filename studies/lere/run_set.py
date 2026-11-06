"""One set: v005 + BASE + the set's variables, at its tyre stiffness.

  python -m studies.lere.run_set NAME --out DIR [--tyre 60] [--no-step] [--no-modal] [--save] [--save-modal]
                                 [--no-plot]
  -> DIR/NAME/result.json (merged with an earlier one there), DIR/NAME/step_LE.png and step_LB.png (contours at the
     instant of each joint's largest hot spot), DIR/NAME/mode_frame.png (the first frame mode's strain);
     --save: DIR/NAME/frames.npz, frames_meta.pkl, load_paths.json; --save-modal: DIR/NAME/modal.npz.

Modal: the shell's first modes (k1_shell.modal, 10 mm elements) on the catalog's N1 load case
(data/requirements/offroad-4w.yaml). Step hit: K2 step_climb_dynamic (record_run speed, 100 mm step, 10 km/h), then
the frame shell at every instant of K2's mount series with mount_path "footprint". Before each shell solve it waits
for 15 GB free memory. Run from the repository root; HIRAKU_LCA_FULL_WALL=1 and HIRAKU_SHELL_WORKERS=0 are set in
main() before hiraku's solvers are imported."""
import argparse
import json
import os
import pickle
import sys
import time


def run_modal(d, summary, name, outdir, save_modal, plot, t0):
    import numpy as np
    from pathlib import Path
    from hiraku.catalog import load_catalog, load_requirements
    from hiraku.catalog.build import build_load_cases
    from hiraku.solvers.shelf.base import on_board
    from hiraku.solvers.k1_shell import modal as smodal
    from hiraku.solvers.shelf.modal import FLEX_LIMIT
    from . import modeinfo
    from .common import REQUIREMENTS, wait_mem
    lc = build_load_cases(load_catalog(), load_requirements(Path(REQUIREMENTS)), d, ["N1"])[0]
    print("modal: free memory", round(wait_mem(), 1), "GB", flush=True)
    r = smodal.modes(on_board(d, lc), 10, element_size_mm=10.0)
    mm, sol = r["model"], r["solve"]
    f, flex, lflex = r["frequencies"], r["flexibility"], r["ladder_flexibility"]
    kinds = [("body" if x < FLEX_LIMIT else "frame" if lx >= FLEX_LIMIT else "roll bar") for x, lx in zip(flex, lflex)]
    ms = mm.mesh
    X, E, mem = ms.nodes, np.asarray(ms.elements), np.asarray(ms.element_member)
    dig, strains = [], []
    for i in range(min(8, len(f))):
        s = smodal.mode_strain(mm, r["phi"][:, i])
        strains.append(s.astype(np.float32))
        g = modeinfo.digest(X, E, mem, r["shapes"][i], s)
        g["groups"] = modeinfo.group_share(mem, X, E, s, modeinfo.GROUPS)
        dig.append(g)
    ff = next((i for i, k in enumerate(kinds) if k == "frame"), None)
    summary["modal"] = {"frequencies": [float(x) for x in f], "kinds": kinds, "flex": [float(x) for x in flex],
                        "ladder_flex": [float(x) for x in lflex],
                        "first_frame_mode": None if ff is None else float(f[ff]),
                        "first_frame_index": None if ff is None else ff + 1, "digest": dig, "ortho": sol.orthonormality,
                        "max_residual": float(np.max(sol.residuals)), "model_mass_kg": mm.total_mass_kg,
                        "load_case": lc.get("load_case_id"), "timings": {k: float(v) for k, v in r["timings"].items()}}
    print("modal", [round(float(x), 3) for x in f], kinds, f"{time.perf_counter() - t0:.0f} s", flush=True)
    if plot and ff is not None and ff < len(strains):
        from . import plot as P
        print(P.mode_strain(name, X, E, mem, strains[ff], ff, float(f[ff]), os.path.join(outdir, "mode_frame.png")),
              flush=True)
    if save_modal:
        np.savez_compressed(os.path.join(outdir, "modal.npz"), nodes=X, elements=E, member=mem,
                            strain=np.stack(strains),
                            shapes=np.stack([np.asarray(u, np.float32) for u in r["shapes"][:len(strains)]]),
                            freq=np.asarray(f), kinds=np.asarray(kinds))
    del r, mm, sol, strains
    run_modal_linkage(on_board(d, lc), summary, lc, t0)


def run_modal_linkage(loaded, summary, lc, t0):
    """The same N1 modes with the real suspension (modal_shell suspension "linkage": arms as beams on K2's bushings,
    knuckle + wheel as a rigid body, the coil spring along the damper axis, the tyre's vertical spring at the
    design's tyre_stiffness). Mode kinds from the energy shares (shelf.modal_shell.linkage_kinds); the first frame
    mode is the first kind starting with "frame"; the first mode with 0.9 or more of its kinetic energy in the frame
    that is not a body mode is given too (the roll bar mode that also bends the frame)."""
    from hiraku.solvers.k1_shell import modal as smodal
    from hiraku.solvers.shelf.modal_shell import linkage_kinds
    from .common import wait_mem
    print("modal linkage: free memory", round(wait_mem(), 1), "GB", flush=True)
    kt = float(loaded["variables"]["tyre_stiffness"]["value"])
    r = smodal.modes(loaded, 20, element_size_mm=10.0, suspension="linkage", tyre_stiffness_N_per_mm=kt)
    f = [float(x) for x in r["frequencies"]]
    kinds = linkage_kinds(r, loaded)
    ff = next((i for i, k in enumerate(kinds) if k.startswith("frame")), None)
    frame_ke = [float(e["kinetic"].get("frame", 0.0)) for e in r["energies"]]
    fk = next((i for i, k in enumerate(kinds) if not k.startswith("body") and frame_ke[i] >= 0.9), None)
    summary["modal_linkage"] = {
        "frequencies": f, "kinds": kinds, "tyre_stiffness": kt, "frame_kinetic_share": frame_ke,
        "first_frame_mode": None if ff is None else f[ff], "first_frame_index": None if ff is None else ff + 1,
        "first_frame_energy_mode": None if fk is None else f[fk], "first_frame_energy_index": None if fk is None else fk + 1,
        "max_residual": float(max(r["solve"].residuals)), "timings": {k: float(v) for k, v in r["timings"].items()}}
    print("modal linkage", [round(x, 3) for x in f[:12]], kinds[:12], f"{time.perf_counter() - t0:.0f} s", flush=True)


def run_step(d, summary, desc, outdir, save, plot, t0):
    import numpy as np
    from hiraku import solvers
    from hiraku.solvers.shelf.base import on_board
    from hiraku.solvers.shelf.frame_static import upstream_mount_series, beam_frames
    from hiraku.solvers.k1_shell.frame import analyze_shell, shared_frame
    from hiraku.solvers.k1_shell.mesh import ELEMENT_SIZE_MM
    from .common import wait_mem

    T = {t.name: t for t in solvers.builtin_tools()}
    mmu = lambda x: {"value": x, "unit": "mm"}  # noqa: E731
    lc_p = {"load_case_id": "P1-run", "test_id": "P1", "domain": "motion", "title": "P1",
            "params": {"step_height": mmu(100.0), "speed": {"value": 10.0, "unit": "km/h"}}}
    k2 = T["step_climb_dynamic"].run(d, lc_p, [{"prediction_id": "pred-0001"}], {"record_run": "speed"})
    print("K2", k2.get("status"), f"{time.perf_counter() - t0:.0f} s", flush=True)
    lc_r = {"load_case_id": "R1-run", "test_id": "R1", "domain": "strength", "title": "R1", "params": {},
            "tolerance_rel": 0.15}
    design = on_board(d, lc_r)
    series = upstream_mount_series({"P1-run": k2}, design)
    lc_id, ana_id, instants = series
    beam_max = beam_frames(design, series)["results"]
    print("shell mesh: free memory", round(wait_mem(), 1), "GB", flush=True)
    fr, _ = shared_frame(design, ELEMENT_SIZE_MM)
    ms = fr.mesh
    E = np.asarray(ms.elements)
    cent = ms.nodes[E].mean(axis=1)
    members = np.asarray(ms.element_member)
    best = {}

    def upd(key, v, t, where=""):
        if key not in best or v > best[key][0]:
            best[key] = (float(v), float(t), where)

    VM, DISP, URAW, TS, HOT, JH = [], [], [], [], [], []
    last_paths = None
    for i, inst in enumerate(instants):
        wait_mem()
        r = analyze_shell(design, frame=fr, mount_loads=inst["mount_loads"], gravity_mps2=inst["gravity_mps2"],
                          inertia_N=inst["inertia_N"], mount_path="footprint")
        t = inst["t_s"]
        hs = r.get("hot_spots") or []
        for h in hs:
            upd(f"hot {h['joint']}", abs(h["value"]), t, h["member"])
        top = r.get("hot_spot_max")
        if top:
            upd("hot max", abs(top["value"]), t, f"{top['joint']} ({top['member']})")
        for bid, v in (r.get("brackets") or {}).items():
            upd(f"R9 plate {bid}", v["plate_bending_utilisation"], t)
            upd(f"R9 weld {bid}", v["weld_utilisation"], t)
        VM.append(np.asarray(r["von_mises"]["max"], np.float32))
        TS.append(float(t))
        pj = {}
        for h in hs:
            if h["joint"] not in pj or abs(h["value"]) > abs(pj[h["joint"]]["value"]):
                pj[h["joint"]] = dict(value=float(h["value"]), member=h["member"],
                                      position_mm=np.asarray(h["position_mm"]).tolist())
        JH.append(pj)
        if save:
            DISP.append(np.asarray(r["displacements"], np.float32))
            URAW.append(np.asarray(r["U"]).reshape(-1, 6)[:, :3].astype(np.float32))
            HOT.append(None if not top else dict(value=float(top["value"]), joint=top["joint"], member=top["member"],
                                                 position_mm=np.asarray(top["position_mm"]).tolist()))
            if last_paths is None or (top and abs(top["value"]) >= best["hot max"][0] - 1e-9):
                last_paths = (t, r["mount_paths"])
        if (i + 1) % 10 == 0:
            print(f"{i + 1}/{len(instants)} t {t:.4f} {time.perf_counter() - t0:.0f} s", flush=True)
    summary.update({"k2_status": k2.get("status"), "instants": len(instants), "beam_max_stress_over_time": beam_max,
                    "n_elements": int(ms.n_elements), "best": best})
    wl = [dict(joint=w_.joint, kind=w_.kind, side=w_.side, place=w_.place, members=list(w_.members),
               p0=w_.p0.tolist(), p1=w_.p1.tolist()) for w_ in ms.weld_lines]
    if plot and VM:
        from . import plot as P
        vm = np.stack(VM)
        for j in ("LE", "LB"):
            print(P.step_joint(j, ms.nodes, E, cent, members, vm, np.asarray(TS), JH, wl, desc,
                               os.path.join(outdir, f"step_{j}.png")), flush=True)
    if save and VM:
        mount_nodes = {sid: np.asarray(ms.joints[fr.support_node[sid]]).tolist() for sid in fr.support_node}
        np.savez_compressed(os.path.join(outdir, "frames.npz"), nodes=ms.nodes, elements=E, member=members,
                            centres=cent, excluded=fr.excluded, t_s=np.asarray(TS), vm_max=np.stack(VM),
                            disp=np.stack(DISP), u_raw=np.stack(URAW))
        with open(os.path.join(outdir, "frames_meta.pkl"), "wb") as fh:
            pickle.dump(dict(hot=HOT, joint_hot=JH, weld_lines=wl, mount_nodes=mount_nodes,
                             support_node=dict(fr.support_node), hold_nodes=list(map(int, np.ravel(fr.hold_nodes))),
                             joint_kinds=dict(ms.joint_kinds), element_size=ms.element_size_mm,
                             k2_status=k2.get("status"), lc=lc_id, ana=ana_id, mount_path="footprint", design=desc),
                        fh)
        if last_paths is not None:
            t_lp, paths = last_paths
            lp = {"design": desc, "mount_path": "footprint", "forces_at_t_s": t_lp, "mounts": [
                {"support": q["support"], "mount": q["mount"], "hard_point_mm": q["point_mm"].round(2).tolist(),
                 "applied_on": q["how"], "force_N": q["force_N"].round(2).tolist(),
                 "nodes": [int(n) for n in q["nodes"]], "nodes_xyz_mm": ms.nodes[q["nodes"]].round(2).tolist(),
                 "nodal_force_N": q["nodal_N"].round(4).tolist()} for q in paths]}
            with open(os.path.join(outdir, "load_paths.json"), "w", encoding="utf-8") as fh:
                json.dump(lp, fh, indent=1)


def _dump(summary, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, default=float)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="Run one set of the LE/RE + N1 study.")
    ap.add_argument("name")
    ap.add_argument("--out", required=True, help="output folder; the set writes DIR/NAME/")
    ap.add_argument("--tyre", type=float, default=None, help="tyre stiffness N/mm (default: the set's)")
    for flag in ("--no-step", "--no-modal", "--save", "--save-modal", "--no-plot"):
        ap.add_argument(flag, action="store_true")
    a = ap.parse_args()

    from .common import setup_env
    setup_env()
    from .common import regen, mass, spare
    from .plot import have_matplotlib
    from .sets import SETS, variables
    if a.name not in SETS:
        sys.exit(f"unknown set {a.name}; python -m studies.lere.runq --list")
    s = SETS[a.name]
    tyre = s["tyre"] if a.tyre is None else a.tyre
    VARS = variables(a.name)
    outdir = os.path.join(a.out, a.name)
    os.makedirs(outdir, exist_ok=True)
    res_path = os.path.join(outdir, "result.json")
    summary = {}
    if os.path.exists(res_path):
        with open(res_path, encoding="utf-8") as f:
            summary = json.load(f)
    plot = not a.no_plot and have_matplotlib()
    if not a.no_plot and not plot:
        print("matplotlib not installed: no images", flush=True)
    t0 = time.perf_counter()
    d = regen(VARS)
    d["variables"]["tyre_stiffness"]["value"] = tyre
    summary.update({"name": a.name, "variables": VARS, "extra": s["extra"], "tyre_stiffness": tyre,
                    "mass_kg": mass(d), "post_spare_mm": spare(d), "doc": s["doc"]})
    print(a.name, f"mass {summary['mass_kg']:.2f} kg", "post spare", summary["post_spare_mm"],
          f"{time.perf_counter() - t0:.0f} s", flush=True)
    _dump(summary, res_path)
    desc = "v005 + " + (", ".join(f"{k} {v:g}" for k, v in VARS.items()) + f", tyre {tyre:g} N/mm")
    if not a.no_modal:
        run_modal(d, summary, a.name, outdir, a.save_modal, plot, t0)
        _dump(summary, res_path)
    if not a.no_step:
        run_step(d, summary, desc, outdir, a.save, plot, t0)
        _dump(summary, res_path)
    print("done", f"{time.perf_counter() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
