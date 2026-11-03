"""A design's frame as an fe shell model: natural modes next to the beam model's, and a frame torsion test.

The model (frame_model):
- the k1_shell mesh of the tube walls (k1_shell.mesh.build_mesh, MITC4 on the core through
  shell.from_shell_model, so the tied joints keep their rigid links);
- each support gets a wheel node at its position_mm, joined rigidly (RBE2) to the wall nodes of its wheel
  mount (mesh.mounts, or the joint nodes where no tube passes through) and to a point at its frame node.
  This stands for the bracket and the stub axle; the beam model (k1_frame.model.build) joins the wheel
  node to the frame node by a stiff link in the same way. That the bracket is rigid over the mount length
  is an assumption, not checked;
- masses: the shell's own (consistent or lumped element mass); parts carried by frame nodes as point masses,
  each node's share (the part's mass over its carried_by nodes, as k1_frame.modal.add_masses) spread
  equally over that node's joint nodes; parts carried by a support (wheel, suspension) on the wheel node;
  parts the shell elements already carry (k1_shell.frame.shell_parts: gussets, through parts) are left out;
  parts carried by members are the tubes' own mass, which the shell elements carry. Part rotational
  inertia is not included (nor in the beam model).
Supports of the modal model are those of k1_frame.modal.modes: the suspension's vertical spring
(stiffness_N_per_mm) at each wheel node to the ground, or a vertical hold for a rigid support, and the
in-plane holds (x and y at the first support, y at the one farthest from it in x) at the frame-node points.

Torsion test (frame_torsion): no springs; the wheel node of one support is lifted by `lift_mm`, the other
three wheel nodes are held vertically, the in-plane holds as above. The reaction R at the lifted wheel and
the twist give the torsional stiffness
    T = R * track,   theta = atan(lift / track),   k = T / theta  (N*m/deg)
with track the lateral distance between the lifted wheel and the other wheel of its axle. This is the
usual one-wheel-lift frame test; the formula is the plain lever arithmetic.
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc.vehicle import mass_summary, node_table
from ..k1_shell.frame import shell_parts
from ..k1_shell.mesh import ELEMENT_SIZE_MM, build_mesh
from ..k1_shell.solve import ShellModel
from . import eigen
from .connect import Spring, rigid_link
from .model import FEModel, StaticSolver
from .shell import from_shell_model


def _hold_pair(sup: list[dict]) -> tuple[str, str]:
    """The supports of the in-plane holds, chosen as k1_frame.model.restrain_on_wheels does."""
    pts = {s["id"]: np.asarray(s["position_mm"], float) for s in sup}
    a = sup[0]["id"]
    b = max(pts, key=lambda k: abs(pts[k][0] - pts[a][0]))
    return a, b


def frame_model(design: dict, element_size_mm: float = ELEMENT_SIZE_MM, springs: bool = True,
                masses: bool = True) -> dict:
    """The fe shell model of the frame (see the module docstring). Returns {"model", "group", "mesh",
    "wheel" {support: node}, "frame_point" {support: node}, "joint_nodes" {design node: mesh nodes},
    "part_mass_kg", "shell_mass_kg"}."""
    ms = build_mesh(design, element_size_mm)
    sm = ShellModel.from_mesh(ms, design)
    model, g = from_shell_model(sm)
    xyz = node_table(design)
    wheel, point = {}, {}
    for s in design["supports"]:
        fn = s["frame_node"]
        w = model.add_node(np.asarray(s["position_mm"], float))
        p = model.add_node(xyz[fn])
        mount = ms.mounts.get(fn, ms.joints[fn])
        model.add(rigid_link(w, list(mount) + [p], model.nodes, name=f"mount {s['id']}"))
        wheel[s["id"]], point[s["id"]] = w, p
    part_mass = 0.0
    if masses:
        skip = shell_parts(design, ms)
        for it in mass_summary(design)["items"]:
            if it.carrier == "member" or it.id.split(":", 1)[-1] in skip:
                continue
            m_t = it.mass_kg / 1000.0
            if it.carrier == "support":
                n = wheel[it.carried_by[0]]
                model.point_masses[n] = model.point_masses.get(n, 0.0) + m_t
            else:
                share = m_t / len(it.carried_by)
                for fn in it.carried_by:
                    jn = ms.joints[fn]
                    for n in jn:
                        model.point_masses[int(n)] = model.point_masses.get(int(n), 0.0) + share / len(jn)
            part_mass += it.mass_kg
    if springs:
        for s in design["supports"]:
            susp = s.get("suspension", {})
            k = susp.get("stiffness_N_per_mm")
            if susp.get("type") in ("spring", "kc_table") and k:
                model.add(Spring([[wheel[s["id"]], -1]], (0.0, 0.0, float(k), 0.0, 0.0, 0.0), name=f"spring {s['id']}"))
            else:
                model.fix(wheel[s["id"]], [2])
    a, b = _hold_pair(design["supports"])
    model.fix(point[a], [0, 1])
    model.fix(point[b], [1])
    joint_nodes = {n: np.asarray(v, int) for n, v in ms.joints.items()}
    return {"model": model, "group": g, "mesh": ms, "wheel": wheel, "frame_point": point, "joint_nodes": joint_nodes,
            "part_mass_kg": part_mass, "shell_mass_kg": float(g.mass_total(model.nodes) * 1000.0)}


def node_shapes(fmod: dict, U: np.ndarray, frame_nodes: list[str]) -> np.ndarray:
    """(n_frame_nodes, 3) mean translation of each design node's joint nodes in a DOF vector U."""
    lay = fmod["model"].layout()
    u = lay.node_array(U)[:, :3]
    return np.array([u[fmod["joint_nodes"][n]].mean(axis=0) for n in frame_nodes])


def frame_modes(design: dict, n_modes: int = 8, element_size_mm: float = ELEMENT_SIZE_MM, lumped: bool = False,
                beam: dict | None = None) -> dict:
    """Shell modes of the frame and the beam model's (k1_frame.modal.modes), paired by the MAC of the
    frame-node translations. Returns frequencies of both, the MAC matrix (beam rows, shell columns), and for
    each beam mode the shell mode that matches it best."""
    from ..k1_frame.modal import modes as beam_modes

    fmod = frame_model(design, element_size_mm)
    r = eigen.modes(fmod["model"], n_modes, lumped=lumped)
    bm = beam if beam is not None else beam_modes(design, n_modes)
    frame_nodes = list(bm["shapes"][0].keys())
    B = np.array([np.concatenate([s[n] for n in frame_nodes]) for s in bm["shapes"]]).T
    S = np.array([node_shapes(fmod, r.shapes[:, i], frame_nodes).ravel() for i in range(len(r.frequencies))]).T
    mac = eigen.mac(B, S)
    best = [int(np.argmax(mac[i])) for i in range(mac.shape[0])]
    return {"shell_hz": r.frequencies, "beam_hz": np.asarray(bm["frequencies"]), "mac": mac, "best": best,
            "beam_flexibility": np.asarray(bm["flexibility"]), "n_dof": int(r.info["n_reduced"]),
            "shell_mass_kg": fmod["shell_mass_kg"], "part_mass_kg": fmod["part_mass_kg"],
            "model_mass_kg": fmod["model"].total_mass() * 1000.0, "frame_nodes": frame_nodes}


def frame_torsion(design: dict, lift_support: str | None = None, lift_mm: float = 1.0,
                  element_size_mm: float = ELEMENT_SIZE_MM) -> dict:
    """One-wheel-lift torsion test of the frame shell (see the module docstring)."""
    fmod = frame_model(design, element_size_mm, springs=False, masses=False)
    model, wheel = fmod["model"], fmod["wheel"]
    sup = {s["id"]: s for s in design["supports"]}
    lift = lift_support or design["supports"][0]["id"]
    x0 = np.asarray(sup[lift]["position_mm"], float)
    mate = min((k for k in sup if k != lift),
               key=lambda k: abs(sup[k]["position_mm"][0] - x0[0]) - abs(sup[k]["position_mm"][1] - x0[1]))
    track = abs(float(sup[lift]["position_mm"][1]) - float(sup[mate]["position_mm"][1]))
    for k, n in wheel.items():
        model.prescribe(n, 2, lift_mm if k == lift else 0.0)
    sol = StaticSolver(model).solve(np.zeros((len(model.nodes), 6)))
    reac = sol.reactions()
    R = {k: float(reac[n, 2]) for k, n in wheel.items()}
    theta = math.degrees(math.atan2(lift_mm, track))
    T = R[lift] * track / 1000.0  # N*m
    return {"stiffness_Nm_per_deg": T / theta, "torque_Nm": T, "twist_deg": theta, "reactions_N": R,
            "track_mm": track, "lifted": lift, "mate": mate, "n_dof": int(sol.info["n_reduced"]),
            "sum_reactions_N": float(sum(R.values()))}


def beam_torsion(design: dict, lift_support: str | None = None, lift_mm: float = 1.0) -> dict:
    """The same one-wheel-lift test on the beam model (k1_frame.model.build, PyNite): wheel nodes held
    vertically, the lifted one moved by lift_mm (PyNite's enforced displacement), the in-plane holds at the
    frame nodes as in frame_torsion."""
    from ..k1_frame.model import build

    fm = build(design)
    sup = design["supports"]
    for s in sup:
        fm.restrain(fm.wheel_nodes[s["id"]], "z")
    a, b = _hold_pair(sup)
    by_id = {s["id"]: s for s in sup}
    fm.restrain(by_id[a]["frame_node"], "xy")
    fm.restrain(by_id[b]["frame_node"], "y")
    lift = lift_support or sup[0]["id"]
    fm.model.def_node_disp(fm.wheel_nodes[lift], "DY", lift_mm)  # PyNite Y is the design's z
    fm.solve()
    R = {s["id"]: float(fm.reaction(fm.wheel_nodes[s["id"]])[0][2]) for s in sup}
    x0 = np.asarray(by_id[lift]["position_mm"], float)
    mate = min((k for k in by_id if k != lift),
               key=lambda k: abs(by_id[k]["position_mm"][0] - x0[0]) - abs(by_id[k]["position_mm"][1] - x0[1]))
    track = abs(float(x0[1]) - float(by_id[mate]["position_mm"][1]))
    theta = math.degrees(math.atan2(lift_mm, track))
    T = R[lift] * track / 1000.0
    return {"stiffness_Nm_per_deg": T / theta, "torque_Nm": T, "twist_deg": theta, "reactions_N": R,
            "track_mm": track}


__all__ = ["beam_torsion", "frame_model", "frame_modes", "frame_torsion", "node_shapes"]
