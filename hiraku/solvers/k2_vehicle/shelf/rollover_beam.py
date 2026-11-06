"""R4 judged by a rollover: the tilt table past its limit to the roll bar's first touch (K2's rigid body with its
ground contact, rollover.tilt_run), then the design's frame as beams (crash_beam) landing on that ground plane in
the body's state at the touch, to rest or a time limit.

The rigid phase is rollover_tilt's run (scenario.tilt_sweep to the tip, then hold_past further and held while the
vehicle falls off the table onto the floor it is hinged on); it stops at the state the step of the roll bar's first
contact force started from. The beam phase starts from that state:

- the frame in the design frame (the K2 body's axes are the design's: a design point x is at p + R (x - cg_s) in the
  road frame), the plane the touching point went into turned into the design frame (normal n_b = R^T n, the frame
  moving into it along -n_b), placed at the frame node furthest along -n_b (no frame node starts inside the ground;
  the record says how far past the touching point that is), gravity R^T g on every node;
- the velocity of the frame as a rigid body (v(x) = u + omega x (x - c_m), c_m the mesh's mass centre), chosen so
  that the mesh's linear momentum and its angular momentum about K2's whole-vehicle mass centre equal K2's (the
  sprung body and the unsprung point masses at the wheel centres, their wheel travel rates included): handoff().
  The mesh's mass equals K2's whole mass on a design whose parts all go on nodes, but it puts each part's mass on its
  carried_by nodes, so its mass centre and inertia differ from K2's (the record gives both, and the turn rate the
  mesh takes against K2's).

Measured (crash_beam.measure.room_to_boxes, in the frame of the frame's own nodes off the roll bar): the least room
over the landing between every frame member (each a capsule of k4_geometry.rollbar.member_radius, the radius R4's
criterion uses) and the occupants' space of k4_geometry.rollbar.occupant_spaces (moving with the occupant's
carried_by nodes): rops_occupant_clearance, R4's criterion quantity; the least room to the head and to the trunk
part boxes; the roll bar top's displacement at the end; the ground's peak force; broken members; the frame's split.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from ..checks import _check
from ..contact import K_CONTACT, ROLLBAR_PREFIXES, design_points
from ..crash_beam import measure
from ..crash_beam.mesh import build_mesh
from ..crash_beam.sim import REC_DT, new_state, run_crash
from ..model import DesignError, build_vehicle
from ..scenario import tilt_sweep
from ..units import G0
from .base import TIPPED_ROLL, P, Pair, _res
from .crash_beam import HINGE_BREAK_DEG
from .rollover import TABLE_MARGIN, _RolloverBase, tilt_run

FLOOR_MU = 2.0          # friction between the frame and the ground in the landing: the table's (rollover_tilt's
                        # table_mu, chosen so that the vehicle tips rather than slides); not checked (確かめていない)
TIME_LIMIT = 1.0        # s, the landing's time limit (HIRAKU's choice)
REST_SPEED = 0.05       # m/s: the landing is at rest when the nodes' mass-weighted rms speed is below this ...
REST_HOLD = 0.05        # s ... this long (HIRAKU's choices)
ROOM_STEP = 0.005       # m, the members' axes are sampled this often for the room
ROOM_EVERY = 1e-3       # s, the room is read at the record row nearest each multiple of this (and the last row)
TOP_PREFIX = "rollbar_top"
# What this test is (the record's first note; the load case's title and basis say the same, analysis.r4_rops)
NOT_A_STANDARD = "HIRAKU 独自の計算の確かめ。規格の試験ではない"
STANDARDS_NOTE = (
    f"R4-rollover: {NOT_A_STANDARD}. OECD Code 4 (static, for tractors of at least 600 kg, its 2.1 as read on "
    "2026-11-03; the 600 kg is not in README's table of checked values) and ISO 3471 (static, for machines of about "
    "700 kg and up: 確かめていない) are static tests for machines of 600-700 kg and up; whether ANSI/ROHVA 1 has a "
    "dynamic (rollover) test was not checked (the standard is sold and was not opened). R4's 10 kN from above (Code 4's "
    "20 N/kg times the reference mass 500 kg) is 「HIRAKU の仮定」, and so are the landing's set-up and constants below")


class _Touched(Exception):
    """Raised from tilt_run's on_step to stop the rigid phase at the roll bar's first touch."""


def k2_momentum(sim) -> dict:
    """K2's whole vehicle at the present state, road frame: mass M (sprung + unsprung), mass centre c, linear
    momentum P and angular momentum L about c (the sprung body's I_s omega and its centre's motion, the unsprung
    point masses at the wheel centres moving with the body and their travel rate; the wheels' own spin is left out),
    kinetic energy E."""
    r = sim.rotation()
    p = np.array(sim.p, float)
    v = np.array(sim.v, float)
    w = np.array(sim.w, float)  # body axes
    m_s = float(sim.veh.m_s)
    pts, vel, mass = [p], [v], [m_s]
    for i in range(sim.n):
        mu = float(sim.mu_[i])
        if mu <= 0.0:
            continue
        g = sim._path(i, sim.s[i])
        c_b = np.array(g[:3], float)
        dc = np.array(g[3:6], float)
        pts.append(p + r @ c_b)
        vel.append(v + r @ (np.cross(w, c_b) + dc * float(sim.sd[i])))
        mass.append(mu)
    pts, vel, mass = np.array(pts), np.array(vel), np.array(mass)
    M = float(mass.sum())
    c = mass @ pts / M
    P = mass @ vel
    i_s = np.array(sim.i_s, float)
    L = r @ (i_s @ w) + np.cross(pts - c, mass[:, None] * vel).sum(axis=0)
    E = 0.5 * float(mass @ (vel * vel).sum(axis=1) + w @ i_s @ w)
    return {"M": M, "c": c, "P": P, "L": L, "E": E}


def mesh_inertia(mesh, about=None) -> np.ndarray:
    """3 x 3 kg m^2: the mesh's inertia about `about` (default its mass centre): its nodes as point masses plus their
    own (isotropic) rotational inertia."""
    M = float(mesh.mass.sum())
    c = mesh.mass @ mesh.x0 / M if about is None else np.asarray(about, float)
    r = mesh.x0 - c
    rr = (r * r).sum(axis=1)
    return (np.einsum("k,k->", mesh.mass, rr) * np.eye(3) - np.einsum("k,ki,kj->ij", mesh.mass, r, r)
            + float(mesh.inertia.sum()) * np.eye(3))


def handoff(mesh, R, p, cg_s, mom: dict) -> dict:
    """The mesh's starting rigid motion in the design frame from K2's state: R (body to road), p (the sprung centre,
    road frame), cg_s (the sprung centre, design frame), mom (k2_momentum). The velocity field u + omega x (x - c_m)
    gives the mesh K2's linear momentum and K2's angular momentum about the mass centre, each model about its own:
        u = P_b / M_mesh,   I_m omega = L_b
    (P_b, L_b = R^T P, R^T L; I_m the mesh's inertia about its own centre c_m). The two mass centres are not the same
    body point when the models spread the mass differently (c_k2 = cg_s + R^T (c - p), K2's in the design frame);
    keeping the angular momentum about one fixed point instead would add M (c_m - c_k2) x u to the mesh's spin.
    Returns {"velocity": u, "spin": omega, "about": c_m, "c_k2": c_k2}."""
    R = np.asarray(R, float)
    M = float(mesh.mass.sum())
    c_m = mesh.mass @ mesh.x0 / M
    c_d = np.asarray(cg_s, float) + R.T @ (np.asarray(mom["c"], float) - np.asarray(p, float))
    P_b, L_b = R.T @ np.asarray(mom["P"], float), R.T @ np.asarray(mom["L"], float)
    om = np.linalg.solve(mesh_inertia(mesh, c_m), L_b)
    return {"velocity": P_b / M, "spin": om, "about": c_m, "c_k2": c_d}


def state_momentum(mesh, st, about) -> tuple[np.ndarray, np.ndarray]:
    """(P, L about `about`) of a crash_beam state: sum m v, sum (x - about) x m v + J w."""
    P = mesh.mass @ st.v
    L = np.cross(st.x - np.asarray(about, float), mesh.mass[:, None] * st.v).sum(axis=0) + mesh.inertia @ st.w
    return P, L


def touch_state(veh, design: dict, left_up: bool, *, mu: float, k: float, dt: float = 1e-3, hold_past: float,
                after_tip_s: float = 5.0) -> dict:
    """rollover.tilt_run to the roll bar's first touch: the state the step of its first contact force started from
    ({"t", "p", "v", "w", "R", "mom" (k2_momentum), "point", "point_design", "group", "plane" (q, n), "gravity",
    "cg_s", "m_s", "tip_angle", "fallback"}). Without a roll bar touch, the first touch of a body point that is not on
    a wheel after the tip (fallback True); neither: DesignError."""
    pts = design_points(design)
    keep = {"prev": None, "touch": None, "body": None, "tipped": False}

    def snap(sim):
        return {"t": float(sim.t), "p": np.array(sim.p, float), "v": np.array(sim.v, float),
                "w": np.array(sim.w, float), "R": sim.rotation().copy(), "mom": k2_momentum(sim),
                "gravity": np.array(sim.g, float), "planes": [(np.array(q, float), np.array(n, float))
                                                               for q, n in getattr(sim.road, "planes", [])]}

    def on_step(sim, con):
        hit = [i for i, pt in enumerate(con.points) if any(f != 0.0 for f in con.force[i])]
        prev = keep["prev"]
        if prev is not None:
            rb = [i for i in hit if con.points[i].group == "rollbar"]
            if rb:
                i = max(rb, key=lambda j: math.sqrt(sum(f * f for f in con.force[j])))
                keep["touch"] = dict(prev, point=con.points[i].name, point_design=np.array(con.points[i].design),
                                     group="rollbar")
                raise _Touched()
            body = [i for i in hit if con.points[i].group != "wheel"]
            if body and keep["body"] is None and keep["tipped"]:
                i = body[0]
                keep["body"] = dict(prev, point=con.points[i].name, point_design=np.array(con.points[i].design),
                                    group=con.points[i].group)
        if not keep["tipped"]:  # the body's up axis past TIPPED_ROLL from the world's up (against gravity)
            g = np.array(sim.g, float)
            keep["tipped"] = float(sim.rotation()[:, 2] @ (-g / np.linalg.norm(g))) < math.cos(TIPPED_ROLL)
        keep["prev"] = snap(sim)

    try:
        st = tilt_run(veh, design, left_up, mu=mu, k=k, dt=dt, rec_hz=0.0, after_tip_s=after_tip_s,
                      hold_past=hold_past, points=pts, on_step=on_step)
        tip = st["tip_angle"]
    except _Touched:
        tip = None
    got = keep["touch"] or keep["body"]
    if got is None:
        raise DesignError("in the rollover on the tilt table neither the roll bar nor another body point touched the "
                          "ground after the tip")
    got["fallback"] = keep["touch"] is None
    R = got["R"]
    pos = got["p"] + R @ (got["point_design"] - np.asarray(veh.cg_s, float))
    planes = got["planes"] or [(np.zeros(3), np.array([0.0, 0.0, 1.0]))]
    got["plane"] = max(planes, key=lambda qn: float((qn[0] - pos) @ qn[1]))
    got.update(point_world=pos, cg_s=np.asarray(veh.cg_s, float), m_s=float(veh.m_s), tip_angle=tip,
               hold_past=hold_past)
    return got


def _rest_watch():
    """stop_when for run_crash: the nodes' mass-weighted rms speed below REST_SPEED for REST_HOLD."""
    calm = {"s": 0.0}

    def stop(mesh, st, imp, chunk):
        rms = math.sqrt(float(mesh.mass @ (st.v * st.v).sum(axis=1)) / float(mesh.mass.sum()))
        calm["s"] = calm["s"] + chunk if rms < REST_SPEED else 0.0
        if calm["s"] >= REST_HOLD:
            return (f"at rest (the nodes' mass-weighted rms speed below {REST_SPEED:g} m/s for "
                    f"{REST_HOLD * 1000:g} ms)")
        return None

    return stop


def landing(design: dict, tch: dict, *, mu: float, hinge_lim: float, time_limit: float, mass_scale: float = 1.0,
            mesh=None):
    """The frame (crash_beam) landing on the plane of the touch state tch (touch_state). Returns (run, info)."""
    mesh = build_mesh(design) if mesh is None else mesh
    if mass_scale != 1.0:
        mesh = replace(mesh, mass=mesh.mass * mass_scale, inertia=mesh.inertia * mass_scale,
                       node_ids=list(mesh.node_ids), member_of=list(mesh.member_of))
    R = np.asarray(tch["R"], float)
    q, n = tch["plane"]
    n_b = R.T @ np.asarray(n, float)
    d_b = -n_b
    g_b = R.T @ np.asarray(tch["gravity"], float)
    ho = handoff(mesh, R, tch["p"], tch["cg_s"], tch["mom"])
    run = run_crash(mesh, 0.0, time_limit, mu=mu, hinge_lim=hinge_lim, wall_dir=d_b, velocity=ho["velocity"],
                    spin=ho["spin"], about=ho["about"], gravity=g_b, until_limit=True, stop_when=_rest_watch())
    along = mesh.x0[:mesh.n_frame] @ d_b
    info = dict(ho, d_b=d_b, g_b=g_b, mesh=mesh, plane_node=mesh.node_ids[int(np.argmax(along))],
                plane_past_point=float(along.max() - np.asarray(tch["point_design"], float) @ d_b))
    return run, info


def k4_radius(design: dict, mesh) -> np.ndarray:
    """(ne,) m: each frame element's capsule radius as k4_geometry.rollbar.member_radius gives its member's section
    (0 for the links and the members it cannot size)."""
    from hiraku.solvers.k4_geometry.rollbar import member_radius

    sec = {m["id"]: design["sections"].get(m["section"]) for m in design.get("members", [])}
    out = np.zeros(mesh.ne)
    for e in mesh.frame_elements():
        s = sec.get(mesh.member_of[e])
        try:
            out[e] = member_radius(s) / 1000.0 if s else float(mesh.crad[e])
        except ValueError:
            out[e] = float(mesh.crad[e])
    return out


def rooms(design: dict, run, mesh) -> dict:
    """The rooms of the landing: {"space", "head", "trunk"} -> room_to_boxes' dict, plus "body" (positions in the frame
    of the frame's nodes off the roll bar), "rows", "rb_elements"."""
    from hiraku.solvers.k4_geometry.people import occupants
    from hiraku.solvers.k4_geometry.rollbar import occupant_spaces

    rb_el = [e for e in mesh.frame_elements() if mesh.member_of[e].startswith(ROLLBAR_PREFIXES)]
    rb_nodes = {int(k) for e in rb_el for k in mesh.con[e]}
    ref = [k for k in range(mesh.n_frame) if k not in rb_nodes]
    body = measure.in_body_frame(run, ref)
    tt = run.t_all
    marks = np.arange(0.0, tt[-1] + 1e-12, ROOM_EVERY)
    rows = sorted({int(k) for k in np.clip(np.rint(marks / run.chunk), 0, len(tt) - 1)} | {len(tt) - 1})
    rad = k4_radius(design, mesh)
    els = mesh.frame_elements()
    parts = {p["id"]: p for p in design.get("parts", [])}
    spaces = []
    for s in occupant_spaces(design):
        sh = mesh.node_parts.get(s["occupant"])
        if sh:
            lo, hi = np.asarray(s["lo_mm"], float), np.asarray(s["hi_mm"], float)
            spaces.append({"id": f"{s['occupant']} space", "center_mm": (lo + hi) / 2.0, "size_mm": hi - lo,
                           "shares": sh})
    if not spaces:
        raise DesignError("no occupant space on the mesh's nodes (k4_geometry.rollbar.occupant_spaces with "
                          "carried_by nodes): R4's criterion has nothing to measure")

    def boxes(ids):
        out = []
        for pid in ids:
            p = parts.get(pid)
            sh = mesh.node_parts.get(pid)
            if p and p.get("box") and sh:
                out.append({"id": pid, "center_mm": p["box"]["center_mm"], "size_mm": p["box"]["size_mm"],
                            "rpy_deg": p["box"].get("rpy_deg"), "shares": sh})
        return out

    trunks = [o["id"] for o in occupants(design)]
    heads = [pid for pid in parts if pid.endswith(".head") and parts[pid].get("kind") == "occupant"]
    return {"space": measure.room_to_boxes(run, body, spaces, els, rad, rows, ROOM_STEP),
            "head": measure.room_to_boxes(run, body, boxes(heads), els, rad, rows, ROOM_STEP),
            "trunk": measure.room_to_boxes(run, body, boxes(trunks), els, rad, rows, ROOM_STEP),
            "body": body, "rows": rows, "rb_elements": rb_el}


def inside_members(rm: dict) -> list[tuple[str, float]]:
    """The members whose least room to an occupant space was below 0 (inside it), deepest first."""
    by = {}
    for o in rm["space"].values():
        for m, r_ in o["by_member"].items():
            if r_ < 0.0:
                by[m] = min(by.get(m, 0.0), r_)
    return sorted(by.items(), key=lambda kv: kv[1])


def cap_text(rm: dict, mesh, design: dict) -> str:
    """The deepest room each space allows to its worst member: -(half its smallest size + that member's radius)."""
    rad = k4_radius(design, mesh)
    out = []
    for bid, o in rm["space"].items():
        if o["element"] < 0:
            continue
        half = float(np.min(o["size_mm"])) / 2.0
        out.append(f"{bid} {-(half + rad[o['element']] * 1000.0):.0f} mm for {mesh.member_of[o['element']]}")
    return "; ".join(out) or "no member reached a space"


def deepest(rm: dict) -> dict:
    """{space id: (member, room_to_boxes' depth dict)} for the member that went deepest into each occupant space past
    the face it came in through (spaces no member entered are left out)."""
    out = {}
    for bid, o in rm["space"].items():
        got = [(m, d) for m, d in o.get("depth", {}).items() if math.isfinite(d["depth"])]
        if got:
            out[bid] = max(got, key=lambda md: md[1]["depth"])
    return out


def depth_text(rm: dict, tt) -> str:
    """The record's note on the deepest intrusion into each occupant space (rops_occupant_clearance stops at minus
    half the space's smallest size minus the radius once a member is at the middle; this does not)."""
    d = deepest(rm)
    if not d:
        return "the deepest intrusion into an occupant space: no member entered one"
    parts = []
    for bid, (m, dp) in d.items():
        parts.append(f"{bid}: {m} {dp['depth'] * 1000:.0f} mm past the space's {dp['face']} face it came in through "
                     f"(at {tt[dp['row']] * 1000:.0f} ms; it entered at "
                     f"{tt[dp['entered_row']] * 1000:.0f} ms)")
    return ("the deepest intrusion into each occupant space, not stopped at the space's middle (the depth of the "
            "member's capsule past the plane of the face it came in through, along that face's inward normal, in the "
            "space's axes, over the samples within the face's extent; it can pass the far face, the space's size along "
            "that normal being the depth of a member right through it): " + "; ".join(parts)
            + ". A note, not a quantity of the record")


def _least(got: dict) -> tuple[str, dict] | None:
    return min(got.items(), key=lambda kv: kv[1]["room"]) if got else None


class RolloverBeamTool(_RolloverBase):
    """R4 by a rollover on the tilt table: the rigid body with its ground contact to the roll bar's first touch, then
    the frame as yielding, breaking beams landing on that ground (module docstring).

    Results: rops_occupant_clearance (mm, where = the occupant's space and the member), head_room and trunk_room (mm,
    the least room to the head and trunk part boxes, where = the part and the member), rollbar_top_displacement (mm,
    the largest of the roll bar top's nodes at the end of the landing, relative to the frame's other nodes, where =
    the node), ground_force_peak (N, the ground's force on the frame along its normal, 1 ms mean), broken_members (1,
    where = the ids), frame_split_fraction (1, the largest share of the mass in a piece broken off the seats' side),
    unsupported_load (1), max_plastic_rotation (deg), and from the rigid phase tip_angle (deg),
    rollbar_touch_time (s), touch_speed (m/s, the touching point's speed into the ground) and touch_roll_rate
    (deg/s)."""

    name = "rollover_beam"
    title = "横転してロールバーが床に当たる (傾斜台で倒し、骨組みを梁でつぶす、K2)"
    domain = "strength"
    stage = 3
    tests = ["R4"]
    pairs = (Pair("rops_occupant_clearance", "frame_static.rops_occupant_clearance", "rops_occupant_clearance",
                  note="the hand calculation pushes the roll bar alone as a portal frame on its feet by OECD Code 4's "
                       "energies and force (borrowed) and measures the gap to k4_geometry.occupant_spaces at those "
                       "deflections; the tool drops the whole frame onto the ground in the rollover's own state and "
                       "measures every member's least gap to the same spaces over the landing"),
             Pair("rops_side_deflection", "frame_static.portal_frame_side", "delta_E",
                  note="intermediate: the hand calculation's sideways deflection at Code 4's side energy; the tool has "
                       "no such push (rollbar_top_displacement is the landing's)"),
             Pair("rops_rear_deflection", "frame_static.portal_frame_rear", "delta_E",
                  note="intermediate: the hand calculation's deflection along the vehicle at Code 4's rear energy"),
             Pair("rops_top_deflection", "frame_static.portal_frame_top", "delta",
                  note="intermediate: the hand calculation's deflection down at mid-span under Code 4's crushing force"),
             Pair("unsupported_load", "load_path.free_parts", "n",
                  note="the path check (hiraku.catalog.builders.load_path.PATH_PREDICTION): the parts with mass, "
                       "followed by their contacts to the members; the tool counts the parts with mass it put on no "
                       "node"))
    params = (P("direction", None, "both", "which way the table tips; right = the table's right side goes down; "
                "both: the side with the smaller tip angle", choices=("both", "right", "left")),
              P("table_mu", "1", 2.0, "table and floor friction in the rigid phase (tyres and body points), high so "
                                      "that it tips, not slides, as in the tilt tool"),
              P("hold_past", "deg", 1.0, "the table is held this far past the tip angle"),
              P("contact_stiffness", "N/mm", K_CONTACT / 1000.0, "per body contact point in the rigid phase "
                "(assumed, no source; it changes the state at the roll bar's touch)", assumed=True),
              P("floor_mu", "1", FLOOR_MU, f"friction between the frame and the ground in the landing ({FLOOR_MU:g}: "
                "the table's; not checked)"),
              P("hinge_break_angle", "deg", HINGE_BREAK_DEG, f"a beam breaks when a plastic hinge at its end turns "
                f"past this ({HINGE_BREAK_DEG:g} deg, crash_beam's; no source, not checked)"),
              P("time_limit", "s", TIME_LIMIT, "the landing stops here if it has not come to rest", run_option=True),
              P("mesh_mass_scale", "1", 1.0, "the beam mesh's masses and rotational inertias times this (a study of "
                "the mesh's mass against K2's; 1 in a judged run)", run_option=True))

    def own_checks(self) -> list[dict]:
        out = super().own_checks()
        h = handoff_check()
        for key, label, ja in (("P", "linear momentum", "運動量"), ("L", "angular momentum about its own mass centre",
                                                                     "それぞれの重心まわりの角運動量")):
            out.append(_check(f"a beam frame (demo design) handed K2's state of a 1.6 rad/s roll, 2.5 m/s sideways and "
                              f"the wheels' travel rates, the body turned 58 deg: the mesh's {label} / K2's (largest "
                              "component of the difference over the size)", f"handoff_{key}", 0.0, h[key], "1", 1e-9,
                              "the hand-off solves u = P / M and I_m omega = L (each about its own mass centre)",
                              case_ja=f"K2 の剛体の状態 (横転の向きに 1.6 rad/s、横に 2.5 m/s、車輪の上下の速さつき、58° 傾いた"
                                      f"車体) を梁の骨組み (demo の設計) に渡したときの{ja}の食い違い (K2 の値の大きさで割る)",
                              source_ja="渡し方: u = P / M、I_m ω = L (それぞれ自分の重心まわり)"))
        return out

    def _run(self, design: dict, lp: dict) -> dict:
        mu_t = self.q(lp, "table_mu", "1")
        veh = build_vehicle(design, {"mu": mu_t})
        k = self.q(lp, "contact_stiffness", "N/m")
        side = self.text(lp, "direction")
        notes = list(veh.assumptions)
        if side == "both":
            angles = {}
            for s in ("right", "left"):
                a = tilt_sweep(veh, left_up=(s == "right"), dt=self.dt)["tip_angle"]
                angles[s] = math.inf if a is None else a
            side = min(angles, key=lambda s: angles[s])
            notes.append("tip angles without the body's contact: " + ", ".join(
                f"{s} {math.degrees(a):.3f} deg" for s, a in angles.items()) + f"; the run tips to the {side}")
        tch = touch_state(veh, design, side == "right", mu=mu_t, k=k, dt=self.dt,
                          hold_past=self.q(lp, "hold_past", "rad"))
        mu_f = self.q(lp, "floor_mu", "1")
        lim = self.q(lp, "hinge_break_angle", "rad")
        t_lim = self.q(lp, "time_limit", "s")
        scale = self.q(lp, "mesh_mass_scale", "1")
        mesh0 = build_mesh(design)
        run, info = landing(design, tch, mu=mu_f, hinge_lim=lim, time_limit=t_lim, mass_scale=scale, mesh=mesh0)
        mesh = info["mesh"]
        rm = rooms(design, run, mesh)
        tt = run.t_all
        res = []
        sp = _least(rm["space"])
        res.append(_res("rops_occupant_clearance", sp[1]["room"] * 1000.0, "mm",
                        f"{sp[0]}, {mesh.member_of[sp[1]['element']]} at {tt[sp[1]['row']] * 1000:.0f} ms"))
        for key, q in (("head", "head_room"), ("trunk", "trunk_room")):
            lo = _least(rm[key])
            if lo is not None:
                res.append(_res(q, lo[1]["room"] * 1000.0, "mm",
                                f"{lo[0]}, {mesh.member_of[lo[1]['element']]} at {tt[lo[1]['row']] * 1000:.0f} ms"))
        body = rm["body"]
        top_nodes = sorted({int(n_) for e in rm["rb_elements"] if mesh.member_of[e].startswith(TOP_PREFIX)
                            for n_ in mesh.con[e]})
        if not top_nodes and rm["rb_elements"]:
            rbn = sorted({int(n_) for e in rm["rb_elements"] for n_ in mesh.con[e]})
            top_nodes = [max(rbn, key=lambda k_: mesh.x0[k_, 2])]
        if top_nodes:
            u_end = np.linalg.norm(body[-1, top_nodes] - mesh.x0[top_nodes], axis=1)
            j = int(np.argmax(u_end))
            res.append(_res("rollbar_top_displacement", float(u_end[j]) * 1000.0, "mm", mesh.node_ids[top_nodes[j]]))
        force = run.decel() * run.total_mass
        kf = int(np.argmax(force))
        res.append(_res("ground_force_peak", float(force[kf]), "N", f"at {tt[kf] * 1000:.1f} ms"))
        broken = measure.broken_members(run)
        res.append(_res("broken_members", float(len(broken)), "1", ", ".join(broken) if broken else None))
        res.append(_res("frame_split_fraction", max((pc["share"] for pc in run.extra.get("pieces", [])), default=0.0),
                        "1"))
        lost = [p["id"] for p in design.get("parts", []) if float(p.get("mass_kg", 0.0)) > 0.0
                and not mesh0.node_parts.get(p["id"])]
        res.append(_res("unsupported_load", float(len(lost)), "1", ", ".join(lost) if lost else None))
        hinges = measure.member_hinges(run)
        worst = max(hinges, key=lambda m: hinges[m])
        res.append(_res("max_plastic_rotation", math.degrees(hinges[worst]), "deg", worst if hinges[worst] > 0 else None))
        # the rigid phase
        R = np.asarray(tch["R"], float)
        n_w = np.asarray(tch["plane"][1], float)
        v_pt = tch["v"] + R @ np.cross(tch["w"], tch["point_design"] - tch["cg_s"])
        if tch["tip_angle"] is not None:
            res.append(_res("tip_angle", math.degrees(tch["tip_angle"]), "deg", f"tipping to the {side}"))
        res.append(_res("rollbar_touch_time", tch["t"], "s", tch["point"]))
        res.append(_res("touch_speed", float(-(v_pt @ n_w)), "m/s", tch["point"]))
        res.append(_res("touch_roll_rate", math.degrees(float(np.linalg.norm(tch["w"]))), "deg/s"))
        # places: each member's least room to an occupant space (the criterion's quantity, not judged by place)
        by = {}
        for o in rm["space"].values():
            for m, r_ in o["by_member"].items():
                if math.isfinite(r_):
                    by[m] = min(by.get(m, math.inf), r_)
        places = [{"where": m, "quantity": "rops_occupant_clearance", "value": r_ * 1000.0, "unit": "mm",
                   "judged": False} for m, r_ in sorted(by.items())]
        # the series: the least room to the spaces and the ground's force at the room's rows
        t_s = [round(float(tt[k_]), 6) for k_ in rm["rows"]]
        least = np.min(np.array([o["series"] for o in rm["space"].values()]), axis=0)
        series = [{"channel": "rops_occupant_clearance", "unit": "mm", "t_s": t_s,
                   "values": [float(x) * 1000.0 for x in least]},
                  {"channel": "ground_force", "unit": "N", "t_s": t_s, "values": [float(force[k_]) for k_ in rm["rows"]]}]
        notes += self._notes(design, veh, tch, run, info, mesh, mesh0, rm, sp, mu_t, k, mu_f, lim, scale, side)
        return {"results": res, "series": series, "assumptions": notes, "places": places}

    def _notes(self, design, veh, tch, run, info, mesh, mesh0, rm, sp, mu_t, k, mu_f, lim, scale, side) -> list[str]:
        tt = run.t_all
        mom = tch["mom"]
        R = np.asarray(tch["R"], float)
        w_k2 = R @ tch["w"]
        w_mesh = R @ info["spin"]
        M_mesh = float(mesh.mass.sum())
        c_off = (info["about"] - info["c_k2"]) * 1000.0
        st0 = new_state(mesh, info["velocity"], None, spin=info["spin"], about=info["about"])
        ke_mesh = 0.5 * float(mesh.mass @ (st0.v * st0.v).sum(axis=1) + mesh.inertia @ (st0.w * st0.w).sum(axis=1))
        n_b = -info["d_b"]
        pt = np.asarray(tch["point_design"], float)
        v_mesh_pt = float(-(info["velocity"] + np.cross(info["spin"], pt - info["about"])) @ n_b)
        v_k2_pt = float(-(tch["v"] + R @ np.cross(tch["w"], pt - tch["cg_s"])) @ np.asarray(tch["plane"][1], float))
        out = [
            STANDARDS_NOTE,
            f"the rigid phase: rollover_tilt's run (the table turned as the tilt tool turns it to the tip, then "
            f"{math.degrees(tch['hold_past']):g} deg past it at 4 deg/s and held, the brakes held; from the "
            f"tip on the ground is the table and the level floor hinged at its low edge {TABLE_MARGIN * 1000:.0f} mm "
            f"outside the wheels), tipping to the {side}, stopped at the state the step of "
            + (f"the roll bar's first contact force started from ({tch['point']} at t {tch['t']:.3f} s)" if not tch["fallback"]
               else f"the first contact force of a body point after the tip started from ({tch['point']}, group "
                    f"{tch['group']}, at t {tch['t']:.3f} s: the roll bar did not touch)"),
            f"the ground contact stiffness of the rigid phase: {k / 1000:g} N/mm per point (HIRAKU's choice, 確かめていない); "
            f"table and floor friction {mu_t:g} (確かめていない); it sets the state at the touch",
            f"the hand-off: K2's whole vehicle {mom['M']:.1f} kg (sprung {tch['m_s']:.1f} kg + unsprung "
            f"{mom['M'] - tch['m_s']:.1f} kg), linear momentum {np.linalg.norm(mom['P']):.1f} N s and angular momentum "
            f"about its mass centre {np.linalg.norm(mom['L']):.1f} N m s, given to the beam mesh as a rigid motion with the "
            f"same momentum and the same angular momentum about its own mass centre (the wheels' spin left out). The "
            f"mesh's mass {M_mesh:.1f} kg (x{scale:g}) against K2's sprung mass {tch['m_s']:.1f} kg and whole mass "
            f"{mom['M']:.1f} kg (確かめていない which is right for the landing: the mesh carries the wheels on links and "
            f"every part's mass on its carried_by nodes); its mass centre is {np.linalg.norm(c_off):.0f} mm from K2's "
            f"({', '.join(f'{x:+.0f}' for x in c_off)} mm, design axes) and its inertia differs, so it turns at "
            f"{np.linalg.norm(w_mesh):.3f} rad/s against K2's {np.linalg.norm(w_k2):.3f} rad/s; kinetic energy "
            f"{ke_mesh:.0f} J against K2's {mom['E']:.0f} J; the touching point moves into the ground at "
            f"{v_mesh_pt:.2f} m/s against K2's {v_k2_pt:.2f} m/s",
            f"the landing: the frame as crash_beam's yielding beams (its record's model notes hold: elastic-perfectly "
            f"plastic, the floor plates not in it, no strain rate) on the plane the touching point went into (normal "
            f"{', '.join(f'{x:+.3f}' for x in -info['d_b'])} in the design frame), placed at the frame node furthest into "
            f"it ({info['plane_node']}, {info['plane_past_point'] * 1000:.1f} mm past the touching point), gravity "
            f"{np.linalg.norm(info['g_b']):.2f} m/s^2 on every node; the plane's friction {mu_f:g} (確かめていない); the "
            f"wheels touch it through their tyres; time step {run.dt * 1e6:.2f} us",
            f"a beam element breaks when a plastic hinge at its end turns past {math.degrees(lim):g} deg (crash_beam's "
            "choice, 確かめていない)",
            f"the landing ran to {tt[-1] * 1000:.0f} ms: {run.stopped}",
            "rops_occupant_clearance: the least room over the landing (every "
            f"{ROOM_EVERY * 1000:g} ms) between every frame member, a capsule of k4_geometry.rollbar.member_radius (half "
            "the diagonal of a rect tube), and the occupants' spaces of k4_geometry.rollbar.occupant_spaces (as R4's "
            "criterion), the spaces moving with the occupants' carried_by nodes, in the frame of the frame's own nodes "
            f"off the roll bar; at the start {sp[1]['start'] * 1000:.0f} mm; head_room and trunk_room the same to the "
            "head and trunk part boxes. Inside a box the room is minus the depth to the box's nearest face (as "
            "k4_geometry.rollbar.occupant_clearance), so it is at least -(half the box's smallest size + the radius): "
            f"here {cap_text(rm, mesh, design)}; members inside a space at some time: "
            + (", ".join(f"{m} {r_ * 1000:.0f} mm" for m, r_ in inside_members(rm)) or "none"),
            depth_text(rm, tt),
            "rollbar_top_displacement: at the end of the landing (rest or the time limit), relative to the frame's "
            "nodes off the roll bar (the elastic part is in it when the frame is still moving)",
            "ground_force_peak: the ground's impulse along its normal over 1 ms / 1 ms",
            "frame_split_fraction: the largest share of the whole mass in a piece broken off the seats' side (any piece)",
        ]
        return out


def handoff_check() -> dict:
    """The hand-off on the demo design (data/designs/offroad-4w-v1.json, two_cargo): K2's vehicle put in a turned,
    rolling state with wheel travel rates, handed to the design's beam mesh; the mesh's starting state's momentum and
    angular momentum about K2's mass centre (state_momentum), turned to the road frame, against K2's (k2_momentum).
    Returns the largest component of (mesh - K2) / |K2| for P and L."""
    import json
    from pathlib import Path

    from ..scenario import new_sim
    from ..sim import quat_from_euler

    root = Path(__file__).resolve().parents[4]
    design = json.loads((root / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
    from hiraku.contract.loading import on_board

    design = on_board(design, "two_cargo")
    veh = build_vehicle(design)
    sim = new_sim(veh, dt=1e-3, settle_s=0.0)
    sim.q = quat_from_euler(math.radians(58.0), math.radians(3.0), math.radians(-2.0))
    sim.v = [0.04, -2.5, -0.3]
    sim.w = [1.6, -0.11, 0.06]
    sim.sd = [0.1 * (i + 1) for i in range(sim.n)]
    mom = k2_momentum(sim)
    mesh = build_mesh(design)
    R = sim.rotation()
    ho = handoff(mesh, R, sim.p, veh.cg_s, mom)
    st = new_state(mesh, ho["velocity"], None, spin=ho["spin"], about=ho["about"])
    P, L = state_momentum(mesh, st, ho["about"])
    P_w, L_w = R @ P, R @ L
    return {"P": float(np.abs(P_w - mom["P"]).max() / np.linalg.norm(mom["P"])),
            "L": float(np.abs(L_w - mom["L"]).max() / np.linalg.norm(mom["L"]))}
