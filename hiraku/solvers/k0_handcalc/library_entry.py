"""K0 formulas for getting in and out of a side seat (test H7, proposed): the clear width beside the seat ahead of
the side frame's front post, the clear height over the opening, the floor's height over the ground, and the count
of the side frame's members (and a net that does not open) in the doorway.

The required sizes the results are judged on (Q93 to Q97) are the intake department's reading of ISO 4252:1983 and
ISO 2867:1980 / 1994 (older editions of standards for agricultural tractors and earth-moving machinery, borrowed for
this vehicle) and of the AIST body sizes (Q95, an assumption); the formulas here only measure.
"""

from __future__ import annotations

import sympy as sp

from .registry import Formula

SPAN = ("the seat's span along x: from the seat box's rear face x_b to the front of the seated person's body parts "
        "x_f (k4_geometry.entry.seat_spaces); the opening is looked for there (an assumption)")


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _width(v: dict) -> dict:
    return {"W": max(0.0, v["x_f"] - max(v["x_b"], v["x_p"] + v["d"] / 2.0))}


def _height(v: dict) -> dict:
    W = _width(v)["W"]
    return {"H": max(0.0, v["h_low"] - v["d"] / 2.0) if W < v["W_req"] else v["H_cap"], "W": W}


def all_formulas() -> list[Formula]:
    n = {k: sp.Symbol(k, real=True) for k in ("n_frame", "n_fixed")}
    s = {k: sp.Symbol(k, real=True) for k in ("z_rail", "h_rail", "gap", "t", "z_g")}
    xs = {"x_f": _in("mm", "front of the seat's span (the person's feet)"),
          "x_b": _in("mm", "rear of the seat's span (the seat box's rear face)"),
          "x_p": _in("mm", "the side frame's front post's centre line (x_b without a side frame)"),
          "d": _in("mm", "the side frame's tube diameter (0 without a side frame)", min=0)}
    return [
        Formula(
            formula_id="entry.opening_width",
            title="座席の横の、座面より上の空きの前後の幅 (横の枠の前の柱から、座った人の足先まで)",
            domain="assembly",
            expression="W = max(0, x_f - max(x_b, x_p + d / 2))",
            inputs=dict(xs),
            outputs={"W": ("mm", "clear width along x beside the seat above the seat top", None)},
            assumptions=[SPAN,
                         "the side frame's bars run from its rear post to its front post x_p and block everything "
                         "behind the front post above the seat top; ahead of it nothing reaches outboard of the seat "
                         "(the tool sees every member and part)"],
            source="plane geometry (intervals along x)",
            fn=_width,
        ),
        Formula(
            formula_id="entry.opening_height",
            title="開口の座面から上の空きの高さ (横の枠の棒がまたぐときは下の棒の下面まで、またがなければ上に何も無い)",
            domain="assembly",
            expression="W = max(0, x_f - max(x_b, x_p + d / 2)); H = max(0, h_low - d / 2) if W < W_req else H_cap",
            inputs={**xs,
                    "h_low": _in("mm", "the side frame's lowest bar's centre line over the seat top (H_cap without "
                                       "a side frame)"),
                    "W_req": _in("mm", "the opening's required width above the seat (Q93)", min=0),
                    "H_cap": _in("mm", "the height reported when nothing is above (assumption)", min=0)},
            outputs={"H": ("mm", "clear height above the seat top over a width W_req", None),
                     "W": ("mm", "the clear width ahead of the front post (entry.opening_width)", None)},
            assumptions=[SPAN,
                         "when the clear stretch ahead of the front post is narrower than W_req, any W_req wide place "
                         "is under the side frame's lowest bar; otherwise nothing is above the opening (no roof, the "
                         "roll bar's top beam behind the seat)"],
            source="plane geometry (intervals along x and z)",
            fn=_height,
        ),
        Formula(
            formula_id="entry.step_height",
            title="地面から床の上面までの高さ (最初に足を置く面、段の無いとき)",
            domain="assembly",
            expression="h = z_rail + h_rail / 2 + gap + t - z_g",
            inputs={"z_rail": _in("mm", "the rails' centre line height (rail_height)"),
                    "h_rail": _in("mm", "the rails' height (rail_h)", min=0),
                    "gap": _in("mm", "the floor's underside over the rails' top (ladder_4w: ON_RAILS_MM)", min=0),
                    "t": _in("mm", "the floor sheet's thickness", min=0),
                    "z_g": _in("mm", "the ground: the lowest tyre bottom (wheel centre less radius)")},
            outputs={"h": ("mm", "floor top over the ground", s["z_rail"] + s["h_rail"] / 2 + s["gap"] + s["t"] - s["z_g"])},
            assumptions=["the floor lies on the rails (its underside gap over their top) and is the first surface a foot "
                         "goes on (no step)", "the vehicle stands on level ground at rest"],
            source="adding heights",
            symbols=s,
        ),
        Formula(
            formula_id="entry.blocked",
            title="開口の形に入る、横の枠の部材と開けられない網の数 (K0 の置き方で)",
            domain="assembly",
            expression="n = n_frame + n_fixed",
            inputs={"n_frame": _in("1", "the side frame's posts and bars in the doorway placed at x0 = max(x_b, x_p "
                                        "+ d / 2), kept within the span", min=0),
                    "n_fixed": _in("1", "nets of kind side_net that do not open (not held by clips alone) in it",
                                   min=0)},
            outputs={"n": ("1", "fixed things in the doorway", n["n_frame"] + n["n_fixed"])},
            assumptions=[SPAN,
                         "the doorway: Q93 wide from the seat top up to max(seat top + Q95, floor + Q96), and Q94 wide "
                         "from the floor up to the seat top at its front end; placed just ahead of the side frame's "
                         "front post (the tool tries every place and sees every member and part)",
                         "only the side frame (its round posts and bars) and the nets are seen"],
            source="counting (entry_inputs.entry_blocked_inputs counts the members)",
            symbols=n,
        ),
        Formula(
            formula_id="entry.handholds",
            title="乗り降りの手がかりの数 (握れる太さの丸い部材で、座席の横の範囲にあるもの)",
            domain="assembly",
            expression="n = n_grip",
            inputs={"n_grip": _in("1", "round members of outside diameter Q49 to Q89 on the seat's side, outboard of "
                                       "the seat's middle, meeting the seat's span along x and reaching below the floor "
                                       "+ Q48", min=0)},
            outputs={"n": ("1", "hand holds beside the seat (the fewest over the side seats)", sp.Symbol("n_grip", real=True))},
            assumptions=[SPAN, "members only, each as its end points' box widened by its diameter (the tool also counts "
                               "parts of kind grab_handle and rectangular tubes)"],
            source="counting (entry_inputs.entry_handhold_inputs counts the members)",
        ),
    ]
