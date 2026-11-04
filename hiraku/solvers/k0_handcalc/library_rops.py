"""K0: the roll bar's clearance to the occupants' space after each loading of the roll-over (ROPS) test (R4).

frame_static.rops_occupant_clearance: for each loading (side, rear, top), the shortest distance between the roll bar
and the occupants' space with the bar moved by that loading's motion at the deflection the portal formula gives
(s_side, s_rear, s_top, measured by rops_inputs.clearances_at at the deflections d0_side, d0_rear, d0_top), and the
smallest of the three:
    rops_occupant_clearance = min(s_side, s_rear, s_top)
The deflections come in twice: as d_* from the portal formulas' predictions (frame_static.portal_frame_side
delta_E, frame_static.portal_frame_rear delta_E, frame_static.portal_frame_top delta), and as d0_*, the ones the
distances were measured at (rops_inputs.portal_deflections, the same functions on the same inputs); they must be
the same (requires). The travel left along each motion, t_* = g_* - d_* (g_* the travel to contact before the
loading, rops_inputs.rops_gaps), is given beside it. No fitting: the distances and the deflections are as computed.

The shortest distance is the criterion's quantity (the R4 load case: the smallest gap between the roll bar's members
and the space, k4_geometry.occupant_clearance); the travel left is a different measure that reaches 0 at the same
deflection but is longer where the bar closes at a slant or passes beside the space.

Source: the R4 load case's criterion (hiraku.catalog.builders.analysis.r4_rops cites OECD Code 4, 3.5.2.2,
3.5.3.2 and 3.8, for it; not re-read here); the rest is geometry written here (no table values).
"""

from __future__ import annotations

from .registry import Formula

DIRECTIONS = ("side", "rear", "top")
_WORD = {"side": "side loading (portal_frame_side delta_E)", "rear": "rear loading (portal_frame_rear delta_E)",
         "top": "crushing (portal_frame_top delta)"}
SAME_REL = 1e-6  # d_* and d0_* agree within this (relative, at least 1e-6 mm)


def _same(v: dict) -> bool:
    return all(abs(v[f"d_{d}"] - v[f"d0_{d}"]) <= SAME_REL * max(1.0, abs(v[f"d0_{d}"])) for d in DIRECTIONS)


def _clearance(v: dict) -> dict:
    c = {d: v[f"s_{d}"] for d in DIRECTIONS}
    worst = min(DIRECTIONS, key=lambda d: c[d])
    return {"rops_occupant_clearance": c[worst], **{f"c_{d}": c[d] for d in DIRECTIONS},
            **{f"t_{d}": v[f"g_{d}"] - v[f"d_{d}"] for d in DIRECTIONS},
            "governing": float(DIRECTIONS.index(worst) + 1)}


def _rops_clearance() -> Formula:
    gap = ("travel before the {w}, along its motion: the loaded point's travel until the roll bar touches the "
           "occupants' space (rops_inputs.rops_gaps; negative when the bar is in it as built)")
    defl = "deflection of the {w} at its load point (pred.<quantity> of that prediction)"
    dist = ("shortest distance between the roll bar and the occupants' space with the bar moved by the {w}'s motion "
            "at d0 (rops_inputs.clearances_at; negative: inside)")
    at = "deflection of the {w} the shortest distance was measured at (rops_inputs.portal_deflections)"
    return Formula(
        formula_id="frame_static.rops_occupant_clearance",
        title="横・後ろ・上の荷重のそれぞれで、たわんだロールバーと乗員の空間のいちばん近い距離を測り、いちばん小さいもの",
        domain="strength",
        expression="c_side = s_side; c_rear = s_rear; c_top = s_top; rops_occupant_clearance = min(c_side, c_rear, "
                   "c_top); t_d = g_d - d_d (travel left); requires d_d = d0_d",
        inputs={
            **{f"g_{d}": {"unit": "mm", "description": gap.format(w=_WORD[d])} for d in DIRECTIONS},
            **{f"d_{d}": {"unit": "mm", "description": defl.format(w=_WORD[d]), "min": 0} for d in DIRECTIONS},
            **{f"s_{d}": {"unit": "mm", "description": dist.format(w=_WORD[d])} for d in DIRECTIONS},
            **{f"d0_{d}": {"unit": "mm", "description": at.format(w=_WORD[d]), "min": 0} for d in DIRECTIONS},
        },
        outputs={
            "rops_occupant_clearance": ("mm", "smallest clearance over the three loadings (< 0: the bar enters the "
                                              "occupants' space)", None),
            "c_side": ("mm", "shortest distance after the side loading (s_side)", None),
            "c_rear": ("mm", "shortest distance after the rear loading (s_rear)", None),
            "c_top": ("mm", "shortest distance after the crushing (s_top)", None),
            "t_side": ("mm", "travel left along the side motion (g_side - d_side)", None),
            "t_rear": ("mm", "travel left along the rear motion (g_rear - d_rear)", None),
            "t_top": ("mm", "travel left along the crushing motion (g_top - d_top)", None),
            "governing": ("1", "the loading with the smallest clearance: 1 side, 2 rear, 3 top", None),
        },
        primary="rops_occupant_clearance",
        assumptions=[
            "each loading on its own from the bar as built: the permanent set of one loading is not carried into the "
            "next (OECD Code 4 loads in sequence)",
            "the bar moves as its top corners move, the rest of the nodes staying: the posts turn about their feet as "
            "straight lines (length kept), the top beam stays straight between the corners (rops_inputs); the "
            "bending of the members between their nodes and the feet's own motion are not seen",
            "the rear motion's corner shares are the portal_frame_rear formula's at the rear energy; both senses of "
            "each motion are tried (the smaller distance is kept)",
            "the deflection is the portal formula's at its load point (side and rear: at the energy; top: at the "
            "crushing force, the posts' length when the frame collapses below it); a deflection past the end of a "
            "motion is taken at its end",
        ],
        source=("the R4 load case's criterion (hiraku.catalog.builders.analysis.r4_rops, which cites OECD Code 4, "
                "3.5.2.2, 3.5.3.2 and 3.8: the structure must not enter the clearance zone under each loading; not "
                "re-read for this formula); the distances are k4_geometry's capsule-box distance; no table values"),
        requires=[(_same, "s_* were measured at d0_*: d_* (the portal predictions) must be those deflections "
                          "(rops_inputs.rops_inputs with the load case's energies and force)")],
        fn=_clearance,
    )


def all_formulas() -> list[Formula]:
    return [_rops_clearance()]
