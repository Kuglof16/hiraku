"""K0: the load path from a control (or a part) to the frame, by hand.

control_deflection: a control held to the frame through a chain of touching parts. Each contact is a spring;
its translation stiffness in series gives k_t, and its rotation stiffness, seen at the load point through the
lever from the contact to that point, gives k_r (k_rot / r^2 for each contact, in series). The frame member
the chain lands on is a cantilever of length L from its nearest end node (the rest of the frame taken as
rigid; a member whose nearest end is joined to nothing else is a cantilever from its other end). Parts are rigid,
but a plate the chain passes through bends: plate_strip, the strip from where the load comes on to the plate's centre
(clamped there) in bending and twist, as the load_path tool models a plate (a star of strips from its centre),
worked out in closed form. The readers that build these inputs from a design are in
hiraku.catalog.builders.load_path (chain_inputs).

free_parts: parts that no chain of contacts joins to a frame member.
"""

from __future__ import annotations

import sympy as sp

from .registry import Formula


def _strip_integrals(L, w0, w1, c):
    """K_k = integral over s from 0 to L of s^k / w(s) ds (k = 0, 1, 2) for a strip whose width grows from w0 at
    s = 0 by c per mm up to w1 at s1 = (w1 - w0) / c, then stays w1 to s = L."""
    s1 = (w1 - w0) / c
    r = sp.log(w1 / w0)
    wedge = [r / c, s1 / c - w0 / c ** 2 * r, ((w1 ** 2 - w0 ** 2) / 2 - 2 * w0 * (w1 - w0) + w0 ** 2 * r) / c ** 3]
    return [wedge[k] + (L ** (k + 1) - s1 ** (k + 1)) / ((k + 1) * w1) for k in range(3)]


def _plate_strip() -> Formula:
    """The plate strip from where a load comes onto a plate to the plate's centre, as a cantilever clamped at the
    centre: the load point moves by the strip end's deflection across the plate and by its rotations (bending
    across the strip and twist about it) times the lever from the strip end to the load point."""
    F_n, F_a, F_c, r_n, r_a, r_c, L, w0, w1, t, E, G, c = sp.symbols(
        "F_n F_a F_c r_n r_a r_c L w_0 w_1 t E G c", real=True)
    K0, K1, K2 = _strip_integrals(L, w0, w1, c)
    m_c = r_n * F_a - r_a * F_n  # the moment at the strip's end about the axis across the strip (c = n x a)
    m_a = r_c * F_n - r_n * F_c  # the torque about the strip's axis
    bend = 12 / (E * t ** 3)  # 1 / (E I) per unit width: I = w t^3 / 12
    twist = 3 / (G * t ** 3)  # 1 / (G J) per unit width: J = w t^3 / 3 (a thin strip)
    d_n = bend * (m_c * K1 + F_n * K2)
    th_c = bend * (m_c * K0 + F_n * K1)
    th_a = twist * m_a * K0
    w = d_n - th_c * r_a + th_a * r_c  # across the plate at the load point
    u_a, u_c = th_c * r_n, -th_a * r_n  # in the plate's plane (the lever across the plate turning)
    delta = sp.sqrt(w ** 2 + u_a ** 2 + u_c ** 2)
    names = {"F_n": F_n, "F_a": F_a, "F_c": F_c, "r_n": r_n, "r_a": r_a, "r_c": r_c, "L": L, "w_0": w0, "w_1": w1,
             "t": t, "E": E, "G": G, "c": c}
    return Formula(
        formula_id="load_path.plate_strip",
        title="板の上の点の荷重: 板の中心から荷重の来る所までの板の帯 (中心で固定した片持ち) の曲げとねじれ",
        domain="strength",
        expression=("w(s) = min(w_0 + c s, w_1); K_k = int_0^L s^k / w(s) ds; m_c = r_n F_a - r_a F_n; "
                    "m_a = r_c F_n - r_n F_c; d_n = 12 (m_c K_1 + F_n K_2) / (E t^3); "
                    "th_c = 12 (m_c K_0 + F_n K_1) / (E t^3); th_a = 3 m_a K_0 / (G t^3); "
                    "w = d_n - th_c r_a + th_a r_c; delta = sqrt(w^2 + (th_c r_n)^2 + (th_a r_n)^2)"),
        inputs={
            "F_n": {"unit": "N", "description": "the load across the plate (along its normal n)"},
            "F_a": {"unit": "N", "description": "the load along the strip (a: from where the load comes on toward the "
                                              "plate's centre)"},
            "F_c": {"unit": "N", "description": "the load across the strip in the plate's plane (c = n x a)"},
            "r_n": {"unit": "mm", "description": "the load point off the strip's end, along n"},
            "r_a": {"unit": "mm", "description": "the load point off the strip's end, along a"},
            "r_c": {"unit": "mm", "description": "the load point off the strip's end, along c"},
            "L": {"unit": "mm", "description": "the strip's length: from where the load comes on to the plate's centre",
                  "min": 0},
            "w_0": {"unit": "mm", "description": "the strip's width where the load comes on (the contact's width)",
                    "min": 0},
            "w_1": {"unit": "mm", "description": "its width at the centre: w_0 + c L, at most the plate's width across "
                                                "the strip", "min": 0},
            "t": {"unit": "mm", "description": "the plate's thickness", "min": 0},
            "E": {"unit": "MPa", "description": "the plate's Young's modulus", "min": 0},
            "G": {"unit": "MPa", "description": "the plate's shear modulus", "min": 0},
            "c": {"unit": "1", "description": "the width's growth per length (2 tan of the spread angle)", "min": 0},
        },
        outputs={"delta": ("mm", "the load point's movement from the strip", delta),
                 "w": ("mm", "its movement across the plate", w),
                 "theta_bend": ("rad", "the strip end's rotation about the axis across the strip", th_c),
                 "theta_twist": ("rad", "the strip end's twist about the strip's axis", th_a)},
        assumptions=[
            "板の中心は動かず回らない (板のほかの触れた所が中心を押さえる)。帯は中心で固定した片持ち。"
            "そのため、中心が動いて回る板では、この式は小さめに出る (v1 でブレーキを隔壁に寄せた形: 中心が 1.9 mm 動き "
            "0.02 rad 回り、帯の端で約 5.4 mm の分が入らず、道具より 30 % 小さい)",
            "帯の幅は荷重の来る所で触れた面の幅 w_0、中心へ c (= 2 tan 45°) で広がり、板の幅 w_1 まで (道具と同じ考え)",
            "曲げは I = w t³/12、ねじれは薄い帯の J = w t³/3。板の面内の曲げ・伸びは硬いとみて入れない",
            "荷重の点は帯の端に剛につながる (ペダルと台は剛体)。小さな変形の線形",
        ],
        source=("a cantilever with an end force and an end moment by the unit-load method, delta = int M m / (E I) ds, "
                "theta = int M / (E I) ds, twist = int T / (G J) ds (textbook statics); "
                "int s^k / (w_0 + c s) ds in closed form"),
        requires=[(sp.And(w0 > 0, w1 >= w0, c > 0, t > 0, E > 0, G > 0), "the widths, t, E, G and c must be positive "
                   "and w_1 at least w_0"),
                  (w1 - w0 <= c * L * (1 + sp.Rational(1, 10 ** 9)) + sp.Rational(1, 10 ** 9),
                   "w_1 must be reached within L (w_1 <= w_0 + c L)")],
        symbols=names,
    )


def _post_node() -> Formula:
    """A node partway up a straight column whose two far ends are clamped (the rest of the frame rigid), loaded
    by a force and a moment there: its movement and turn, read at the load point along the force, by the work
    the load does (F . u + M . theta = F delta)."""
    F, N, T, P1, M1, P2, M2, a, b, E, G, A, I1, I2, J = sp.symbols(
        "F N T P_1 M_1 P_2 M_2 a b E G A I_1 I_2 J", real=True)

    def bend(P, M, I):  # q^T K^-1 q of one bending plane: w and its slope at the node, both segments clamped
        k11 = 12 * E * I * (1 / a ** 3 + 1 / b ** 3)
        k12 = 6 * E * I * (1 / b ** 2 - 1 / a ** 2)
        k22 = 4 * E * I * (1 / a + 1 / b)
        return (k22 * P ** 2 - 2 * k12 * P * M + k11 * M ** 2) / (k11 * k22 - k12 ** 2)

    axial = N ** 2 / (E * A * (1 / a + 1 / b))
    twist = T ** 2 / (G * J * (1 / a + 1 / b))
    b1, b2 = bend(P1, M1, I1), bend(P2, M2, I2)
    delta = (axial + twist + b1 + b2) / F
    names = {"F": F, "N": N, "T": T, "P_1": P1, "M_1": M1, "P_2": P2, "M_2": M2, "a": a, "b": b, "E": E, "G": G,
             "A": A, "I_1": I1, "I_2": I2, "J": J}
    return Formula(
        formula_id="load_path.post_node",
        title="柱の途中の節の動き: 両端を固定した柱 (2 本の区間) に、節で力とモーメントが掛かるとき、荷重の点の力の向きの動き",
        domain="strength",
        expression=("delta = (N^2 / (E A (1/a + 1/b)) + T^2 / (G J (1/a + 1/b)) + sum_i q_i^T K_i^-1 q_i) / F; "
                    "q_i = (P_i, M_i); K_i = [[12 E I_i (1/a^3 + 1/b^3), 6 E I_i (1/b^2 - 1/a^2)], "
                    "[6 E I_i (1/b^2 - 1/a^2), 4 E I_i (1/a + 1/b)]]"),
        inputs={
            "F": {"unit": "N", "description": "the load's size", "min": 0},
            "N": {"unit": "N", "description": "its part along the column (e)"},
            "T": {"unit": "N*mm", "description": "its moment about the column's axis at the node"},
            "P_1": {"unit": "N", "description": "its part across the column along u_1"},
            "M_1": {"unit": "N*mm", "description": "its moment at the node about e x u_1"},
            "P_2": {"unit": "N", "description": "its part across the column along u_2 = e x u_1"},
            "M_2": {"unit": "N*mm", "description": "its moment at the node about e x u_2"},
            "a": {"unit": "mm", "description": "the column's length from the node to one clamped end", "min": 0},
            "b": {"unit": "mm", "description": "and to the other", "min": 0},
            "E": {"unit": "MPa", "description": "the column's Young's modulus", "min": 0},
            "G": {"unit": "MPa", "description": "its shear modulus", "min": 0},
            "A": {"unit": "mm^2", "description": "its section's area", "min": 0},
            "I_1": {"unit": "mm^4", "description": "its second moment of area for bending along u_1", "min": 0},
            "I_2": {"unit": "mm^4", "description": "for bending along u_2", "min": 0},
            "J": {"unit": "mm^4", "description": "its torsion constant", "min": 0},
        },
        outputs={"delta": ("mm", "the load point's movement along the load from the column", delta)},
        terms={"axial": axial / F, "twist": twist / F, "bending_1": b1 / F, "bending_2": b2 / F},
        assumptions=[
            "柱の両端 (節から a と b の所) は固定 (そこから先の枠は硬いとみる)。そのため、枠が動く所では小さめに出る",
            "柱はまっすぐな 1 本の断面 (2 つの区間は同じ断面)。節から荷重の点までは剛 (その間の部材の曲げは別の項)",
            "小さな変形の線形。荷重の点の力の向きの動きは、荷重のする仕事 (F・u + M・θ = F δ) から出す",
        ],
        source=("the beam element's end stiffness 12 E I / l^3, 6 E I / l^2, 4 E I / l for a segment clamped at its "
                "far end, two segments summed at the node; torsion G J / l and axial E A / l (textbook statics); "
                "Clapeyron's theorem for the movement along the load"),
        requires=[(sp.And(F > 0, a > 0, b > 0, E > 0, G > 0, A > 0, I1 > 0, I2 > 0, J > 0),
                   "F, the lengths and the section's values must be positive")],
        symbols=names,
    )


def _control_deflection() -> Formula:
    F, k_t, k_r, L, E, I, d_p = sp.symbols("F k_t k_r L E I delta_plate", real=True)
    G, J, r_t, d_r = sp.symbols("G J r_t delta_root", real=True)
    joints_t, joints_r, beam = F / k_t, F / k_r, F * L ** 3 / (3 * E * I)
    twist = F * r_t ** 2 * L / (G * J)
    return Formula(
        formula_id="load_path.control_deflection",
        title="操作部の握る点 (踏む面) の動き: 触れている所のばねの直列と、通る板の帯と、着く部材の片持ちの曲げとねじれと、その根元",
        domain="strength",
        expression="delta = F / k_t + F / k_r + delta_plate + F L^3 / (3 E I) + F r_t^2 L / (G J) + delta_root",
        inputs={
            "F": {"unit": "N", "description": "the hand or foot force at the control", "min": 0},
            "k_t": {"unit": "N/mm", "description": "the contacts' translation stiffness in series along the chain",
                    "min": 0},
            "k_r": {"unit": "N/mm", "description": "the contacts' rotation stiffness seen at the load point "
                                                   "(k_rot / r^2 for each, in series)", "min": 0},
            "L": {"unit": "mm", "description": "the frame member's length from the landing point to its nearest node",
                  "min": 0},
            "E": {"unit": "MPa", "description": "Young's modulus of that member", "min": 0},
            "I": {"unit": "mm^4", "description": "its smaller second moment of area", "min": 0},
            "delta_plate": {"unit": "mm", "description": "the load point's movement from the plates the chain passes "
                                                         "through (load_path.plate_strip, summed; 0 without plates)",
                            "min": 0},
            "G": {"unit": "MPa", "description": "the shear modulus of the member the chain lands on", "min": 0},
            "J": {"unit": "mm^4", "description": "its torsion constant", "min": 0},
            "r_t": {"unit": "mm", "description": "the load's lever about that member's axis at the landing point (its "
                                                 "moment about the axis over F)"},
            "delta_root": {"unit": "mm", "description": "the load point's movement from the node the cantilever "
                                                        "starts at, when that node is partway up a straight column "
                                                        "(load_path.post_node; 0 when the node is taken as rigid)",
                           "min": 0},
        },
        outputs={"delta": ("mm", "the load point's movement", joints_t + joints_r + d_p + beam + twist + d_r)},
        terms={"joints_translation": joints_t, "joints_rotation": joints_r, "plate_strips": d_p, "frame_cantilever": beam,
               "frame_twist": twist, "root_on_column": d_r},
        assumptions=[
            "部品は剛体。触れている所だけがばね",
            "握る点から枠までは、触れている所のいちばん短い鎖 1 本 (並んだ別の道は入れない: 予測は柔らかめ)",
            "回る向きのばねは、触れた所から荷重の点までの腕の長さで、点の動きに直す",
            "鎖が板を通るときは、荷重の来る所から板の中心までの板の帯の曲げとねじれ (load_path.plate_strip、中心は固定)",
            "着いた部材は、いちばん近い節から片持ち (その節に部材がほかに無ければ、もう一方の端から)。曲げは小さい方の I、"
            "ねじれは荷重の部材の軸まわりのモーメント F r_t を着いた所から根元までの長さで受ける",
            "根元の節がまっすぐな柱の途中にあれば、柱の両端を固定とみて、その節の動きを足す (load_path.post_node)。"
            "それより先のフレームは硬いとみる (柱の根元の枠が動く分は入らない: 予測は小さめ)",
        ],
        source=("springs in series and a cantilever with an end load, delta = P L^3 / (3 E I), and its twist, "
                "theta = T L / (G J), read along the load as F r_t^2 L / (G J) (textbook statics)"),
        requires=[(sp.And(k_t > 0, k_r > 0, E > 0, I > 0, G > 0, J > 0),
                   "the stiffnesses, E, I, G and J must be positive")],
        symbols={"F": F, "k_t": k_t, "k_r": k_r, "L": L, "E": E, "I": I, "delta_plate": d_p, "G": G, "J": J,
                 "r_t": r_t, "delta_root": d_r},
    )


def _free_parts() -> Formula:
    n_parts, n_held = sp.symbols("n_parts n_held", real=True)
    return Formula(
        formula_id="load_path.free_parts",
        title="どこにも繋がらない部品の数 (触れている所を部材からたどる)",
        domain="strength",
        expression="n = n_parts - n_held",
        inputs={"n_parts": {"unit": "1", "description": "parts modelled as rigid bodies", "min": 0},
                "n_held": {"unit": "1", "description": "those a chain of contacts joins to a frame member", "min": 0}},
        outputs={"n": ("1", "parts joined to nothing that holds them", n_parts - n_held)},
        assumptions=["触れている所はどれも 6 方向に硬い (1 か所で触れれば、その部品は止まる)"],
        source="graph search over the contacts (counting)",
        requires=[(n_held <= n_parts, "held parts cannot outnumber the parts")],
        symbols={"n_parts": n_parts, "n_held": n_held},
    )


def all_formulas() -> list[Formula]:
    return [_control_deflection(), _plate_strip(), _post_node(), _free_parts()]
