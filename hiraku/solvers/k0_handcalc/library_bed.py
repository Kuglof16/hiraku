"""K0 formulas for a care bed (hiraku.designgen.care_bed): the deck frame under a uniform load, tipping under an
edge load and under a push on the side rail, the side rail pushed outward (with the sleeve its post is inserted
in), the actuators' levers and brackets, the lift, the actuators' duty and the fatigue damage over the counts of
use.

The inputs are made from the design by functions in hiraku.catalog.builders.care_bed (inputs_from).
"""

from __future__ import annotations

import math

import numpy as np
import sympy as sp

from .registry import Formula

THETA_STEPS = 721  # angles scanned between 0 and the largest raise (the force's largest is between two of them)


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _rect_I(b: float, h: float, t: float) -> float:
    """Second moment of a rectangular tube about the axis across h (bending in the plane of h), mm^4."""
    return (b * h**3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0


def _rect_A(b: float, h: float, t: float) -> float:
    return b * h - (b - 2 * t) * (h - 2 * t)


# ---------------------------------------------------------------- the deck frame: a closed portal in slope-deflection


def _deck_frame(v: dict) -> dict:
    """Upper rail (span L between the posts, overhangs a1 at the head and a2 at the foot, uniform w = W / (a1 + L +
    a2)), two posts of length h, the base rail of span L, joined rigidly at the four corners; no sway. Slope-
    deflection with the four joint rotations; member-end moments clockwise positive."""
    E, L, a1, a2, h, W = (v[k] for k in ("E", "L", "a1", "a2", "h", "W"))
    I_r = _rect_I(v["b_r"], v["h_r"], v["t_r"])
    I_p = _rect_I(v["b_p"], v["b_p"], v["t_p"])
    I_g = _rect_I(v["b_g"], v["h_g"], v["t_g"])
    w = W / (a1 + L + a2)
    kr, kp, kg = 2 * E * I_r / L, 2 * E * I_p / h, 2 * E * I_g / L
    fem = w * L**2 / 12.0
    m1o, m2o = w * a1**2 / 2.0, -w * a2**2 / 2.0
    # unknowns th1 (top head), th2 (top foot), th3 (bottom head), th4 (bottom foot)
    K = np.array([[2 * kr + 2 * kp, kr, kp, 0.0],
                  [kr, 2 * kr + 2 * kp, 0.0, kp],
                  [kp, 0.0, 2 * kp + 2 * kg, kg],
                  [0.0, kp, kg, 2 * kp + 2 * kg]])
    rhs = -np.array([-fem + m1o, fem + m2o, 0.0, 0.0])
    th = np.linalg.solve(K, rhs)
    M12 = kr * (2 * th[0] + th[1]) - fem
    M21 = kr * (2 * th[1] + th[0]) + fem
    M13, M31 = kp * (2 * th[0] + th[2]), kp * (2 * th[2] + th[0])
    M24, M42 = kp * (2 * th[1] + th[3]), kp * (2 * th[3] + th[1])
    x = np.linspace(0.0, L, 401)
    # internal moment, sagging positive: the clockwise end moment M12 is the internal moment at the left end, -M21 at the right
    span = w * x * (L - x) / 2.0 + M12 * (1 - x / L) - M21 * (x / L)
    M_rail = max(abs(M12), abs(M21), abs(m1o), abs(m2o), float(np.max(np.abs(span))))
    M_post = max(abs(M13), abs(M31), abs(M24), abs(M42))
    # the posts' axial loads: the rail's reactions (beam statics with the end moments)
    R1 = w * a1 + w * L / 2.0 - (M12 + M21) / L
    R2 = W - R1
    A_p = _rect_A(v["b_p"], v["b_p"], v["t_p"])
    Z_r, Z_p = 2 * I_r / v["h_r"], 2 * I_p / v["b_p"]
    s_rail = M_rail / Z_r
    s_post = M_post / Z_p + max(R1, R2) / A_p
    return {"sigma": max(s_rail, s_post), "sigma_rail": s_rail, "sigma_post": s_post, "M_rail": M_rail,
            "M_post": M_post, "R_head": R1, "R_foot": R2}


def _deck_frame_formula() -> Formula:
    tube = lambda what: {f"b_{what[0]}": _in("mm", f"{what[1]} tube width", min=0),  # noqa: E731
                         f"t_{what[0]}": _in("mm", f"{what[1]} tube wall", min=0)}
    inputs = {"W": _in("N", "load on one upper rail (its share of the deck's load and the rail's own weight)", min=0),
              "L": _in("mm", "post to post along the rail", min=0),
              "a1": _in("mm", "rail beyond the head posts", min=0), "a2": _in("mm", "rail beyond the foot posts", min=0),
              "h": _in("mm", "post length, base rail centre to upper rail centre", min=0),
              "E": _in("MPa", "Young's modulus", min=0),
              **tube(("r", "upper rail")), "h_r": _in("mm", "upper rail tube height", min=0),
              **tube(("p", "post (square)")),
              **tube(("g", "base rail")), "h_g": _in("mm", "base rail tube height", min=0)}
    names = list(inputs)
    return Formula(
        formula_id="bed.deck_frame_udl",
        title="ベッドの床の枠: 上のレール・柱・下のレールの閉じた枠に一様な荷重 (たわみ角法)",
        domain="strength",
        expression=("w = W / (a1 + L + a2); FEM = w L^2 / 12; overhang moments w a1^2 / 2, w a2^2 / 2; "
                    "M_ij = (2 E I / L)(2 th_i + th_j) + FEM_ij for the upper rail, the posts and the base rail; "
                    "joint equilibrium at the 4 corners (no sway) -> th; sigma = max(M_rail / Z_r, M_post / Z_p + R / A_p)"),
        inputs=inputs,
        outputs={"sigma": ("MPa", "largest bending stress of the rail and the posts (the larger)", None),
                 "sigma_rail": ("MPa", "upper rail's largest bending stress", None),
                 "sigma_post": ("MPa", "post's largest bending plus axial stress", None),
                 "M_rail": ("N*mm", "upper rail's largest moment", None),
                 "M_post": ("N*mm", "post's largest moment", None),
                 "R_head": ("N", "head post's axial load", None), "R_foot": ("N", "foot post's axial load", None)},
        assumptions=["the load is uniform along the upper rail (the weights spread along the deck)",
                     "one side of the bed as a plane frame: the cross members and the other side are left out",
                     "joints rigid, no sway of the posts", "Euler-Bernoulli beams, bending only (the posts' axial "
                     "stress added)"],
        source="slope-deflection method for rigid plane frames (textbook structural analysis)",
        requires=[(lambda v: v["L"] > 0 and v["h"] > 0 and 2 * v["t_r"] < min(v["b_r"], v["h_r"])
                   and 2 * v["t_p"] < v["b_p"] and 2 * v["t_g"] < min(v["b_g"], v["h_g"]),
                   "L and h positive; every tube's wall less than half of its sides")],
        fn=lambda v: _deck_frame({k: v[k] for k in names}),
    )


# ---------------------------------------------------------------- the actuators' levers


def lever_force(M0: float, r: float, a_x: float, a_z: float, theta_max_deg: float, steps: int = THETA_STEPS) -> dict:
    """The actuator force that holds a board raised by theta (0 .. theta_max) about its hinge.

    Plane of the hinge (origin), x along the board at the flat position, z up. The load's moment about the hinge is
    M0 cos(theta) (the board's weight and its load act down at a fixed distance along the board). A lever of length
    r hangs at a right angle to the board (its tip at r (sin theta, -cos theta)); the actuator runs from the anchor
    (-a_x, -a_z) to the tip. Force F = M / d, d the hinge's distance from the actuator's line."""
    th = np.radians(np.linspace(0.0, theta_max_deg, steps))
    tip = np.stack([r * np.sin(th), -r * np.cos(th)], axis=1)
    anchor = np.array([-a_x, -a_z])
    e = tip - anchor
    length = np.linalg.norm(e, axis=1)
    e = e / length[:, None]
    d = np.abs(tip[:, 0] * e[:, 1] - tip[:, 1] * e[:, 0])
    if np.any(d <= 1e-9 * r):
        raise ValueError("the actuator's line passes through the hinge at some angle: it cannot raise the board there")
    F = M0 * np.cos(th) / d
    k = int(np.argmax(F))
    return {"F_max": float(F[k]), "theta_at_max": float(np.degrees(th[k])), "F_flat": float(F[0]),
            "stroke": float(length[-1] - length[0]), "length_flat": float(length[0])}


def _lever_formula() -> Formula:
    return Formula(
        formula_id="bed.lever_actuator_force",
        title="背上げ・膝上げのアクチュエーターの力 (板をちょうつがいのまわりに起こす腕)",
        domain="powertrain",
        expression=("M(theta) = M0 cos(theta); tip = r (sin theta, -cos theta); anchor (-a_x, -a_z); "
                    "d = |tip x e|, e the unit vector anchor -> tip; F = M / d, the largest over 0 .. theta_max; "
                    "stroke = |tip(theta_max) - anchor| - |tip(0) - anchor|"),
        inputs={"M0": _in("N*mm", "moment of the board and its load about the hinge, flat", min=0),
                "r": _in("mm", "lever length (hinge to the actuator's pin, at right angles to the board)", min=0),
                "a_x": _in("mm", "anchor's distance from the hinge along the frame, on the side the actuator pushes from",
                           min=0),
                "a_z": _in("mm", "anchor below the hinge"),
                "theta_max": _in("deg", "largest raise", min=0, max=89)},
        outputs={"F_max": ("N", "largest actuator force over the raise", None),
                 "theta_at_max": ("deg", "raise angle of the largest force", None),
                 "F_flat": ("N", "actuator force at the flat position (start of the raise)", None),
                 "stroke": ("mm", "actuator stroke used for the raise", None),
                 "length_flat": ("mm", "pin-to-pin length at the flat position", None)},
        assumptions=["quasi-static (no acceleration, no friction in the hinge and the pins)",
                     "the load stays where it is on the board as it rises (it turns with the board)",
                     f"scanned at {THETA_STEPS} angles"],
        source="moment balance of a lever about its hinge (textbook statics)",
        requires=[(lambda v: v["r"] > 0 and v["a_x"] > 0, "r and a_x must be positive")],
        fn=lambda v: lever_force(v["M0"], v["r"], v["a_x"], v["a_z"], v["theta_max"]),
    )


# ---------------------------------------------------------------- fatigue over the counts of use

# the welds each use loads: use 1 (getting on and off) and use 4 (the lift) the deck frame, use 2 the back
# bracket's lug root, use 3 the knee bracket's
USE_WELDS = {"frame": ("1", "4"), "lug_back": ("2",), "lug_knee": ("3",)}


def use_damage(v: dict) -> dict:
    """bed.use_damage: Miner damage of each use (n_i cycles of the range dS_i) on the S-N curve FAT, m, N_knee with
    the spectrum's slope 2 m - 1 below the knee; each weld's sum and the worst weld."""
    from ..k1_frame.fatigue import sn_cycles
    from .library_fatigue import spectrum_slope

    m2 = spectrum_slope(v["m"])
    out = {"m2": m2}
    for i in "1234":
        ds, cycles = v[f"dS{i}"], v[f"n{i}"]
        out[f"D{i}"] = 0.0 if ds <= 0 or cycles <= 0 else cycles / sn_cycles(ds, v["FAT"], v["m"], v["N_knee"], m2)
    out["D"] = sum(out[f"D{i}"] for i in "1234")
    for weld, uses in USE_WELDS.items():
        out[f"D_{weld}"] = sum(out[f"D{i}"] for i in uses)
    out["D_worst"] = max(out[f"D_{w}"] for w in USE_WELDS)
    ranges = {w: max(v[f"dS{i}"] for i in uses) for w, uses in USE_WELDS.items()}
    # the worst weld by damage; with no damage anywhere, the weld of the largest range
    worst = max(USE_WELDS, key=lambda w: (out[f"D_{w}"], ranges[w]))
    out["dS_worst"] = ranges[worst]
    return out


# ---------------------------------------------------------------- the side rail pushed outward

GUSSET_STEPS = 4  # the tapered gusset is taken as this many steps, each at its middle's depth


def _tube(D: float, Di: float) -> tuple[float, float, float]:
    """A, I, J of a round tube D outside, Di inside."""
    A = math.pi / 4.0 * (D**2 - Di**2)
    I = math.pi / 64.0 * (D**4 - Di**4)
    return A, I, 2.0 * I


def rail_post_steps(H: float, d: float, t: float, s: float, t_s: float, g: float, t_g: float) -> list[dict]:
    """The post from its socket (z = 0) to the top bar (z = H) in steps of one section each: the post alone
    (d, t); in the sleeve (z < s) one tube from the post's inside to the sleeve's outside; along the gusset
    (z < g) two plates t_g thick flanking the tube, as deep (along the push) as the tube's outside at the root
    and tapering to nothing at z = g, each step at its middle's depth. Bending in the push's plane: I = the tube's
    + 2 t_g h^3 / 12, extreme fibre max(D / 2, h / 2); torsion: J = the tube's + 2 h t_g^3 / 3, the shear taken in
    the tube alone (D / 2 over the tube's J)."""
    cuts = {0.0, H}
    if s > 0:
        cuts.add(s)
    if g > 0:
        cuts.update(g * k / GUSSET_STEPS for k in range(1, GUSSET_STEPS + 1))
    zs = sorted(z for z in cuts if z <= H)
    root = d + 2.0 * t_s if s > 0 else d
    out = []
    for z0, z1 in zip(zs, zs[1:]):
        zm = (z0 + z1) / 2.0
        D, wall = (d + 2.0 * t_s, t + t_s) if zm < s else (d, t)
        A, I, J = _tube(D, D - 2.0 * wall)
        h = root * (1.0 - zm / g) if 0 < zm < g else 0.0
        step = {"z0": z0, "z1": z1, "D": D, "wall": wall, "h": h, "tube_J": J,
                "A": A + 2.0 * t_g * h, "I": I + 2.0 * t_g * h**3 / 12.0, "J": J + 2.0 * h * t_g**3 / 3.0,
                "c": max(D / 2.0, h / 2.0), "tau_per_T": (D / 2.0) / J}
        out.append(step)
    return out


def side_rail_push(v: dict) -> dict:
    """bed.side_rail_push: the top bar (span S between the end posts, n posts at 0, u2, u3, S) as a beam in the
    level plane on the posts: each post a spring along the push (a stepped cantilever H long) and a spring about the
    vertical axis (its torsion); the push F at S / 2. Each post's foot moment R H goes into its socket stub (a
    square tube b_k, wall t_k, along the push) with the post's torque; the post's bending R (H - z) over each step's
    section with the torque's shear."""
    E, G, F, H, S = v["E"], v["G"], v["F"], v["H"], v["S"]
    n = int(round(v["n"]))
    steps = rail_post_steps(H, v["d"], v["t"], v["s"], v["t_s"], v["g"], v["t_g"])
    # each post a stepped cantilever: tip force R (along the push) and tip moment M (in the push's plane) against the
    # tip's deflection w and rotation phi (flexibility by M(z) = R (H - z) + M), and its torsion against the bar's
    # turn theta about the vertical axis
    f_ww = sum(((H - st["z0"]) ** 3 - (H - st["z1"]) ** 3) / (3.0 * E * st["I"]) for st in steps)
    f_wp = sum(((H - st["z0"]) ** 2 - (H - st["z1"]) ** 2) / (2.0 * E * st["I"]) for st in steps)
    f_pp = sum((st["z1"] - st["z0"]) / (E * st["I"]) for st in steps)
    k_t = 1.0 / sum((st["z1"] - st["z0"]) / (G * st["J"]) for st in steps)
    posts = [0.0, S] if n == 2 else [0.0, v["u2"], S] if n == 3 else [0.0, v["u2"], v["u3"], S]
    # the foot turns with the upper rail's twist (k_f: the foot moment R H + M per radian; 0: held rigid): the whole
    # post turns with it
    k_f = [v[f"k_f{i + 1}"] for i in range(n)]
    K_posts = []
    for kf in k_f:
        c = 1.0 / kf if kf > 0 else 0.0
        K_posts.append(np.linalg.inv(np.array([[f_ww + H * H * c, f_wp + H * c], [f_wp + H * c, f_pp + c]])))
    xs = sorted(set(posts) | {S / 2.0})
    _, I_b, J_b = _tube(v["d"], v["d"] - 2.0 * v["t"])
    EI, GJ = E * I_b, G * J_b
    N = len(xs)
    # degrees of freedom per bar node: w (along the push), theta (turn about the vertical axis), phi (the bar's twist,
    # the posts' tip rotation in the push's plane)
    K = np.zeros((3 * N, 3 * N))
    for i in range(N - 1):
        L = xs[i + 1] - xs[i]
        k = EI / L**3 * np.array([[12, 6 * L, -12, 6 * L], [6 * L, 4 * L**2, -6 * L, 2 * L**2],
                                  [-12, -6 * L, 12, -6 * L], [6 * L, 2 * L**2, -6 * L, 4 * L**2]])
        idx = [3 * i, 3 * i + 1, 3 * i + 3, 3 * i + 4]
        K[np.ix_(idx, idx)] += k
        tw = [3 * i + 2, 3 * i + 5]
        K[np.ix_(tw, tw)] += GJ / L * np.array([[1.0, -1.0], [-1.0, 1.0]])
    at = {x: i for i, x in enumerate(xs)}
    for x, Kp in zip(posts, K_posts):
        j = 3 * at[x]
        K[np.ix_([j, j + 2], [j, j + 2])] += Kp
        K[j + 1, j + 1] += k_t
    if np.any(np.abs(np.diag(K)) <= 0):
        raise ValueError("bed.side_rail_push: a bar node is held in no direction")
    f = np.zeros(3 * N)
    f[3 * at[S / 2.0]] = F
    u = np.linalg.solve(K, f)
    # bar moments at the nodes (linear between them): element end moments from the element stiffness
    M_bar = 0.0
    for i in range(N - 1):
        L = xs[i + 1] - xs[i]
        ue = u[[3 * i, 3 * i + 1, 3 * i + 3, 3 * i + 4]]
        m1 = EI / L**2 * (6 * ue[0] + 4 * L * ue[1] - 6 * ue[2] + 2 * L * ue[3])
        m2 = EI / L**2 * (6 * ue[0] + 2 * L * ue[1] - 6 * ue[2] + 4 * L * ue[3])
        M_bar = max(M_bar, abs(m1), abs(m2))
    s_bar = M_bar * (v["d"] / 2.0) / I_b
    bk, tk = v["b_k"], v["t_k"]
    A_k = bk**2 - (bk - 2 * tk) ** 2
    Z_k = (bk**4 - (bk - 2 * tk) ** 4) / 12.0 / (bk / 2.0)
    s_post = s_sock = 0.0
    R = []
    for x, Kp in zip(posts, K_posts):
        j = 3 * at[x]
        Ri, Mi = Kp @ u[[j, j + 2]]
        Ti = k_t * u[j + 1]
        R.append(Ri)
        for st in steps:
            sig = abs(Ri * (H - st["z0"]) + Mi) * st["c"] / st["I"]
            s_post = max(s_post, math.sqrt(sig**2 + 3.0 * (abs(Ti) * st["tau_per_T"]) ** 2))
        s_sock = max(s_sock, abs(Ri) / A_k + (abs(Ri * H + Mi) + abs(Ti)) / Z_k)
    return {"sigma": max(s_post, s_bar, s_sock), "sigma_post": s_post, "sigma_bar": s_bar, "sigma_socket": s_sock,
            "R_max": max(abs(r) for r in R), "k_post": 1.0 / f_ww, "k_end": k_t}


def _side_rail_formula() -> Formula:
    names = ("F", "H", "S", "n", "u2", "u3", "d", "t", "s", "t_s", "g", "t_g", "b_k", "t_k", "E", "G",
             "k_f1", "k_f2", "k_f3", "k_f4")

    def ok(v):
        n = round(v["n"])
        return (abs(v["n"] - n) < 1e-9 and 2 <= n <= 4 and 2 * v["t"] < v["d"] and 0 <= v["s"] < v["H"]
                and 0 <= v["g"] < v["H"] and v["S"] > 0 and 2 * v["t_k"] < v["b_k"] and v["E"] > 0 and v["G"] > 0
                and (n < 3 or 0 < v["u2"] < v["S"]) and (n < 4 or v["u2"] < v["u3"] < v["S"])
                and (v["s"] == 0 or v["t_s"] > 0) and (v["g"] == 0 or v["t_g"] > 0))

    return Formula(
        formula_id="bed.side_rail_push",
        title="サイドレールの上の棒の真ん中を外へ押したときの柱 (差し込みの筒・ガセット)、受け口、棒の曲げ",
        domain="strength",
        expression=("top bar = beam (E I, G J of d, t; bending in the level plane and twist) on n posts at 0, u2, u3, "
                    "S; push F at S / 2; each post a stepped cantilever (tip force R and moment M against deflection and "
                    "rotation: flexibilities sum over the steps of (H - z)^2, (H - z), 1 over E I_step) and a torsion "
                    "spring k_t = 1 / sum(dz / (G J_step)); the foot's turn (R H + M) / k_f adds H^2 / k_f, H / k_f, "
                    "1 / k_f to the flexibilities; stiffness method -> R_i, M_i, T_i; sigma_post = max over posts "
                    "and steps sqrt(((R_i (H - z0) + M_i) c / I)^2 + 3 (T_i tau_per_T)^2); sigma_socket = R / A_k + "
                    "(|R H + M| + |T|) / Z_k; sigma_bar = the bar's largest moment / Z; sigma = the largest"),
        inputs={"F": _in("N", "outward push at the top bar's middle", min=0),
                "H": _in("mm", "post length, socket's centre line to the top bar", min=0),
                "S": _in("mm", "top bar span, end post to end post", min=0),
                "n": _in("1", "posts on the side (2, 3 or 4)", min=2, max=4),
                "u2": _in("mm", "second post from the head-end post (n >= 3; else not used)", min=0),
                "u3": _in("mm", "third post from the head-end post (n = 4; else not used)", min=0),
                "d": _in("mm", "round tube outside diameter", min=0), "t": _in("mm", "tube wall", min=0),
                "s": _in("mm", "sleeve the post is inserted in: its length up from the socket (0: none)", min=0),
                "t_s": _in("mm", "sleeve wall (its inside diameter is d)", min=0),
                "g": _in("mm", "gusset's leg up the post (0: none)", min=0),
                "t_g": _in("mm", "gusset plate thickness (two plates)", min=0),
                "b_k": _in("mm", "socket stub's width (square tube)", min=0),
                "t_k": _in("mm", "socket stub's wall", min=0),
                "E": _in("MPa", "Young's modulus of the tubes", min=0),
                "G": _in("MPa", "shear modulus of the tubes", min=0),
                **{f"k_f{i}": _in("N*mm", f"post {i}'s foot (head to foot): the upper rail's stiffness against its turn in "
                                          "the push's plane, per radian (0: held rigid; not used past n)", min=0)
                   for i in range(1, 5)}},
        outputs={"sigma": ("MPa", "the largest of the posts', the sockets' and the bar's", None),
                 "sigma_post": ("MPa", "posts' von Mises (bending in the push's plane and the torque's shear)", None),
                 "sigma_bar": ("MPa", "top bar's bending", None),
                 "sigma_socket": ("MPa", "socket stubs' (the post's foot moment and torque, and its force along the stub)",
                                  None),
                 "R_max": ("N", "largest post force", None),
                 "k_post": ("N/mm", "each post's stiffness along the push at its top", None),
                 "k_end": ("N*mm", "each post's torsional stiffness at its top, per radian", None)},
        assumptions=["the posts are cantilevers from their sockets; each foot turns with the upper rail's twist "
                     "(k_f, given) and the socket stub itself does not bend",
                     "the top bar is held by the posts along the push, about the vertical axis (the posts' torsion) "
                     "and in its twist (the posts' tips turning in the push's plane, against the bar's torsion)",
                     "in the sleeve the post and the sleeve bend and twist together as one tube (no gap between them)",
                     f"the gusset: two plates flanking the post, as deep as the tube at the root, tapering to nothing at "
                     f"g; taken in {GUSSET_STEPS} steps at each step's middle depth, checked at each step's lower end; "
                     "its shear in torsion taken in the tube alone",
                     "the socket stub: square tube along the push, its stress at its end (no length effect)"],
        source=("cantilever bending, torsion of a round tube (J = 2 I), Euler-Bernoulli beam on springs by the "
                "stiffness method (textbook)"),
        requires=[(ok, "n is 2, 3 or 4 with 0 < u2 < u3 < S as needed; t < d / 2; s, g less than H; t_k < b_k / 2; "
                       "t_s, t_g positive when used; E, G, S positive")],
        fn=lambda v: side_rail_push({k: v[k] for k in names}),
    )


# ---------------------------------------------------------------- everything else


def all_formulas() -> list[Formula]:
    out = [_deck_frame_formula(), _lever_formula()]
    s = {n: sp.Symbol(n, real=True) for n in ("W", "y_cg", "F", "y_F", "y_s")}
    W, ycg, F, yF, ys = (s[k] for k in ("W", "y_cg", "F", "y_F", "y_s"))
    R_far = (W * (ys - ycg) - F * (yF - ys)) / (2 * ys)
    out.append(Formula(
        formula_id="bed.edge_load_tipping",
        title="ベッドの側端に下向きの力をかけたときの、反対側のキャスターに残る力 (倒れ)",
        domain="handling",
        expression="R_far = (W (y_s - y_cg) - F (y_F - y_s)) / (2 y_s); R_near = W + F - R_far",
        inputs={"W": _in("N", "weight of the bed and what is on it (without the edge load)", min=0),
                "y_cg": _in("mm", "centre of gravity's offset toward the loaded side"),
                "F": _in("N", "edge load, straight down", min=0),
                "y_F": _in("mm", "edge load's offset from the centre line, on the loaded side", min=0),
                "y_s": _in("mm", "caster line's offset from the centre line (half the caster track)", min=0)},
        outputs={"R_far": ("N", "load left on the casters of the far side (both together); below 0 the bed tips", R_far),
                 "R_near": ("N", "load on the casters of the loaded side (both together)", W + F - R_far)},
        assumptions=["rigid bed on a level floor; the casters' contact points on two lines along the bed",
                     "the load and the weight act straight down"],
        source="moment balance about the loaded side's caster line (textbook statics)",
        requires=[(ys > 0, "y_s must be positive")],
        symbols=s,
    ))
    out.append(_side_rail_formula())
    s = {n: sp.Symbol(n, real=True) for n in ("W", "y_cg", "F", "h_F", "y_s")}
    W, ycg, F, hF, ys = (s[k] for k in ("W", "y_cg", "F", "h_F", "y_s"))
    R_far = (W * (ys - ycg) - F * hF) / (2 * ys)
    out.append(Formula(
        formula_id="bed.side_push_tipping",
        title="サイドレールを外へ押したときの、反対側のキャスターに残る力 (倒れ)",
        domain="handling",
        expression="R_far = (W (y_s - y_cg) - F h_F) / (2 y_s); R_near = W - R_far",
        inputs={"W": _in("N", "weight of the bed and what is on it", min=0),
                "y_cg": _in("mm", "centre of gravity's offset toward the pushed side"),
                "F": _in("N", "push, level and outward (away from the bed's centre line)", min=0),
                "h_F": _in("mm", "push's height above the floor", min=0),
                "y_s": _in("mm", "caster line's offset from the centre line (half the caster track)", min=0)},
        outputs={"R_far": ("N", "load left on the casters of the far side (both together); below 0 the bed tips", R_far),
                 "R_near": ("N", "load on the casters of the pushed side (both together)", W - R_far)},
        assumptions=["rigid bed on a level floor; the casters' contact points on two lines along the bed",
                     "the casters do not slide: the floor takes the push at the contact points (floor level)"],
        source="moment balance about the pushed side's caster line (textbook statics), as bed.edge_load_tipping",
        requires=[(ys > 0, "y_s must be positive")],
        symbols=s,
    ))
    s = {n: sp.Symbol(n, real=True) for n in ("F", "h_p", "w", "t", "d")}
    F, hp, w, t, d = (s[k] for k in ("F", "h_p", "w", "t", "d"))
    out.append(Formula(
        formula_id="bed.lug_bending",
        title="アクチュエーターの金具 (2 枚の耳) の根元の曲げとピンの面圧",
        domain="strength",
        expression="each lug F / 2 at the pin, h_p below its root: sigma_root = (F / 2) h_p / (t w^2 / 6) = 3 F h_p / (t w^2); "
                   "sigma_bearing = F / (2 d t)",
        inputs={"F": _in("N", "actuator force along the actuator (horizontal at the flat position)", min=0),
                "h_p": _in("mm", "pin centre below the lug's root (the cross member's underside)", min=0),
                "w": _in("mm", "lug width (along the force)", min=0), "t": _in("mm", "lug thickness", min=0),
                "d": _in("mm", "pin diameter", min=0)},
        outputs={"sigma_root": ("MPa", "bending stress at the lug's root", 3 * F * hp / (t * w**2)),
                 "sigma_bearing": ("MPa", "pin bearing stress", F / (2 * d * t))},
        assumptions=["each lug a cantilever from its root, loaded at the pin by half the force",
                     "the force is along the lug's width (the actuator lies along x at the flat position)",
                     "no hole: the section at the root is the full lug"],
        source="cantilever bending, bearing pressure on the projected area (textbook machine design)",
        requires=[(sp.And(w > 0, t > 0, d > 0), "w, t and d must be positive")],
        symbols=s,
    ))
    s = {n: sp.Symbol(n, real=True) for n in ("W", "ratio")}
    out.append(Formula(
        formula_id="bed.lift_actuator_force",
        title="高さのアクチュエーターの力",
        domain="powertrain",
        expression="F = ratio W",
        inputs={"W": _in("N", "weight on the lift (the upper frame, the deck and its load)", min=0),
                "ratio": _in("1", "lift linkage's ratio: actuator force per newton on the lift", min=0)},
        outputs={"F": ("N", "lift actuator force", s["ratio"] * s["W"])},
        assumptions=["the lift linkage's ratio is constant over its travel (the linkage is not modelled)"],
        source="definition of the ratio (arithmetic)",
        symbols=s,
    ))
    s = {n: sp.Symbol(n, real=True) for n in ("n", "k", "s", "v")}
    n, k, st, vel = (s[x] for x in ("n", "k", "s", "v"))
    t_stroke = st / 1000 / vel
    out.append(Formula(
        formula_id="bed.actuator_duty",
        title="アクチュエーターの使う割合 (いちばん忙しい 1 時間)",
        domain="thermal",
        expression="t_stroke = s / v; on_time = n k t_stroke; duty = on_time / 3600 s",
        inputs={"n": _in("1", "uses in the busiest hour", min=0), "k": _in("1", "strokes per use (up and down: 2)", min=0),
                "s": _in("mm", "stroke used", min=0), "v": _in("m/s", "stroke speed", min=0)},
        outputs={"duty": ("%", "share of the hour the actuator runs", 100 * n * k * t_stroke / 3600),
                 "on_time": ("s", "time it runs in the hour", n * k * t_stroke),
                 "run_time": ("s", "longest continuous run (one stroke)", t_stroke)},
        assumptions=["each stroke at the rated speed", "the hour is the busiest one: every use of the day in it"],
        source="time = stroke / speed (arithmetic)",
        requires=[(vel > 0, "v must be positive")],
        symbols=s,
    ))
    out.append(Formula(
        formula_id="bed.use_damage",
        title="使う回数ごとの応力の幅から、溶接の疲労の損傷 (マイナー則。床の枠と 2 つの金具の耳の根元の悪い方)",
        domain="strength",
        expression=("N_i = 2e6 (FAT / dS_i)^m above the knee, N_knee (dS_knee / dS_i)^(2m - 1) below; D_i = n_i / N_i; "
                    "D_frame = D1 + D4, D_lug_back = D2, D_lug_knee = D3, D_worst = the largest; D = sum of the four"),
        inputs={**{f"n{i}": _in("1", f"count of use {i}", min=0) for i in "1234"},
                **{f"dS{i}": _in("MPa", f"stress range of one use {i}", min=0) for i in "1234"},
                "FAT": _in("MPa", "fatigue class: range at 2e6 cycles", min=0), "m": _in("1", "slope", min=0),
                "N_knee": _in("1", "cycles at the knee", min=0)},
        outputs={"D": ("1", "Miner damage of all four uses summed as if at one weld (the safe side)", None),
                 "D_worst": ("1", "damage at the worst weld: the deck frame (uses 1 and 4), the back bracket's lug root "
                                  "(use 2) or the knee bracket's (use 3); failure at 1", None),
                 "D_frame": ("1", "damage at the deck frame (uses 1 and 4)", None),
                 "D_lug_back": ("1", "damage at the back bracket's lug root (use 2)", None),
                 "D_lug_knee": ("1", "damage at the knee bracket's lug root (use 3)", None),
                 "m2": ("1", "slope below the knee used (2 m - 1)", None),
                 "dS_worst": ("MPa", "stress range at the worst weld (its largest use's; with no damage anywhere, the "
                                     "weld of the largest range): what to compare when the damages are next to 0", None),
                 **{f"D{i}": ("1", f"damage of use {i}", None) for i in "1234"}},
        assumptions=["each use is one cycle of its stress range (0 to the loaded stress)",
                     "IIW-type S-N curve (no mean stress correction)", "linear damage accumulation (Miner)",
                     "the uses' ranges differ, so the cycles are a spectrum: below the knee the slope is 2 m - 1 "
                     "(IIW-1823-07 (2008) sec. 4.3.1, p.110; library_fatigue.spectrum_slope), not the constant-"
                     "amplitude slope of the design variable fatigue_slope_after_knee_constant_amplitude"],
        source=("S-N curve N = 2e6 (FAT / range)^m and Miner's rule: A. F. Hobbacher, Recommendations for Fatigue "
                "Design of Welded Joints and Components, 2nd ed., Springer 2016 (IIW), not checked in this repository; "
                "the slope 2 m - 1 below the knee for a spectrum: IIW-1823-07 (2008) sec. 4.3.1, p.110 (read in that "
                "document by the quality department)"),
        requires=[(lambda v: v["FAT"] > 0 and v["m"] > 0 and v["N_knee"] > 0, "FAT, m and N_knee must be positive")],
        primary="D",
        terms={f"D{i}": None for i in "1234"},
        fn=use_damage,
    ))
    return out


__all__ = ["USE_WELDS", "all_formulas", "lever_force", "use_damage"]
