"""K0: the roll bar's clearance to the occupants' space after each loading of the roll-over (ROPS) test (R4).

frame_static.rops_occupant_clearance: for each loading (side, rear, top), the gap between the roll bar and the
occupants' space before the loading, measured along that loading's motion of the bar, minus the deflection the
portal frame formula gives for it:
    c_side = g_side - d_side,  c_rear = g_rear - d_rear,  c_top = g_top - d_top
    rops_occupant_clearance = min(c_side, c_rear, c_top)
The gaps come from rops_inputs.rops_gaps(design) (the travel of the loaded point until a member touches a space of
k4_geometry.occupant_spaces, the bar moving as its top corners are moved), the deflections from
frame_static.portal_frame_side (delta_E), frame_static.portal_frame_rear (delta_E) and frame_static.portal_frame_top
(delta). No fitting: the gap and the deflection are subtracted as they are.

Source: the R4 load case's criterion (hiraku.catalog.builders.analysis.r4_rops cites OECD Code 4, 3.5.2.2,
3.5.3.2 and 3.8, for it; not re-read here); the subtraction is plain geometry (no table values).
"""

from __future__ import annotations

from .registry import Formula

DIRECTIONS = ("side", "rear", "top")
_WORD = {"side": "side loading (portal_frame_side delta_E)", "rear": "rear loading (portal_frame_rear delta_E)",
         "top": "crushing (portal_frame_top delta)"}


def _clearance(v: dict) -> dict:
    c = {d: v[f"g_{d}"] - v[f"d_{d}"] for d in DIRECTIONS}
    worst = min(DIRECTIONS, key=lambda d: c[d])
    return {"rops_occupant_clearance": c[worst], **{f"c_{d}": c[d] for d in DIRECTIONS},
            "governing": float(DIRECTIONS.index(worst) + 1)}


def _rops_clearance() -> Formula:
    gap = ("gap before the {w}, along its motion: the loaded point's travel until the roll bar touches the "
           "occupants' space (rops_inputs.rops_gaps; negative when the bar is in it as built)")
    defl = "deflection of the {w} at its load point (pred.<quantity> of that prediction)"
    return Formula(
        formula_id="frame_static.rops_occupant_clearance",
        title="ロールバーと乗員の空間の隙間を、横・後ろ・上の荷重のそれぞれでたわみの分だけ減らし、いちばん小さいもの",
        domain="strength",
        expression="c_side = g_side - d_side; c_rear = g_rear - d_rear; c_top = g_top - d_top; "
                   "rops_occupant_clearance = min(c_side, c_rear, c_top)",
        inputs={
            **{f"g_{d}": {"unit": "mm", "description": gap.format(w=_WORD[d])} for d in DIRECTIONS},
            **{f"d_{d}": {"unit": "mm", "description": defl.format(w=_WORD[d]), "min": 0} for d in DIRECTIONS},
        },
        outputs={
            "rops_occupant_clearance": ("mm", "smallest clearance over the three loadings (< 0: the bar enters the "
                                              "occupants' space)", None),
            "c_side": ("mm", "clearance after the side loading (g_side - d_side)", None),
            "c_rear": ("mm", "clearance after the rear loading (g_rear - d_rear)", None),
            "c_top": ("mm", "clearance after the crushing (g_top - d_top)", None),
            "governing": ("1", "the loading with the smallest clearance: 1 side, 2 rear, 3 top", None),
        },
        primary="rops_occupant_clearance",
        assumptions=[
            "each loading on its own from the bar as built: the permanent set of one loading is not carried into the "
            "next (OECD Code 4 loads in sequence)",
            "the bar moves as its top corners move, the rest of the nodes staying: the posts turn about their feet as "
            "straight lines, the top beam stays straight between the corners (rops_inputs.rops_gaps); the bending "
            "of the members between their nodes is not seen",
            "the deflection is the portal formula's at its load point (side and rear: at the energy, bilinear; top: "
            "elastic under the crushing force, which means nothing once the beam collapses, portal_frame_top "
            "utilisation >= 1)",
            "the gap is the travel left before contact along the loading's motion, not the shortest distance: where "
            "the bar closes on the space at a slant, or passes beside it (the gap then is the posts' length), the "
            "clearance is larger than the shortest distance; both reach 0 at the same deflection",
        ],
        source=("the R4 load case's criterion (hiraku.catalog.builders.analysis.r4_rops, which cites OECD Code 4, "
                "3.5.2.2, 3.5.3.2 and 3.8: the structure must not enter the clearance zone under each loading; not "
                "re-read for this formula); the subtraction is geometry written here, no table values"),
        fn=_clearance,
    )


def all_formulas() -> list[Formula]:
    return [_rops_clearance()]
