from hiraku.solvers.shelf.base import (BuiltinTool, LOADING, LoadCaseError, Param, Predicts, check_record,
                                       param_value, result)


def _design_var(design, names, unit):
    """A design variable by the first of `names` found in design['variables'] (a number or {value, unit})."""
    variables = design.get("variables") or {}
    for n in names:
        if n in variables:
            v = variables[n]
            if isinstance(v, dict):
                if v.get("unit") not in (None, unit):
                    raise LoadCaseError(f"design variable {n} is in {v.get('unit')}, expected {unit}")
                return float(v["value"]), n
            return float(v), n
    return None, None


class DragForce(BuiltinTool):
    name = "drag_force_steady"
    title = "一定の速さで受ける空気抵抗の力と、それに使う動力 (無風)"
    domain = "aero"
    base = "K0"
    tests = ["F2"]
    formulas = ["drag.force"]
    predicts = (Predicts("drag_force", "drag.force", "F"),)
    load_case_params = (
        LOADING,
        Param("speed", "km/h", "vehicle speed through still air", default=40.0),
        Param("cd", "1", "drag coefficient (else the design variable drag_coefficient)"),
        Param("front_area", "m^2", "frontal area (else the design variable frontal_area)"),
        Param("rho", "kg/m^3", "air density (else the design variable air_density)"),
    )

    def known_answers(self):
        design = {"variables": {"drag_coefficient": {"value": 1.0, "unit": "1"},
                                "frontal_area": {"value": 1.0, "unit": "m^2"},
                                "air_density": {"value": 1.2, "unit": "kg/m^3"}}}
        lc = self.declared({"load_case_id": "check", "params": {"speed": {"value": 36.0, "unit": "km/h"}}})
        res = {r["quantity"]: r["value"] for r in self.analyse(design, lc, {})["results"]}
        return [
            check_record("C_d 1, A 1 m^2, rho 1.2 kg/m^3, 36 km/h (10 m/s), still air", "drag_force", 60.0,
                         res.get("drag_force"), "N", 1e-9,
                         "closed form F = 1/2 rho C_d A v^2 (textbook fluid mechanics, dynamic pressure "
                         "1/2 rho v^2 = 60 Pa at 10 m/s), worked out by hand",
                         "C_d 1、A 1 m²、空気の密度 1.2 kg/m³、36 km/h (10 m/s)、無風",
                         "閉じた式 F = 1/2 ρ C_d A v² (流体力学の教科書)、手で計算"),
            check_record("same, power to push through the air", "drag_power", 600.0, res.get("drag_power"), "W",
                         1e-9, "closed form P = F v (textbook mechanics), 60 N x 10 m/s, worked out by hand",
                         "同じ条件で、空気を押しのけるのに使う動力", "閉じた式 P = F v (力学の教科書)、手で計算"),
        ]

    def analyse(self, design, load_case, params):
        assumptions = ["still air, steady speed, the wind straight on the front", "no aerodynamic lift"]
        v = param_value(load_case, "speed", "m/s")
        if v is None or v < 0:
            raise LoadCaseError("speed must be given and not negative")
        vals = {}
        for key, names, unit in (("cd", ("drag_coefficient",), "1"),
                                 ("front_area", ("frontal_area", "front_area"), "m^2"),
                                 ("rho", ("air_density",), "kg/m^3")):
            got = param_value(load_case, key, unit)
            if got is not None:
                vals[key] = got
                assumptions.append(f"{key} from the load case")
                continue
            got, name = _design_var(design, names, unit)
            if got is None:
                raise LoadCaseError(f"neither the load case ({key}) nor the design variables ({', '.join(names)}) "
                                    f"give it")
            vals[key] = got
            assumptions.append(f"{key} from the design variable {name}")
        F = 0.5 * vals["rho"] * vals["cd"] * vals["front_area"] * v * v
        return {"results": [result("drag_force", F, "N"), result("drag_power", F * v, "W")],
                "assumptions": assumptions}
