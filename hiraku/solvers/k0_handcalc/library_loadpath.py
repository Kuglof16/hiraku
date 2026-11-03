"""K0: the load path from a control (or a part) to the frame, by hand.

control_deflection: a control held to the frame through a chain of touching parts. Each contact is a spring;
its translation stiffness in series gives k_t, and its rotation stiffness, seen at the load point through the
lever from the contact to that point, gives k_r (k_rot / r^2 for each contact, in series). The frame member
the chain lands on is a cantilever of length L from its nearest end node (the rest of the frame taken as
rigid). Parts are rigid. The readers that build these inputs from a design are in
hiraku.catalog.builders.load_path (control_inputs).

free_parts: parts that no chain of contacts joins to a frame member.
"""

from __future__ import annotations

import sympy as sp

from .registry import Formula


def _control_deflection() -> Formula:
    F, k_t, k_r, L, E, I = sp.symbols("F k_t k_r L E I", real=True)
    joints_t, joints_r, beam = F / k_t, F / k_r, F * L ** 3 / (3 * E * I)
    return Formula(
        formula_id="load_path.control_deflection",
        title="操作部の握る点 (踏む面) の動き: 触れている所のばねの直列と、着く部材の片持ち",
        domain="strength",
        expression="delta = F / k_t + F / k_r + F L^3 / (3 E I)",
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
        },
        outputs={"delta": ("mm", "the load point's movement", joints_t + joints_r + beam)},
        terms={"joints_translation": joints_t, "joints_rotation": joints_r, "frame_cantilever": beam},
        assumptions=[
            "部品は剛体。触れている所だけがばね",
            "握る点から枠までは、触れている所のいちばん短い鎖 1 本 (並んだ別の道は入れない: 予測は柔らかめ)",
            "回る向きのばねは、触れた所から荷重の点までの腕の長さで、点の動きに直す",
            "着いた部材は、いちばん近い節から片持ち。フレームの残りは硬いとみる",
        ],
        source="springs in series and a cantilever with an end load, delta = P L^3 / (3 E I) (textbook statics)",
        requires=[(sp.And(k_t > 0, k_r > 0, E > 0, I > 0), "the stiffnesses, E and I must be positive")],
        symbols={"F": F, "k_t": k_t, "k_r": k_r, "L": L, "E": E, "I": I},
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
    return [_control_deflection(), _free_parts()]
