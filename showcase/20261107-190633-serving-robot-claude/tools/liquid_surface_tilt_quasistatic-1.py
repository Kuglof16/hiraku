import math

from hiraku.solvers.shelf.base import BuiltinTool, LoadCaseError, Param, Predicts, check_record, param_text, param_value, result


def surface_tilt_deg(ax_g, ay_g):
    """Quasi-static tilt [deg] of a free liquid surface in a container under a steady horizontal
    acceleration (ax, ay in g): the surface stays normal to the apparent gravity, tan(theta) = |a_h| / g."""
    return math.degrees(math.atan(math.hypot(ax_g, ay_g)))


class LiquidSurfaceTilt(BuiltinTool):
    name = "liquid_surface_tilt_quasistatic"
    title = "トレーの飲み物の液面の傾き (準静的: 水平の加速度と重力の合力に液面が直交する)"
    domain = "aero"
    base = "K0"
    tests = ["F3"]
    formulas = ["tilt.slide_angle"]
    predicts = (Predicts("liquid_surface_tilt", "tilt.slide_angle", "theta",
                         note="tan(theta) = mu has the same form as tan(theta) = a_h / g: give mu the horizontal "
                              "acceleration in g as a ratio. One direction per prediction (where braking or "
                              "turning); braking_and_turning needs the vector sum of the two"),)
    load_case_params = (
        Param("a_x", "g", "fore-aft acceleration of the tray (braking or launch)", default=0.0),
        Param("a_y", "g", "sideways acceleration of the tray (turning)", default=0.0),
        Param("combine", None, "both: braking and turning at once is also a case; none: each alone", default="both"),
    )

    def known_answers(self):
        def run(ax, ay, combine):
            lc = self.declared({"load_case_id": "check", "params": {"a_x": {"value": ax, "unit": "g"},
                                                                   "a_y": {"value": ay, "unit": "g"},
                                                                   "combine": combine}})
            res = self.analyse({}, lc, {})["results"]
            return next(r["value"] for r in res if r["quantity"] == "liquid_surface_tilt" and r.get("where") is None)
        src = ("closed form for a liquid in a container under uniform horizontal acceleration a: "
               "tan(theta) = a / g (rigid-body motion of a fluid, e.g. F. M. White, Fluid Mechanics, "
               "section 'rigid-body motion: uniform linear acceleration')")
        return [
            check_record("1 g fore-aft, no turning", "liquid_surface_tilt", 45.0, run(1.0, 0.0, "both"), "deg",
                         1e-6, src),
            check_record("0.6 g braking with 0.8 g turning at once (resultant 1 g)", "liquid_surface_tilt", 45.0,
                         run(0.6, 0.8, "both"), "deg", 1e-6, src + "; the horizontal components add as a vector"),
            check_record("no acceleration", "liquid_surface_tilt_level", 0.0, run(0.0, 0.0, "none"), "deg",
                         1e-6, "a liquid at rest has a level surface (hydrostatics)"),
        ]

    def analyse(self, design, load_case, params):
        ax = abs(param_value(load_case, "a_x", "g"))
        ay = abs(param_value(load_case, "a_y", "g"))
        combine = param_text(load_case, "combine")
        if combine not in ("both", "none"):
            raise LoadCaseError(f"combine {combine!r} is not both or none")
        cases = {"braking": surface_tilt_deg(ax, 0.0), "turning": surface_tilt_deg(0.0, ay)}
        if combine == "both":
            cases["braking_and_turning"] = surface_tilt_deg(ax, ay)
        worst = max(cases, key=cases.get)
        out = [result("liquid_surface_tilt", cases[worst], "deg")]
        out += [result("liquid_surface_tilt", v, "deg", where=k) for k, v in cases.items()]
        return {"results": out,
                "assumptions": [
                    "quasi-static: the liquid surface stays normal to the sum of gravity and the steady horizontal "
                    "acceleration (tan theta = a_h / g); sloshing (a dynamic overshoot) is not included",
                    "the tray moves with the body without pitch or roll (the wheels and the frame taken as rigid)",
                    "the worst of braking alone, turning alone and (combine both) braking while turning is reported"]}
