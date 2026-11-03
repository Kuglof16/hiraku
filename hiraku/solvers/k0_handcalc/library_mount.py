"""K0: the root of a lower arm's leg when the ladder twists (frame_static.mount_leg_stress).

A lower arm's two pivots hang on legs from the rails, and a lower cross member joins the left and right
pivots: legs and cross member make a U under the rails at one station. When the ladder twists, its rails
bend vertically opposite ways (warping), so at the station the left rail pitches by theta and the right by
-theta. The U resists that: its cross member is twisted and its legs are bent. This is the U's half on the
left, cut at the cross member's middle where the antisymmetric deformation leaves no fore-and-aft or vertical
movement and no turn about the cross member's axis (three redundants, force method; the members' flexibility
by the unit-load method, Simpson's rule, exact for the linear moments). The leg stands on the rail node with
three rotational springs: the rail's roll (k_x), pitch (k_y) and yaw (k_z) against the rest of the ladder, so
the U turns the rail back and is turned less than the free ladder would be (theta: the free ladder's pitch
there, frame_inputs.mount_leg_inputs from the warping torsion of the ladder without the U).

Stress at the leg's end on the rail: axial force and the two bending moments at the tube's corners (as the
fatigue tool reads a weld: normal stress only), for the section axes K1 gives an inclined member (one pair of
walls upright: local z horizontal across the leg, local y = z x the leg's axis).
"""

from __future__ import annotations

import numpy as np

from . import sections
from .registry import Formula


def _leg_axes(e: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local y and z of a member along e (K1's convention: a vertical member's local y along x; otherwise local z
    horizontal, local y = z x e, up for a horizontal member)."""
    if abs(e[0]) < 1e-9 and abs(e[1]) < 1e-9:
        y = np.array([1.0, 0.0, 0.0])
        return y, np.cross(e, y)
    z = np.cross(e, [0.0, 0.0, 1.0])
    z /= np.linalg.norm(z)
    return np.cross(z, e), z


def half_u(v: dict, theta: float = 1.0) -> dict:
    """The left half of the U for the free ladder's pitch theta (rad) at the leg's rail node. Coordinates from the
    leg's node: the pivot at (dx, dy, dz), the cut at (dx, dy - y_p, dz) on the centre line."""
    E, G = v["E"], v["E"] / (2.0 * (1.0 + v["nu"]))
    leg = sections.rect_tube(v["b_l"], v["b_l"], v["t_l"])
    crs = sections.rect_tube(v["b_x"], v["b_x"], v["t_x"])
    B = np.zeros(3)
    T = np.array([v["dx"], v["dy"], v["dz"]])
    C = T - np.array([0.0, v["y_p"], 0.0])
    ex, ey, ez = np.eye(3)
    loads = [(ex, np.zeros(3)), (ez, np.zeros(3)), (np.zeros(3), ey)]  # unit force x, force z, moment y at the cut
    F = np.zeros((3, 3))
    for P0, P1, s in ((B, T, leg), (T, C, crs)):
        L = float(np.linalg.norm(P1 - P0))
        if L <= 0.0:
            continue
        e = (P1 - P0) / L
        for X, wgt in ((P0, 1.0), ((P0 + P1) / 2.0, 4.0), (P1, 1.0)):  # Simpson: products of linear moments
            parts = []
            for f, m in loads:
                M = np.cross(C - X, f) + m
                T_ = float(M @ e)
                parts.append((float(f @ e), T_, M - T_ * e))
            for i in range(3):
                for j in range(3):
                    Ni, Ti, Mi = parts[i]
                    Nj, Tj, Mj = parts[j]
                    F[i, j] += wgt * L / 6.0 * (Ni * Nj / (E * s["A"]) + Ti * Tj / (G * s["J"]) + float(Mi @ Mj) / (E * s["Iz"]))
    MB = [np.cross(C - B, f) + m for f, m in loads]  # the base moments of the unit loads
    for k, ax in ((v["k_x"], ex), (v["k_y"], ey), (v["k_z"], ez)):  # the rail's springs in series at the base
        a = np.array([float(M @ ax) for M in MB])
        F += np.outer(a, a) / k
    u0 = np.cross(theta * ey, C - B)  # the cut moved by the free pitch about the base
    R = np.linalg.solve(F, -np.array([u0[0], u0[2], theta]))
    f = R[0] * ex + R[1] * ez
    M_B = np.cross(C - B, f) + R[2] * ey  # moment the cut's forces make at the leg's base
    e = (T - B) / float(np.linalg.norm(T - B))
    y, z = _leg_axes(e)
    N = float(f @ e)
    # moments about the local y (fibre distance c_z = b/2) and z (c_y = h/2), at the four corners as K1 reads them
    M_about_y, M_about_z = float(M_B @ y), float(M_B @ z)
    corners = [N / leg["A"] + s1 * M_about_y * leg["cz"] / leg["Iy"] + s2 * M_about_z * leg["cy"] / leg["Iz"]
               for s1, s2 in ((1, 1), (1, -1), (-1, 1), (-1, -1))]
    worst = max(corners, key=abs)
    turn = theta + float(M_B @ ey) / v["k_y"]  # the rail's pitch there with the U on it (the U's moment turns it back)
    return {"sigma": worst, "N": N, "M_y": M_about_y, "M_z": M_about_z, "T": float(M_B @ e), "theta_base": turn,
            "M_cross": -float(R[2])}


def _leg(v: dict) -> dict:
    u = half_u(v, 1.0e-3)  # per mrad of the free ladder's pitch (linear)
    s = u["sigma"] / 1.0e-3  # MPa per rad
    # one wheel's 1 kN is half the antisymmetric pair (the left-right alike half does not pitch the rails apart)
    return {"sigma_fa": 0.5 * s * v["theta_front"], "sigma_fb": -0.5 * s * v["theta_front"],
            "sigma_ra": 0.5 * s * v["theta_rear"], "sigma_rb": -0.5 * s * v["theta_rear"],
            "sigma_per_mrad": abs(u["sigma"]), "M_leg_y_per_mrad": u["M_y"], "M_leg_z_per_mrad": u["M_z"],
            "T_cross_per_mrad": u["M_cross"], "base_turn": u["theta_base"] / 1.0e-3}


def _mount_leg_stress() -> Formula:
    mm = lambda d: {"unit": "mm", "description": d, "min": 0}  # noqa: E731
    spring = lambda d: {"unit": "N*mm", "description": d + " (N*mm per rad; frame_inputs.rail_springs)", "min": 0}  # noqa: E731
    pitch = ("pitch (rad) of the left rail at the leg's node in the free ladder (the warping torsion without the U) for "
             "1 kN up at the left and 1 kN down at the right {} wheel (frame_inputs.mount_leg_inputs)")
    per_kN = "stress at the worst corner of the leg's end on the rail for 1 kN up at"
    return Formula(
        formula_id="frame_static.mount_leg_stress",
        title="はしごがねじれたときの、下の腕の脚の根元の応力 (脚と下の横の部材の U 字、レールのばねの上)",
        domain="strength",
        expression=("half U (leg to the pivot, half the lower cross member to the centre line), cut at the centre line with "
                    "u_x = u_z = rot_y = 0; force method: F R = -u0, F = sum int (N N'/EA + T T'/GJ + M.M'/EI) ds "
                    "+ a a'/k for the rail's springs k_x, k_y, k_z at the base; u0 = the cut moved by the free pitch theta; "
                    "sigma = N/A +- M_y c_z/I_y +- M_z c_y/I_z at the leg's base; one wheel = half the antisymmetric pair"),
        inputs={
            "dx": {"unit": "mm", "description": "pivot ahead of the leg's rail node (x)"},
            "dy": {"unit": "mm", "description": "pivot beside the leg's rail node (y; negative inboard of the left rail)"},
            "dz": {"unit": "mm", "description": "pivot above the leg's rail node (z; negative below)"},
            "y_p": mm("pivot's distance from the centre line (half the lower cross member)"),
            "b_l": mm("leg tube width (square)"), "t_l": mm("leg tube wall"),
            "b_x": mm("lower cross member tube width (square)"), "t_x": mm("lower cross member wall"),
            "E": {"unit": "MPa", "description": "Young's modulus", "min": 0},
            "nu": {"unit": "1", "description": "Poisson's ratio", "min": 0, "max": 0.5},
            "k_x": spring("the rail's roll against the rest of the ladder at the leg's node"),
            "k_y": spring("the rails' opposite pitch (the ladder's warping) at the leg's node"),
            "k_z": spring("the rail's yaw against the rest of the ladder at the leg's node"),
            "theta_front": {"unit": "rad", "description": pitch.format("front")},
            "theta_rear": {"unit": "rad", "description": pitch.format("rear")},
        },
        outputs={
            "sigma_fa": ("MPa", per_kN + " front wheel a (left)", None),
            "sigma_fb": ("MPa", per_kN + " front wheel b (right)", None),
            "sigma_ra": ("MPa", per_kN + " rear wheel a (left, the track of front a)", None),
            "sigma_rb": ("MPa", per_kN + " rear wheel b", None),
            "sigma_per_mrad": ("MPa", "the same stress for 1 mrad of the free ladder's pitch at the node", None),
            "M_leg_y_per_mrad": ("N*mm", "the leg's base moment about its local y for 1 mrad", None),
            "M_leg_z_per_mrad": ("N*mm", "the leg's base moment about its local z for 1 mrad", None),
            "T_cross_per_mrad": ("N*mm", "torque in the lower cross member for 1 mrad", None),
            "base_turn": ("1", "the rail's pitch at the node with the U on it over the free ladder's", None),
        },
        assumptions=[
            "the ladder's twist reaches the leg as the opposite pitch of the two rails at the leg's node (warping); the "
            "left-right alike half of the wheels' forces (the rails' roll in mirror) is not counted",
            "left and right legs and the lower cross member are mirror images; the cross member runs straight across (y)",
            "the rail under the leg is three springs (roll, pitch, yaw) against the rest of the ladder, uncoupled",
            "slender beams (no shear deformation); square tubes, one pair of walls upright",
            "only the U at this station: the towers, the dash and the other station's U are not in this model",
        ],
        source="the unit-load (force) method for a statically indeterminate frame and beam bending and Bredt's torsion "
               "(textbook structures); the ladder's warping torsion (Vlasov) gives theta and k_y",
        requires=[(lambda v: 2 * v["t_l"] < v["b_l"] and 2 * v["t_x"] < v["b_x"],
                   "wall thickness must be less than half of the tube width"),
                  (lambda v: v["k_x"] > 0 and v["k_y"] > 0 and v["k_z"] > 0, "the rail's springs must be positive"),
                  (lambda v: v["y_p"] > 0 and (v["dx"] ** 2 + v["dy"] ** 2 + v["dz"] ** 2) > 0,
                   "the leg must have a length and the pivot must be off the centre line")],
        fn=_leg,
    )


def _worst(v: dict) -> dict:
    a, b = v["D_a"], v["D_b"]
    return {"D": max(a, b), "which": 1.0 if a >= b else 2.0}


def _worst_weld() -> Formula:
    return Formula(
        formula_id="fatigue.worst_of_two_welds",
        title="2 か所の溶接の疲労の損傷のうち大きいほう (フレームの寿命はいちばん傷む溶接で決まる)",
        domain="strength",
        expression="D = max(D_a, D_b); which = 1 if D_a >= D_b else 2",
        inputs={"D_a": {"unit": "1", "description": "Miner damage at weld a", "min": 0},
                "D_b": {"unit": "1", "description": "Miner damage at weld b", "min": 0}},
        outputs={"D": ("1", "the larger damage", None), "which": ("1", "1: weld a, 2: weld b", None)},
        assumptions=["the frame's life is that of its most damaged weld (Miner's rule at each weld point separately)"],
        source="Miner's rule applied per weld point (textbook fatigue)",
        fn=_worst,
    )


def all_formulas() -> list[Formula]:
    return [_mount_leg_stress(), _worst_weld()]
