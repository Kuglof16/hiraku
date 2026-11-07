import math

import numpy as np
from scipy.integrate import solve_ivp

from hiraku.solvers.shelf.base import (LOADING, BuiltinTool, LoadCaseError, Param, Predicts, check_record, on_board,
                                       param_value, result)


def section_area_mm2(sec):
    shape = sec.get("shape")
    d = sec.get("dims_mm", {})
    if shape == "rect_tube":
        return d["b"] * d["h"] - (d["b"] - 2 * d["t"]) * (d["h"] - 2 * d["t"])
    if shape == "round_tube":
        D = d.get("d", d.get("D"))
        return math.pi / 4.0 * (D ** 2 - (D - 2 * d["t"]) ** 2)
    raise LoadCaseError(f"section shape {shape!r} has no area rule in this tool")


def robot_mass_kg(design):
    """Frame members (area x length x density) plus the parts on board."""
    xyz = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    secs = design.get("sections", {})
    mats = design.get("materials", {})
    m = 0.0
    for mem in design.get("members", []):
        L = math.dist(xyz[mem["node_a"]], xyz[mem["node_b"]])
        A = section_area_mm2(secs[mem["section"]])
        rho = mats.get(mem.get("material"), {}).get("density_kg_m3")
        if rho is None:
            raise LoadCaseError(f"member {mem['id']} has no material density")
        m += A * L * 1e-9 * rho
    for p in design.get("parts", []):
        m += float(p.get("mass_kg", 0.0) or 0.0)
    return m


def two_mass_impact(m1, m2, k, v):
    """Robot m1 [kg] at v [m/s] meets a body region m2 [kg] at rest through a contact spring k [N/m]
    that only pushes. Integrated in time; returns peak force [N], peak compression [m], contact time [s],
    peak deceleration of m1 [m/s^2]."""
    def rhs(t, y):
        x1, v1, x2, v2 = y
        F = k * max(x1 - x2, 0.0)
        return [v1, -F / m1, v2, F / m2]

    def sep(t, y):
        return y[0] - y[2]
    sep.terminal = True
    sep.direction = -1
    mu = m1 * m2 / (m1 + m2)
    T = math.pi * math.sqrt(mu / k)
    sol = solve_ivp(rhs, (0.0, 3.0 * T), [0.0, v, 0.0, 0.0], events=sep, rtol=1e-10, atol=1e-13,
                    max_step=T / 400.0, dense_output=True)
    t_end = sol.t_events[0][0] if len(sol.t_events[0]) else sol.t[-1]
    ts = np.linspace(0.0, t_end, 4001)
    ys = sol.sol(ts)
    comp = np.maximum(ys[0] - ys[2], 0.0)
    x_max = float(comp.max())
    F_max = k * x_max
    return F_max, x_max, float(t_end), F_max / m1


class PersonContact(BuiltinTool):
    name = "person_contact_two_mass"
    title = "人との接触: ロボットと人の体の部位を 2 つの質量とみて、体のばねで当たる力を時間で解く"
    domain = "crash"
    base = "K0"
    tests = ["C2"]
    formulas = ["crash.linear_spring", "crash.mean_deceleration"]
    predicts = (
        Predicts("max_compression", "crash.linear_spring", "x_max",
                 "m にロボットの質量を入れると人を動かない壁とみることになり、つぶれは解析より大きく出る (上から押さえる)"),
        Predicts("robot_peak_deceleration", "crash.linear_spring", "a_max",
                 "人を壁とみると、ロボットの減速度は解析より小さく出る (2 つの質量では人も動くので、同じ力でも減速の分け方が違う)"),
        Predicts("max_contact_force", "crash.mean_deceleration", "F_mean",
                 "つぶれ s で平均した力。直線のばねでは最大の力はこの 2 倍になるので、初めの見積もりで、解析と比べると不一致になる"),
    )
    load_case_params = (
        Param("speed", "m/s", "robot speed when it meets the person", required=True),
        LOADING,
        Param("body_mass", "kg", "effective mass of the body region hit (ISO/TS 15066 Annex A body model; "
              "default 75 kg for the legs, not checked in this repository)", default=75.0),
        Param("body_stiffness", "N/mm", "effective spring constant of the body region (ISO/TS 15066 Annex A; "
              "default 60 N/mm for the lower legs, not checked in this repository)", default=60.0),
    )

    def known_answers(self):
        design = {"nodes": [], "members": [], "sections": {}, "materials": {}, "supports": [],
                  "parts": [{"id": "body", "kind": "body", "mass_kg": 2.0}]}
        lc = self.declared({"load_case_id": "check", "params": {
            "speed": {"value": 1.0, "unit": "m/s"}, "loading": "empty",
            "body_mass": {"value": 2.0, "unit": "kg"}, "body_stiffness": {"value": 0.4, "unit": "N/mm"}}})
        got = {r["quantity"]: r["value"] for r in self.analyse(design, lc, {})["results"]}
        src = ("closed form of the undamped impact of two masses through a linear spring (energy balance "
               "1/2 mu v^2 = 1/2 k x^2, mu = m1 m2 / (m1 + m2), F = k x = v sqrt(mu k)), worked out by hand: "
               "m1 = m2 = 2 kg, mu = 1 kg, k = 400 N/m, v = 1 m/s")
        return [
            check_record("2 kg robot at 1 m/s meets 2 kg body through 0.4 N/mm", "max_contact_force", 20.0,
                         got.get("max_contact_force"), "N", 1e-3, src),
            check_record("same, peak compression", "max_compression", 50.0, got.get("max_compression"), "mm", 1e-3,
                         src),
            check_record("same, contact time pi sqrt(mu / k)", "contact_time", math.pi / 20.0,
                         got.get("contact_time"), "s", 1e-3, src + "; half period pi sqrt(mu/k)"),
        ]

    def analyse(self, design, load_case, params):
        loaded = on_board(design, load_case)
        m1 = robot_mass_kg(loaded)
        if m1 <= 0:
            raise LoadCaseError("the design has no mass")
        v = param_value(load_case, "speed", "m/s")
        m2 = param_value(load_case, "body_mass", "kg")
        k = param_value(load_case, "body_stiffness", "N/mm") * 1000.0
        F, x, t, a = two_mass_impact(m1, m2, k, v)
        return {"results": [result("max_contact_force", F, "N", where="person"),
                            result("max_compression", x * 1000.0, "mm"),
                            result("contact_time", t, "s"),
                            result("robot_peak_deceleration", a / 9.80665, "g"),
                            result("robot_mass", m1, "kg")],
                "assumptions": [
                    "the robot is rigid (no soft cover in the design); only the body region is a spring",
                    "no braking before or during the contact; undamped linear contact spring",
                    f"body region {m2} kg and {k / 1000.0} N/mm (ISO/TS 15066 Annex A values for the legs, "
                    "taken as defaults; not checked in this repository)",
                    "robot mass = frame members (area x length x density) + parts on board"]}
