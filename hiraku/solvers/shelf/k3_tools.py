"""drive_energy (P4) and thermal (T1-T3): the K3 power and thermal circuit on a
forest-road loop.

Until K2 hands over a driving record, the speed along the loop comes from
K3's own longitudinal driver (cycle.speed_profile).
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k3_power_thermal import (Segment, ThermalNetwork, drive, forest_loop, from_design, network_from_design, repeat,
                               speed_profile, temperatures)
from ..k3_power_thermal.powertrain import Powertrain
from .base import LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, check_record, on_board, param_value, result

SERIES_STEP_S = 5.0  # finest sampling of the series written into the record
SERIES_MAX_POINTS = 600  # longer runs are sampled more coarsely
LOOP_PARAMS = (
    Param("distance", "km", "distance driven: laps of a 2 km forest loop, the last one cut", required=True),
    Param("grade_low", "%", "grade of the gentle climb and descent (300 m each per lap)", required=True),
    Param("grade_high", "%", "grade of the steep climb and descent (200 m each per lap)", required=True),
    Param("speed_low", "km/h", "target speed on the steep grades", required=True),
    Param("speed_high", "km/h", "target speed on the flat parts (the gentle grades take the mean of the two)",
          required=True),
    Param("soc_start", "%", "battery state of charge at the start", default=100.0),
    LOADING,
)


def _cycle(load_case: Record) -> tuple[list[Segment], float]:
    need = {"distance": "km", "grade_low": "%", "grade_high": "%", "speed_low": "km/h", "speed_high": "km/h"}
    vals = {k: param_value(load_case, k, u) for k, u in need.items()}
    missing = [f"{k} [{need[k]}]" for k, v in vals.items() if v is None]
    if missing:
        raise LoadCaseError("the loop needs load case parameters " + ", ".join(missing))
    if vals["distance"] <= 0 or vals["speed_low"] <= 0 or vals["speed_high"] < vals["speed_low"]:
        raise LoadCaseError("distance and speeds must be positive, with speed_low <= speed_high")
    lap = forest_loop(vals["grade_low"] / 100.0, vals["grade_high"] / 100.0, vals["speed_low"] / 3.6, vals["speed_high"] / 3.6)
    return repeat(lap, vals["distance"] * 1000.0), vals["distance"]


def _soc0(load_case: Record) -> float:
    s = param_value(load_case, "soc_start", "%")
    if not 0 < s <= 100:
        raise LoadCaseError(f"soc_start must be in (0, 100] %, got {s}")
    return s / 100.0


def _series(channel: str, unit: str, t: np.ndarray, values: np.ndarray) -> Record:
    step = max(SERIES_STEP_S, float(t[-1]) / SERIES_MAX_POINTS)
    grid = np.arange(0.0, t[-1] + 1e-9, step)
    return {"channel": channel, "unit": unit, "t_s": [float(x) for x in grid],
            "values": [float(x) for x in np.interp(grid, t, values)]}


def _step_series(t: np.ndarray, per_step: np.ndarray) -> np.ndarray:
    """Values held over steps, as point values at the step ends (for interpolation)."""
    return np.concatenate([[per_step[0]], per_step])


class DriveEnergy(BuiltinTool):
    name = "drive_energy"
    title = "周回の電力と電池の残量 (電力と熱回路)"
    domain = "powertrain"
    base = "K3"
    tests = ["P4"]
    formulas = ["drive_energy.loop_energy", "drive_energy.soc_end", "drive_energy.steady_power"]
    load_case_params = LOOP_PARAMS
    predicts = (
        Predicts("energy_used", "drive_energy.loop_energy", "E", "H = total height climbed over the distance"),
        Predicts("soc_used", "drive_energy.loop_energy", "soc_used"),
        Predicts("soc_end", "drive_energy.soc_end", "soc_end",
                 "E = the predicted energy_used, E_batt = battery_voltage x battery_capacity"),
        Predicts("range", "drive_energy.loop_energy", "range"),
        Predicts("max_battery_power", "drive_energy.steady_power", "P",
                 "rough: steady climb at speed_high up grade_high, without acceleration or auxiliary loads"),
    )

    def known_answers(self) -> list[Record]:
        return drive_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params."""
        design = on_board(design, load_case)
        segs, dist = _cycle(load_case)
        pt = from_design(design)
        soc0 = _soc0(load_case)
        r = drive(segs, pt, soc0)
        e = r.energy
        soc = r.soc
        empty = np.nonzero(soc <= 0)[0]
        reached = float(r.x[empty[0]]) / 1000.0 if len(empty) else dist
        soc_end = max(float(soc[-1]), 0.0)
        used = soc0 - float(soc[-1])
        results = [
            result("energy_used", e["battery"], "Wh"),
            result("soc_end", soc_end * 100.0, "%"),
            result("soc_used", used * 100.0, "%"),
            result("range", dist * soc0 / used if used > 0 else math.inf, "km"),
            result("distance_on_battery", reached, "km"),
            result("max_battery_power", float(r.P_batt.max()) / 1000.0, "kW"),
            result("max_battery_current", float(r.I_batt.max()), "A"),
            result("drive_time", float(r.t[-1]), "s"),
            result("mean_speed", dist / (r.t[-1] / 3600.0), "km/h"),
            result("vehicle_mass", pt.mass, "kg"),
        ]
        known = e["rolling"] + e["grade_up"] + e["aero"] + e["aux"]
        parts = {"rolling": e["rolling"], "climbing": e["grade_up"], "aero": e["aero"], "aux": e["aux"],
                 "chain_losses_minus_recovered": e["battery"] - known}
        effects = [{"name": k, "value": float(v), "unit": "Wh", "share": float(v / e["battery"])} for k, v in parts.items()]
        series = [
            _series("speed", "km/h", r.t, r.v * 3.6),
            _series("grade", "%", r.t, r.grade * 100.0),
            *battery_power_series(design, r),
            _series("current.battery", "A", r.t, _step_series(r.t, r.I_batt)),
            _series("soc", "%", r.t, r.soc * 100.0),
            *chain_series(design, r),
        ]
        out = {"results": results, "effects": effects, "series": series}
        bat = _part_of(design, "battery")
        if bat:
            out["fields"] = [{"kind": "damage", "label": "battery used (share of capacity)", "unit": "%",
                              "per_part": {bat: used * 100.0}}]
        return out


class Thermal(BuiltinTool):
    name = "thermal"
    title = "電池・モーター・制御器の温度 (熱回路)"
    domain = "thermal"
    base = "K3"
    tests = ["T1", "T2", "T3"]
    formulas = ["thermal.loop_steady_temperature", "thermal.steady_temperature", "thermal.rc_step",
                "thermal.loss_from_efficiency"]
    load_case_params = LOOP_PARAMS + (
        Param("ambient_temperature", "degC", "air temperature (and the parts' starting temperature)", required=True),
        Param("charge_power", "kW", "charging power after the drive (with charge_time; without them: no charge)"),
        Param("charge_time", "h", "charging time after the drive (with charge_power)"),
    )
    predicts = (
        Predicts("temp_<part>_max", "thermal.loop_steady_temperature", "T_<part>",
                 "the loop params of the load case, the powertrain and thermal design variables"),
        Predicts("drive_time", "thermal.loop_steady_temperature", "t"),
        Predicts("temp_<part>_max", "thermal.rc_step", "T",
                 "P = loss_<part>_mean, R and tau = R C of the part, t = drive_time"),
        Predicts("temp_<part>_max", "thermal.steady_temperature", "T",
                 "the upper bound for a long drive: P = loss_<part>_mean"),
        Predicts("loss_<part>_mean", "thermal.loss_from_efficiency", "P_loss",
                 "motor and controller: P_out = the mean output power of that part over the drive"),
    )

    def known_answers(self) -> list[Record]:
        return thermal_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params."""
        design = on_board(design, load_case)
        segs, dist = _cycle(load_case)
        T_amb = param_value(load_case, "ambient_temperature", "degC")
        if T_amb is None:
            raise LoadCaseError("the thermal test needs the load case parameter ambient_temperature (degC)")
        pt = from_design(design)
        net = network_from_design(design)
        r = drive(segs, pt, _soc0(load_case))
        P_ch = param_value(load_case, "charge_power", "W")
        t_ch = param_value(load_case, "charge_time", "s")
        charge = (P_ch, t_ch) if P_ch and t_ch else None
        th = temperatures(r, net, T_amb, charge=charge, pt=pt)
        T, t = th["T"], th["t"]
        results, per_part, effects = [], {}, []
        steps = np.diff(r.t)
        for i, p in enumerate(net.names):
            where = _part_of(design, p) or p
            k = int(np.argmax(T[:, i]))
            results.append(result(f"temp_{p}_max", float(T[k, i]), "degC", where))
            results.append(result(f"temp_{p}_end", float(T[-1, i]), "degC", where))
            mean_loss = float(np.sum(r.heat[p] * steps) / r.t[-1])  # over the drive, not the charge
            results.append(result(f"loss_{p}_mean", mean_loss, "W", where))
            per_part[where] = float(T[k, i])
            tau = net.C[i] / net.G_amb[i]
            steady = mean_loss / float(net.G_amb[i])
            effects.append({"name": f"{p}: steady rise of the mean drive loss (P R)", "value": steady, "unit": "K"})
            effects.append({"name": f"{p}: share of that rise reached (thermal time constant {tau:.0f} s)",
                            "value": (float(T[k, i]) - T_amb) / steady if steady > 0 else 0.0, "unit": "1"})
        results.append(result("drive_time", float(r.t[-1]), "s"))
        series = [_series(f"temp.{p}", "degC", t, T[:, i]) for i, p in enumerate(net.names)]
        series += chain_series(design, r)
        for i, p in enumerate(net.names):  # heat to the air G_amb (T - T_amb) at the grid points, drive and charge
            q_air = float(net.G_amb[i]) * (T[:, i] - T_amb)
            ids = _parts_of(design, p)
            series += (_per_part(f"heat_to_air.{p}", "W", t, q_air, ids, W_DIGITS) if ids
                       else [_series(f"heat_to_air.{p}", "W", t, q_air)])
        return {"results": results,
                "fields": [{"kind": "temperature", "label": "highest temperature in the run", "unit": "degC", "per_part": per_part}],
                "series": series, "effects": effects}


def _parts_of(design: Record, kind: str) -> list[str]:
    """The ids of the parts of a kind; for wheels only those on a driven support (a wheel part's connects_to names
    its support)."""
    ids = [p for p in design.get("parts", []) if p["kind"] == kind]
    if kind == "wheel":
        driven = {s["id"] for s in design.get("supports", []) if s["actuation"]["drive"]}
        ids = [p for p in ids if any(c.get("to") in driven for c in p.get("connects_to", []))]
    return [p["id"] for p in ids]


def _per_part(channel: str, unit: str, t: np.ndarray, values: np.ndarray, ids: list[str],
              digits: int | None = None) -> list[Record]:
    """One series per part, the value shared equally between the parts of the kind (K3 has one battery, motor,
    controller, reducer and one wheel force: two battery packs in series carry the same current at half the
    voltage each, so half the power and, with half the resistance each (assumption: the two packs alike, as
    designgen ladder_4w battery_split cuts them), half the loss; driven wheels share the one wheel force, as K3
    uses one mean wheel radius). Without a part of the kind: no series. digits: values rounded to that many decimals
    (None: as they are)."""
    out = []
    for pid in ids:
        s = _series(channel, unit, t, values / len(ids))
        if digits is not None:
            s["values"] = [round(x, digits) + 0.0 for x in s["values"]]
        out.append(dict(s, part_id=pid))
    return out


W_DIGITS = 1  # the power, loss and heat series in W are written to 0.1 W (keeps the record small)


def chain_series(design: Record, r) -> list[Record]:
    """power.<stage> (W) through the controller, motor, reducer and driven wheels and loss.<part> (W) of the battery,
    controller, motor and reducer, each with the part_id of its design part; a stage or loss whose kind has no part
    in the design is left out."""
    out = []
    for stage in ("controller", "motor", "reducer", "wheel"):
        out += _per_part(f"power.{stage}", "W", r.t, _step_series(r.t, r.flow[stage]), _parts_of(design, stage),
                         W_DIGITS)
    losses = {"battery": r.heat["battery"], "controller": r.heat["controller"], "motor": r.heat["motor"],
              "reducer": r.gear_loss}
    for kind, per_step in losses.items():
        out += _per_part(f"loss.{kind}", "W", r.t, _step_series(r.t, per_step), _parts_of(design, kind), W_DIGITS)
    return out


def battery_power_series(design: Record, r) -> list[Record]:
    """power.battery (kW, terminal power): one series per battery part with its part_id (two packs in series:
    half each), or one without part_id when the design has no battery part."""
    values = _step_series(r.t, r.P_batt) / 1000.0
    ids = _parts_of(design, "battery")
    return _per_part("power.battery", "kW", r.t, values, ids) if ids else [_series("power.battery", "kW", r.t, values)]


def _part_of(design: Record, kind: str) -> str | None:
    for p in design.get("parts", []):
        if p["kind"] == kind:
            return p["id"]
    return None


# ---------------------------------------------------------------- known answers

def _test_powertrain(**over) -> Powertrain:
    base = dict(mass=600.0, Crr=0.02, CdA=0.0, rho=1.225, mass_factor=1.0, P_max=1e9, T_max=100.0, n_max=1e9,
                gear=10.0, r_wheel=0.3, eta_drive=1.0, eta_motor=1.0, eta_ctrl=1.0, eta_regen=1.0, P_aux=0.0,
                V_batt=50.0, Q_batt=100.0, R_batt=0.0)
    base.update(over)
    return Powertrain(**base)


def drive_known_answers() -> list[Record]:
    out = []
    pt = _test_powertrain()
    d = 5000.0
    r = drive([Segment(d, 0.0, 10.0)], pt)
    expected = 600.0 * STANDARD_GRAVITY * 0.02 * d / 3600.0
    out.append(check_record("flat 5 km, lossless drive chain with full regeneration", "energy_used",
                            expected, r.energy["battery"], "Wh", 1e-6,
                            "rolling resistance work m g C_rr d; acceleration energy comes back when stopping"))
    pt = _test_powertrain(R_batt=0.1)
    I = pt.battery_current(1000.0)
    out.append(check_record("battery 50 V, 0.1 ohm, 1 kW at the terminals", "current",
                            (50.0 - math.sqrt(2500.0 - 400.0)) / 0.2, I, "A", 1e-12,
                            "equivalent circuit: (OCV - R I) I = P, smaller root"))
    pt = _test_powertrain(T_max=1000.0 * 0.3 / 10.0)  # 1000 N at the wheels, 1.67 m/s^2 for 600 kg
    prof = speed_profile([Segment(200.0, 0.0, 10.0), Segment(200.0, 0.0, 10.0)], pt, dx=0.05)
    k = int(np.argmax(prof["v"] >= 10.0 - 1e-9))
    t = float(np.sum(np.diff(prof["x"][:k + 1]) / ((prof["v"][1:k + 1] + prof["v"][:k]) / 2.0)))
    out.append(check_record("0 to 10 m/s with 1000 N at the wheels, 600 kg, rolling resistance 0.02",
                            "time_to_speed", 600.0 * 10.0 / (1000.0 - 600.0 * STANDARD_GRAVITY * 0.02), t, "s", 2e-3,
                            "constant net force: t = m v / (F - m g C_rr)"))
    return out


def thermal_known_answers() -> list[Record]:
    out = []
    net = ThermalNetwork(["part"], np.array([2000.0]), np.array([1.0 / 0.5]))
    n = 1000
    T = net.simulate(np.full((n, 1), 100.0), 1.0, 0.0)
    src = "first-order RC: dT = P R (1 - exp(-t / R C)); P 100 W, R 0.5 K/W, C 2000 J/K, tau 1000 s"
    out.append(check_record("one part, 100 W step", "rise_at_tau", 50.0 * (1.0 - math.exp(-1.0)), float(T[n, 0]), "K", 1e-9, src))
    out.append(check_record("one part, 100 W step", "steady_rise", 50.0, float(net.steady([100.0])[0]), "K", 1e-12, src))
    G = np.array([[0.0, 1.0 / 0.3], [1.0 / 0.3, 0.0]])
    net2 = ThermalNetwork(["inner", "outer"], np.array([500.0, 800.0]), np.array([0.0, 1.0 / 0.7]), G)
    out.append(check_record("two parts in series to the air (0.3 + 0.7 K/W), 40 W into the inner", "steady_rise",
                            40.0 * (0.3 + 0.7), float(net2.steady([40.0, 0.0])[0]), "K", 1e-12,
                            "series thermal resistances add"))
    P = np.zeros((3000, 2))
    P[:1000, 0] = 40.0
    P[1000:, 1] = 25.0
    T2 = net2.simulate(P, 1.0, 20.0)
    stored = float(np.sum(net2.C * (T2[-1] - 20.0)))
    lost = float(np.sum((T2[:-1, 1] + T2[1:, 1]) / 2.0 - 20.0) / 0.7)  # trapezoid of the outflow
    put_in = 40.0 * 1000 + 25.0 * 2000
    out.append(check_record("energy ledger of the two-part network", "heat_in",
                            put_in, stored + lost, "J", 1e-3,
                            "heat put in = heat stored + heat lost to the air (outflow integrated by trapezoids)"))
    return out
