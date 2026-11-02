"""K2 scenarios as contract AnalysisTools (hiraku.contract.protocols.AnalysisTool).

Each tool: spec() -> tool_spec, check() -> tool_check list (the six pre-run
checks plus the tool's own known answers), run(design, load_case,
predictions, params) -> analysis record without analysis_id.

Load-case params each tool reads are listed in the tool's docstring; all are
contract quantities (or strings / booleans) and every one has a default.
"""

from __future__ import annotations

import math
import time
from functools import lru_cache

from scipy.optimize import brentq

from .checks import _check, base_checks
from .model import DesignError, box_design, build_vehicle
from .scenario import tilt_sweep
from .units import G0, param, si

try:  # the contract may grow an `assumptions` list on analysis records
    from hiraku import contract as _contract
    _ANALYSIS_PROPS = set(_contract.schema()["$defs"]["analysis"]["properties"])
except Exception:  # pragma: no cover
    _ANALYSIS_PROPS = set()


def _res(quantity: str, value: float, unit: str, where: str | None = None) -> dict:
    out = {"quantity": quantity, "value": float(value), "unit": unit}
    if where:
        out["where"] = where
    return out


class K2Tool:
    name = "k2"
    title = "K2"
    domain = "handling"
    tests: list[str] = []
    formulas: list[str] = []

    @lru_cache(maxsize=1)
    def _own_checks(self) -> tuple:
        return tuple(self.own_checks())

    def own_checks(self) -> list[dict]:
        return []

    def check(self) -> list[dict]:
        return base_checks() + [dict(c) for c in self._own_checks()]

    def spec(self) -> dict:
        checks = self.check()
        return {"name": self.name, "title": self.title, "domain": self.domain, "base": "K2", "tests": list(self.tests),
                "origin": "builtin", "usable": all(c["pass"] for c in checks), "checks": checks,
                "formulas": list(self.formulas)}

    def run(self, design: dict, load_case: dict, predictions: list[dict], params: dict | None = None) -> dict:
        t0 = time.perf_counter()
        rec = {"tool": self.name, "base": "K2", "design_version": int(design.get("design_version", 1)),
               "load_case_id": load_case.get("load_case_id", ""),
               "prediction_ids": [p["prediction_id"] for p in predictions if "prediction_id" in p],
               "status": "ok", "results": []}
        lp = dict(load_case.get("params") or {})
        lp.update(params or {})
        try:
            out = self._run(design, lp)
        except DesignError as e:
            rec["status"] = "failed"
            rec["error"] = str(e)
            out = {}
        rec["results"] = out.get("results", [])
        if out.get("series"):
            rec["series"] = out["series"]
        if out.get("vectors"):
            rec["vectors"] = out["vectors"]
        if "assumptions" in _ANALYSIS_PROPS and out.get("assumptions"):
            rec["assumptions"] = out["assumptions"]
        if out.get("error"):
            rec["status"] = "failed"
            rec["error"] = out["error"]
        rec["wall_time_s"] = time.perf_counter() - t0
        self.last_assumptions = out.get("assumptions", [])
        return rec

    def _run(self, design: dict, lp: dict) -> dict:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------- tilt
def tilt_closed_form(m_s: float, h_s: float, m_u: float, h_u: float, track: float, k_roll: float,
                     k_heave: float = math.inf) -> float:
    """Tip angle (rad) of a vehicle on vertical-slider suspension (roll axis on the ground).

    The normal load falls with cos(theta), so the body rises on its springs:
    h_s(theta) = h_s + m_s g (1 - cos theta) / k_heave. Body and wheel carriers roll
    together by phi about the roll axis: k_roll phi = g S sin(theta + phi), with
    S = m_s h_s(theta) + m_u h_u. Tip when the moment about the lower contact line
    vanishes: cos(theta) (t/2 - S sin(phi) / M) = sin(theta) S cos(phi) / M. Tyres rigid.
    """
    m = m_s + m_u

    def big_s(theta: float) -> float:
        hs = h_s + (m_s * G0 * (1.0 - math.cos(theta)) / k_heave if math.isfinite(k_heave) else 0.0)
        return m_s * hs + m_u * h_u

    def f(theta: float) -> float:
        sm = big_s(theta)
        p = brentq(lambda p: k_roll * p - G0 * sm * math.sin(theta + p), 0.0, 1.0)
        return math.cos(theta) * (track / 2.0 - sm * math.sin(p) / m) - math.sin(theta) * sm * math.cos(p) / m

    return brentq(f, 0.01, 1.5)


class TiltTool(K2Tool):
    """Tilt table (S1): turn the table slowly until the upper-side wheels carry no load.

    Load-case params: direction "left" | "right" | "both" (default both; "right" = the
    table's right side goes down), table_mu (1, default 2.0: the table surface holds the
    tyres so tipping, not sliding, is found), mu (1, optional: the road friction; reports
    whether the vehicle would slide before it tips).
    Results: tilt_angle (deg, the smaller side), tilt_angle_right, tilt_angle_left (deg),
    static_stability_factor (1, T/2h on the settled vehicle), body_roll_at_tip (deg),
    cg_shift_at_tip (mm, lateral CG shift against the contact line from suspension roll),
    friction_demand_at_tip (1), slides_first (1: 1 if mu < the demand).
    """

    name = "tilt"
    title = "傾斜台 (K2 で台をゆっくり傾ける)"
    domain = "handling"
    tests = ["S1", "S1'"]

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, k_w=20.0, wheel_mass=20.0, travel=200.0,
                       variables={"tire_vertical_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "tire_mu": {"value": 2.0, "unit": "1"}}, name="tilt check, springs")
        veh = build_vehicle(d)
        r = tilt_sweep(veh, left_up=True)
        got = None if r["tip_angle"] is None else math.degrees(r["tip_angle"])
        k_roll = 4.0 * 20e3 * 0.6 ** 2
        expected = math.degrees(tilt_closed_form(500.0, 0.5, 80.0, 0.3, 1.2, k_roll, 4.0 * 20e3))
        return [_check("tilt table, springs 20 N/mm (roll stiffness 28.8 kN m/rad), roll axis on the ground", "tilt_angle",
                       expected, got, "deg", 2e-3,
                       "body rises m_s g (1 - cos theta) / k_heave; body and carriers roll k phi = g S sin(theta + phi), "
                       "S = m_s h_s + m_u h_u; tip when cos(theta)(t/2 - S sin(phi)/M) = sin(theta) S cos(phi)/M")]

    def _run(self, design: dict, lp: dict) -> dict:
        table_mu = param(lp, "table_mu", "1", 2.0)
        veh = build_vehicle(design, {"mu": table_mu})
        direction = str(lp.get("direction", "both"))
        sides = {"both": ["right", "left"], "right": ["right"], "left": ["left"]}.get(direction)
        if sides is None:
            return {"error": f"direction must be left, right or both, not {direction!r}"}
        angles, runs = {}, {}
        for side in sides:
            # tipping to the right = left side up
            r = tilt_sweep(veh, left_up=(side == "right"), record_hz=10.0)
            if r["tip_angle"] is None:
                return {"error": f"the vehicle did not tip up to 80 deg toward the {side}; check the mass and supports",
                        "assumptions": veh.assumptions}
            angles[side] = r["tip_angle"]
            runs[side] = r
        worst = min(angles, key=lambda k: angles[k])
        run = runs[worst]
        sim = run["sim"]
        rec = run["recorder"]
        snap = rec.snap[run["snapshot"]]
        cps = [w[1] for w in snap["wheels"]]
        cg = snap["cg"]
        res = [_res("tilt_angle", math.degrees(angles[worst]), "deg", f"tipping to the {worst}")]
        for side in sides:
            res.append(_res(f"tilt_angle_{side}", math.degrees(angles[side]), "deg"))
        ys = sorted(set(round(float(c[1]), 9) for c in cps))
        track = max(ys) - min(ys)
        ssf_sim = _ssf(veh)
        res.append(_res("static_stability_factor", ssf_sim, "1"))
        roll_series = rec.ch["roll"]
        tip_i = len(rec.t) - 1
        for k, tt in enumerate(rec.t):
            if tt >= snap["t"] - 1e-9:
                tip_i = k
                break
        body_roll = roll_series[tip_i]
        res.append(_res("body_roll_at_tip", body_roll, "deg"))
        lower = [c for c in cps if (c[1] < 0) == (worst == "right")]
        y_line = sum(c[1] for c in lower) / len(lower)
        shift = (track / 2.0 - abs(float(cg[1]) - y_line)) * 1000.0
        res.append(_res("cg_shift_at_tip", shift, "mm"))
        demand = math.tan(angles[worst])
        res.append(_res("friction_demand_at_tip", demand, "1"))
        mu_road = param(lp, "mu", "1", None)
        if mu_road is not None:
            res.append(_res("slides_first", 1.0 if mu_road < demand else 0.0, "1"))
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(run["snapshot"]),
                "assumptions": veh.assumptions + [f"table surface friction {table_mu:g} so the tyres do not slide"]}


def _ssf(veh) -> float:
    """T / 2h of the vehicle as designed (track between the outermost supports, total CG height)."""
    ys = [float(c.pos[1]) for c in veh.corners]
    t = max(ys) - min(ys)
    return t / (2.0 * float(veh.cg_total[2]))


TOOLS = {t.name: t for t in (TiltTool(),)}
