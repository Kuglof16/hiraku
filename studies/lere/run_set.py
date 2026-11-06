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


POST_MEMBERS = ("tower_post_LB", "tower_post_RB", "rollbar_foot_L", "rollbar_foot_R")
POST_FOOT_MM = 70.0  # elements of a post within this of its foot node (half the 80 mm rail and 30 mm of the post)


def post_foot_elements(d, ms):
    """{member: element indices} of the post bases (POST_MEMBERS: the rear tower posts and the roll bar feet): the
    member's elements within POST_FOOT_MM of its lower end node. Their joints are tied (no weld strip), so they
    have no IIW hot spot; the base is read as the largest element von Mises there."""
    import numpy as np
    xyz = {n["id"]: np.asarray(n.get("position_mm", n.get("xyz_mm")), float) for n in d["nodes"]}
    mem = np.asarray(ms.element_member)
    cent = ms.nodes[np.asarray(ms.elements)].mean(axis=1)
    out = {}
    for m in d["members"]:
        if m["id"] not in POST_MEMBERS:
            continue
        foot = min((m["node_a"], m["node_b"]), key=lambda n: xyz[n][2])
        els = np.nonzero(mem == m["id"])[0]
        els = els[np.linalg.norm(cent[els] - xyz[foot], axis=1) <= POST_FOOT_MM]
        if len(els):
            out[m["id"]] = els
    return out


def run_step(d, summary, desc, outdir, save, plot, t0, adds=None, h=None):
    import numpy as np
    from hiraku import solvers
    from hiraku.solvers.shelf.base import on_board
    from hiraku.solvers.shelf.frame_static import upstream_mount_series, beam_frames
    from hiraku.solvers.k1_shell.frame import analyze_shell, shared_frame
    from hiraku.solvers.k1_shell.mesh import ELEMENT_SIZE_MM
    from .common import wait_mem
    h = ELEMENT_SIZE_MM if h is None else float(h)

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
    whole = adds is not None
    if whole and adds:
        from hiraku.solvers.k1_shell.whole import build_whole
        tb = time.perf_counter()
        fr = build_whole(design, adds, h)
        summary["whole_notes"] = list(fr.notes)
        summary["corners"] = {sid: {k: float(c[k]) for k in ("motion_ratio", "spring_rate", "wheel_rate",
                                                             "tyre_N_per_mm")} for sid, c in fr.corners.items()}
        summary["mount_ties"] = {f"{a.sid}.{a.mount}": f"{a.kind} on {a.where} ({len(a.nodes)} nodes)"
                                 for a in fr.attach.values()}
        print("whole model", list(adds), f"built {time.perf_counter() - tb:.0f} s", fr.notes, flush=True)
    else:
        fr, _ = shared_frame(design, h)
    ms = fr.mesh
    E = np.asarray(ms.elements)
    cent = ms.nodes[E].mean(axis=1)
    members = np.asarray(ms.element_member)
    feet = post_foot_elements(design, ms)
    best = {}

    def upd(key, v, t, where=""):
        if key not in best or v > best[key][0]:
            best[key] = (float(v), float(t), where)

    VM, DISP, URAW, TS, HOT, JH = [], [], [], [], [], []
    LM = []  # whole model with arms: the linkage's mount forces at each instant
    last_paths = None
    model_mass = None
    for i, inst in enumerate(instants):
        wait_mem()
        kw = {"model": "whole", "adds": list(adds)} if whole else {}
        r = analyze_shell(design, frame=fr, mount_loads=inst["mount_loads"], gravity_mps2=inst["gravity_mps2"],
                          inertia_N=inst["inertia_N"], mount_path="footprint", **kw)
        t = inst["t_s"]
        model_mass = float(r["total_mass_kg"])
        if r.get("linkage_mounts"):
            LM.append({sid: {m: np.asarray(f, float) for m, f in ms_.items()} for sid, ms_ in r["linkage_mounts"].items()})
        for pm, els in feet.items():
            k = int(els[np.argmax(r["von_mises"]["max"][els])])
            upd(f"post {pm}", r["von_mises"]["max"][k], t, f"element {k}")
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
            URAW.append(np.asarray(r["U"]).reshape(-1, 6)[:ms.n_nodes, :3].astype(np.float32))
            HOT.append(None if not top else dict(value=float(top["value"]), joint=top["joint"], member=top["member"],
                                                 position_mm=np.asarray(top["position_mm"]).tolist()))
            if last_paths is None or (top and abs(top["value"]) >= best["hot max"][0] - 1e-9):
                last_paths = (t, r["mount_paths"])
        if (i + 1) % 10 == 0:
            print(f"{i + 1}/{len(instants)} t {t:.4f} {time.perf_counter() - t0:.0f} s", flush=True)
    summary.update({"k2_status": k2.get("status"), "instants": len(instants), "beam_max_stress_over_time": beam_max,
                    "n_elements": int(ms.n_elements), "best": best, "element_size_mm": h,
                    "model_mass_kg": model_mass})
    if LM:
        summary["linkage_check"] = linkage_check(instants, LM, best)
        for key, c in summary["linkage_check"].items():
            print("linkage vs K2 at", key, f"t {c['t_s']}", "worst", c["worst"], "damper tops",
                  c["damper_upper_worst_rel"], flush=True)
            for mk, row_ in c["mounts"].items():
                print(f"  {mk:18s} K2 {row_['k2_N']} linkage {row_['linkage_N']} rel {row_['rel']}", flush=True)
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


def linkage_check(instants, LM, best):
    """The linkage's mount forces against K2's mount series at the instants of the largest LE / RE / any hot spot
    and of the largest K2 mount force: per mount the two vectors (N, body axes) and |linkage - K2| / |K2|; the
    damper tops (the coil spring: the lever ratio) and the worst mount given apart."""
    import numpy as np
    tk = [inst["t_s"] for inst in instants]
    peak = int(np.argmax([max(np.linalg.norm(v[2]) for ml in inst["mount_loads"].values() for v in ml["vectors"])
                          for inst in instants]))
    picks = {k: tk.index(best[k][1]) for k in ("hot LE", "hot RE", "hot max") if k in best and best[k][1] in tk}
    picks["largest K2 mount force"] = peak
    out = {}
    for key, i in picks.items():
        rows, worst, damper = {}, (0.0, ""), 0.0
        for sid, ml in instants[i]["mount_loads"].items():
            for m, _, f in ml["vectors"]:
                g = LM[i].get(sid, {}).get(m, np.zeros(3))
                rel = float(np.linalg.norm(g - f) / max(np.linalg.norm(f), 1e-9))
                rows[f"{sid}.{m}"] = {"k2_N": np.round(f, 1).tolist(), "linkage_N": np.round(g, 1).tolist(),
                                      "rel": round(rel, 4)}
                if rel > worst[0]:
                    worst = (round(rel, 4), f"{sid}.{m}")
                if m == "damper_upper":
                    damper = max(damper, rel)
        out[key] = {"t_s": tk[i], "worst": worst, "damper_upper_worst_rel": round(damper, 4), "mounts": rows}
    return out


def _dump(summary, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o))
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="Run one set of the LE/RE + N1 study.")
    ap.add_argument("name")
    ap.add_argument("--out", required=True, help="output folder; the set writes DIR/NAME/")
    ap.add_argument("--tyre", type=float, default=None, help="tyre stiffness N/mm (default: the set's)")
    ap.add_argument("--h", type=float, default=None, help="shell element size mm (default k1_shell's, 10)")
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
    adds = s.get("adds")
    summary.update({"name": a.name, "variables": VARS, "extra": s["extra"], "tyre_stiffness": tyre,
                    "mass_kg": mass(d), "post_spare_mm": spare(d), "doc": s["doc"]})
    if adds is not None:
        summary["adds"] = list(adds)
    print(a.name, f"mass {summary['mass_kg']:.2f} kg", "post spare", summary["post_spare_mm"],
          f"{time.perf_counter() - t0:.0f} s", flush=True)
    _dump(summary, res_path)
    desc = "v005 + " + (", ".join(f"{k} {v:g}" for k, v in VARS.items()) + f", tyre {tyre:g} N/mm")
    if adds is not None:
        desc += ", whole model" + (" + " + ", ".join(adds) if adds else " (no additions)")
    if not a.no_modal and adds is None:  # the whole-vehicle sets are step hit only
        run_modal(d, summary, a.name, outdir, a.save_modal, plot, t0)
        _dump(summary, res_path)
    if not a.no_step:
        run_step(d, summary, desc, outdir, a.save, plot, t0, adds=adds, h=a.h)
        _dump(summary, res_path)
    print("done", f"{time.perf_counter() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
