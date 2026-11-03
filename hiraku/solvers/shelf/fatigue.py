"""fatigue (R3): damage of the frame's welded joints on a rough road.

Wheel forces: from an upstream analysis with time series of the ground force
at every wheel (K2: reaction_x/y/z.<ID> and grip_x/y/z.<ID>, N, world axes),
or, until that exists, from a quarter-car at each wheel driven over an
ISO 8608 road (vertical forces only; front and rear on the same track, left
and right tracks independent). Stress at each weld point is the sum of the
unit-force stresses (K1 with inertia relief) times the wheel forces;
rainflow counting, an IIW-type S-N curve and Miner's rule give the damage,
scaled from the sampled length to the target distance.

Road classes: G_d(0.1 cycle/m) = 16e-6 m^3 for class A, times 4 per class
(C = 256e-6), waviness 2: the road classes of ISO 8608:2016, Mechanical
vibration - Road surface profiles - Reporting of measured data; not checked
in this repository (the standard text has not been read here). The S-N curve
(FAT at 2e6 cycles, slope m, knee) is that of formula
fatigue.constant_amplitude_damage, but the rainflow cycles of the road are a
stress spectrum: below the knee the curve goes on with slope 2 m - 1, as
IIW-1823-07 (2008) sec. 4.3.1, PDF p.110 gives for Miner summation of a
spectrum (read in that document by the quality department; the k0 function
library_fatigue.spectrum_slope). The design variable fatigue_slope_after_knee
is the constant-amplitude slope (IIW-1823-07 sec. 3.2, p.44) and is not used
for the damage.

Design variables: fatigue_fat_class (MPa at 2e6 cycles), fatigue_slope,
fatigue_knee_cycles, tyre_stiffness (N/mm); fatigue_slope_after_knee only for
the constant-amplitude prediction.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal

from ..k0_handcalc import units
from ..k0_handcalc.library_fatigue import spectrum_slope
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError, mass_summary, support_loads
from ..k1_frame.fatigue import equivalent_range, influence, miner, rainflow, sn_cycles
from .base import (LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, check_record, on_board, param_text,
                   param_value, result)

ROAD_CLASSES = {c: 16e-6 * 4**i for i, c in enumerate("ABCDEFGH")}  # ISO 8608 (see above)
N0 = 0.1  # cycle/m
WAVINESS = 2.0
# Band of the road model (wavelengths 0.35 m to 91 m): a modelling choice, not
# checked against the bands ISO 8608 reports.
N_BAND = (0.011, 2.83)  # cycle/m
SN_VARS = {"fatigue_fat_class": "MPa", "fatigue_slope": "1", "fatigue_knee_cycles": "1"}
CONSTANT_AMPLITUDE_SLOPE = "fatigue_slope_after_knee"  # not used for the road spectrum (see above)
SERIES_POINTS = 600


ROAD_DX = 0.05  # m, road sampling (the shortest wavelength in the band is 0.35 m)


def road_profile(x: np.ndarray, gd0: float, seed: int, n_terms: int = 800) -> np.ndarray:
    """Height (m) along x (m): sum of cosines with amplitudes sqrt(2 G_d(n) dn) and random phases,
    so the one-sided displacement PSD is G_d(n) = G_d(n0) (n / n0)^-w."""
    n = np.linspace(N_BAND[0], N_BAND[1], n_terms)
    dn = n[1] - n[0]
    amp = np.sqrt(2.0 * gd0 * (n / N0) ** (-WAVINESS) * dn)
    phase = np.random.default_rng(seed).uniform(0.0, 2.0 * math.pi, n_terms)
    x = np.asarray(x, float)
    out = np.empty(len(x))
    for i in range(0, len(x), 4000):
        out[i:i + 4000] = np.cos(2.0 * math.pi * np.outer(x[i:i + 4000], n) + phase) @ amp
    return out


def quarter_car_force(z_road: np.ndarray, dt: float, ms: float, mu: float, ks: float, cs: float, kt: float) -> np.ndarray:
    """Dynamic tyre force (N) of a linear quarter car (SI) for a road height series."""
    A = np.array([[0, 1, 0, 0],
                  [-ks / ms, -cs / ms, ks / ms, cs / ms],
                  [0, 0, 0, 1],
                  [ks / mu, cs / mu, -(ks + kt) / mu, -cs / mu]])
    B = np.array([[0], [0], [0], [kt / mu]])
    C = np.array([[0, 0, -kt, 0]])
    D = np.array([[kt]])
    t = np.arange(len(z_road)) * dt
    _, y, _ = signal.lsim((A, B, C, D), z_road, t)
    return np.asarray(y, float).ravel()


def _var(design: Record, name: str, unit: str) -> float | None:
    q = design.get("variables", {}).get(name)
    return None if q is None else units.to_unit(q, unit, name)


def _sn(design: Record) -> tuple[float, float, float, float]:
    """(FAT, m, knee cycles, slope below the knee for the road spectrum = 2 m - 1)."""
    vals = {k: _var(design, k, u) for k, u in SN_VARS.items()}
    missing = [f"{k} [{SN_VARS[k]}]" for k, v in vals.items() if v is None]
    if missing:
        raise DesignError("the design has no S-N data for: " + ", ".join(missing) + "; add them to design.variables")
    fat, m, n_knee = (vals[k] for k in SN_VARS)
    return fat, m, n_knee, spectrum_slope(m)


def slope_assumption(m: float, m2: float, m2_design: float | None) -> str:
    text = (f"S-N の曲がり点より下は、傾き {m2:g} (= 2 × {m:g} − 1) で延ばした。道のゆれは振幅の変わる繰り返し (スペクトル) で、"
            "IIW-1823-07 (2008) 4.3.1 節 (p.110) は、その Miner の和に 2m − 1 を使う (品質保証部がその文書で確かめた)")
    if m2_design is not None:
        text += (f"。設計の変数 {CONSTANT_AMPLITUDE_SLOPE} = {m2_design:g} は一定の振幅のときの傾き "
                 "(同じ文書 3.2 節、p.44、とても多い回数) なので、ここでは使わない")
    return text


def road_wheel_forces(design: Record, speed: float, length: float, gd0: float, seed: int) -> tuple[np.ndarray, dict]:
    """Vertical ground forces (N) at each support over time from quarter cars on the road (SI inputs)."""
    kt = _var(design, "tyre_stiffness", "N/mm")
    if kt is None:
        raise DesignError("the design has no tyre_stiffness [N/mm]; add it to design.variables")
    summ = mass_summary(design)
    static = support_loads(design, summary=summ)["supports"]
    unsprung = {s["id"]: 0.0 for s in design["supports"]}
    for it in summ["items"]:
        if it.carrier == "support":
            unsprung[it.carried_by[0]] += it.mass_kg
    f_hop = max(math.sqrt((s["suspension"].get("stiffness_N_per_mm", 0.0) * 1e3 + kt * 1e3) / max(unsprung[s["id"]], 1.0))
                for s in design["supports"]) / (2 * math.pi)
    dt = min(1e-3, 1.0 / (20.0 * f_hop))
    t = np.arange(0.0, length / speed, dt)
    xs = [s["position_mm"][0] / 1000.0 for s in design["supports"]]
    x_front = max(xs)
    reach = (x_front - min(xs)) + 1.0
    tracks: dict[float, np.ndarray] = {}
    forces = {}
    for s in design["supports"]:
        y = round(s["position_mm"][1], 3)
        if y not in tracks:
            xx = np.arange(-reach, length + ROAD_DX, ROAD_DX)
            tracks[y] = (xx, road_profile(xx, gd0, seed + len(tracks)))
        xx, zz = tracks[y]
        lag = x_front - s["position_mm"][0] / 1000.0  # rear wheels meet the same bump later
        z = np.interp(speed * t - lag, xx, zz)
        susp = s.get("suspension", {})
        ks = float(susp.get("stiffness_N_per_mm") or 0.0) * 1e3
        cs = float(susp.get("damping_Ns_per_mm") or 0.0) * 1e3
        if ks <= 0:
            raise DesignError(f"support {s['id']} has no spring rate; the road model needs suspension.stiffness_N_per_mm")
        W = float(static[s["id"]]["force_N"][2])
        mu = max(unsprung[s["id"]], 1.0)
        ms = max(W / STANDARD_GRAVITY - mu, 1.0)
        forces[s["id"]] = np.maximum(W + quarter_car_force(z, dt, ms, mu, ks, cs, kt * 1e3), 0.0)
    return t, forces


SCENE_POINTS = 801  # road points drawn per track (the road is sampled more finely for the forces)


def road_scene(design: Record, length: float, gd0: float, seed: int, cls: str) -> Record:
    """The road of road_wheel_forces as a scene road_profile: each track's height (mm) along x
    from behind the rear wheels to the end of the sample, at up to SCENE_POINTS points."""
    xs = [s["position_mm"][0] / 1000.0 for s in design["supports"]]
    reach = (max(xs) - min(xs)) + 1.0
    x = np.linspace(-reach, length, min(SCENE_POINTS, int((length + reach) / ROAD_DX) + 1))
    tracks: list[float] = []
    for s in design["supports"]:
        y = round(s["position_mm"][1], 3)
        if y not in tracks:
            tracks.append(y)
    z = {f"y {y:g} mm": [float(v) for v in road_profile(x, gd0, seed + i) * 1000.0] for i, y in enumerate(tracks)}
    return {"kind": "road_profile", "label": f"ISO 8608 class {cls}",
            "profile": {"x_mm": [float(v) for v in x * 1000.0], "z_mm": z}}


def upstream_series(upstream: dict | None, design: Record) -> tuple[str, np.ndarray, dict, float] | None:
    """(load case id, t, {(support, axis): force series}, distance m) from an upstream analysis that
    has ground-force series for every support."""
    ids = [s["id"] for s in design["supports"]]
    for lc_id, ana in (upstream or {}).items():
        if not isinstance(ana, dict) or ana.get("status") != "ok":
            continue
        ch = {s["channel"]: s for s in ana.get("series", [])}
        if not all(f"reaction_z.{i}" in ch for i in ids):
            continue
        t = np.asarray(ch[f"reaction_z.{ids[0]}"]["t_s"], float)
        out = {}
        for i in ids:
            for ax in "xyz":
                vals = np.zeros_like(t)
                for kind in ("reaction", "grip"):
                    c = ch.get(f"{kind}_{ax}.{i}")
                    if c is not None:
                        vals = vals + np.interp(t, c["t_s"], c["values"])
                out[(i, ax)] = vals
        dist = None
        if "speed" in ch:
            sp = ch["speed"]
            v = np.asarray(sp["values"], float) * (1 / 3.6 if sp["unit"] == "km/h" else 1.0)
            dist = float(np.trapezoid(v, sp["t_s"]))
        return lc_id, t, out, dist
    return None


class Fatigue(BuiltinTool):
    name = "fatigue"
    stage = 3  # fatigue on road spectra
    title = "悪路の疲労 (輪の力 → フレームの応力 → レインフロー → S-N とマイナー則)"
    domain = "strength"
    base = "K1"
    tests = ["R3"]
    formulas = ["fatigue.road_damage_four_wheels", "frame_static.end_cross_member_stress",
                "fatigue.road_damage_quarter_car", "fatigue.constant_amplitude_damage", "fatigue.cycles_over_distance",
                "modal.quarter_car", "frame_static.rail_joint_stress"]
    load_case_params = (
        Param("distance", "km", "life to check: the distance the damage is scaled to (e.g. requirement key Q90)",
              required=True),
        Param("road_class", None, "ISO 8608 road class; needed without an upstream wheel-force history",
              choices=tuple(ROAD_CLASSES)),
        Param("speed", "km/h", "speed over the road; needed without an upstream wheel-force history"),
        Param("sample_length", "m", "length of road simulated (the damage is scaled from it to distance)", default=400.0),
        Param("seed", "1", "seed of the random road phases", default=0.0),
        LOADING,
    )
    predicts = (
        Predicts("fatigue_damage", "fatigue.road_damage_four_wheels", "D",
                 "sigma_ra .. sigma_fb from frame_static.end_cross_member_stress with the inputs of "
                 "hiraku.solvers.k0_handcalc.frame_inputs.end_cross_member_inputs(design, loading=...): the end cross "
                 "member of the rear overhang, for a ladder frame whose wheels stand outboard of the rails"),
        Predicts("fatigue_damage", "fatigue.road_damage_quarter_car", "D",
                 "sigma_w from frame_static.rail_joint_stress at rest (F = 0, W = the joint's wheel load); one quarter "
                 "car on the road class of the load case"),
        Predicts("fatigue_damage", "fatigue.constant_amplitude_damage", "D",
                 "dS = equivalent_stress_range, n = cycles_counted, S-N data from the design variables (m2: the "
                 "constant-amplitude slope fatigue_slope_after_knee, where the tool sums the spectrum with 2 m - 1)"),
        Predicts("cycles_counted", "fatigue.cycles_over_distance", "n",
                 "rough: f = the body bounce frequency (modal.quarter_car f)"),
    )
    upstream = ("an analysis with series reaction_z.<support> (and reaction_x/y, grip_x/y/z) for every support and "
                "a speed series (K2): its wheel forces replace the road model")

    def known_answers(self) -> list[Record]:
        out = []
        cyc = rainflow([-2, 1, -3, 5, -1, 3, -4, 4, -2])
        counts: dict[float, float] = {}
        for r, _, c in cyc:
            counts[r] = counts.get(r, 0.0) + c
        src = ("ASTM E1049-85 rainflow example [-2, 1, -3, 5, -1, 3, -4, 4, -2]: ranges 3 (0.5), 4 (1.5), 6 (0.5), 8 (1.0), "
               "9 (0.5); not checked against the standard text in this repository")
        for rng, n in ((3.0, 0.5), (4.0, 1.5), (6.0, 0.5), (8.0, 1.0), (9.0, 0.5)):
            out.append(check_record("ASTM E1049 rainflow example", f"count_of_range_{rng:g}", n, counts.get(rng, 0.0),
                                    "1", 1e-12, src))
        out.append(check_record("S-N curve, FAT 71, m 3", "cycles_at_FAT", 2e6, sn_cycles(71.0, 71.0, 3.0, 1e7, 22.0),
                                "1", 1e-12, "the FAT class is the stress range at 2e6 cycles"))
        D = miner([(100.0, 0.0, 1000.0)], 71.0, 3.0, 1e7, 22.0)
        out.append(check_record("1000 cycles of 100 MPa, FAT 71, m 3", "damage", 1000.0 / (2e6 * (71.0 / 100.0) ** 3), D,
                                "1", 1e-12, "Miner: D = n / N, N = 2e6 (FAT / range)^m"))
        knee = 71.0 * (2e6 / 1e7) ** (1.0 / 3.0)
        out.append(check_record("S-N curve below the knee for a spectrum, FAT 71, m 3", "cycles_at_half_the_knee_range",
                                1e7 * 2.0**5, sn_cycles(knee / 2.0, 71.0, 3.0, 1e7, spectrum_slope(3.0)), "1", 1e-9,
                                "slope 2 m - 1 = 5 below the knee for Miner summation of a spectrum (IIW-1823-07 (2008) "
                                "sec. 4.3.1, p.110): half the knee range lasts 2^5 times the knee cycles"))
        rng = np.random.default_rng(0)
        series = rng.normal(size=4000)
        out.append(check_record("rainflow of a random series", "sum_of_half_ranges",
                                float(np.sum(np.abs(np.diff(_rev(series))))) / 2.0,
                                sum(r * c for r, _, c in rainflow(series)), "1", 1e-9,
                                "every reversal is counted once: sum over cycles of range * count = half the total travel"))
        x = np.arange(0.0, 2000.0, ROAD_DX)
        z = road_profile(x, ROAD_CLASSES["C"], 0)
        f, p = signal.welch(z, fs=1.0 / ROAD_DX, nperseg=4096)
        band = (f > 0.05) & (f < 1.0)
        g_fit = float(np.mean(p[band] * (f[band] / N0) ** WAVINESS))
        out.append(check_record("ISO 8608 class C road made by the sum of cosines", "G_d_at_n0", 256e-6 * 1e6, g_fit * 1e6,
                                "1", 0.15, "one-sided Welch PSD of the generated road over 0.05-1 cycle/m, "
                                "scaled by (n/n0)^2 and averaged (x 1e6 m^3); random phases leave about 10 % scatter"))
        return out

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params."""
        design = on_board(design, load_case)
        target = param_value(load_case, "distance", "m")
        if target is None or target <= 0:
            raise LoadCaseError("fatigue needs the load case parameter distance (the life to check, e.g. 5000 km)")
        fat, m, n_knee, m2 = _sn(design)  # m2: the spectrum's slope below the knee, 2 m - 1
        up = upstream_series(params.get("upstream_analyses"), design)
        if up is not None:
            src, t, F, sample = up
            if not sample:
                raise LoadCaseError(f"the upstream history {src} has no speed series, so its distance is unknown")
            S, cols, pts = influence(design, "xyz")
            Fm = np.vstack([F[c] for c in cols])
            source = f"wheel forces from {src}"
            road = None  # the upstream analysis draws its own road
        else:
            cls = param_text(load_case, "road_class")
            if cls not in ROAD_CLASSES:
                raise LoadCaseError(f"give road_class (one of {', '.join(ROAD_CLASSES)}) or an upstream wheel-force history")
            v = param_value(load_case, "speed", "m/s")
            if v is None or v <= 0:
                raise LoadCaseError("the road model needs the load case parameter speed (km/h)")
            sample = param_value(load_case, "sample_length", "m")
            seed = int(param_value(load_case, "seed", "1"))
            t, F = road_wheel_forces(design, v, sample, ROAD_CLASSES[cls], seed)
            S, cols, pts = influence(design, "z")
            Fm = np.vstack([F[sid] for sid, _ in cols])
            source = f"quarter cars on an ISO 8608 class {cls} road at {v * 3.6:.0f} km/h"
            road = road_scene(design, sample, ROAD_CLASSES[cls], seed, cls)
        sigma = S @ Fm  # points x time, MPa
        scale = target / sample
        damage = np.empty(len(pts))
        cycles_at = []
        for p in range(len(pts)):
            cyc = rainflow(sigma[p])
            cycles_at.append(cyc)
            damage[p] = miner(cyc, fat, m, n_knee, m2) * scale
        w = int(np.argmax(damage))
        member = pts[w][0]
        cyc = cycles_at[w]
        D = float(damage[w])
        per_member: dict[str, float] = {}
        for (mid, _, _), dm in zip(pts, damage):
            per_member[mid] = max(per_member.get(mid, 0.0), float(dm))
        ranges = [r for r, _, _ in cyc]
        scaled = [(r, mean, c * scale) for r, mean, c in cyc]
        results = [
            result("fatigue_damage", D, "1", member),
            result("fatigue_life", target / D / 1000.0 if D > 0 else math.inf, "km", member),
            result("max_stress_range", max(ranges) if ranges else 0.0, "MPa", member),
            result("equivalent_stress_range", equivalent_range(scaled, m), "MPa", member),
            result("cycles_counted", sum(c for _, _, c in scaled), "1", member),
            result("mean_stress", float(np.mean(sigma[w])), "MPa", member),
            result("sample_length", sample, "m"),
        ]
        # contribution of stress-range bands to the damage at the worst point
        bands = [(0, 10), (10, 20), (20, 40), (40, 80), (80, math.inf)]
        effects = []
        for lo, hi in bands:
            d_band = sum(c / sn_cycles(r, fat, m, n_knee, m2) for r, _, c in scaled if lo <= r < hi and r > 0)
            effects.append({"name": f"stress ranges {lo}-{hi if hi < math.inf else 'up'} MPa", "value": d_band, "unit": "1",
                            "share": d_band / D if D > 0 else 0.0})
        effects.append({"name": f"loads: {source}", "value": scale, "unit": "1"})
        step = max(1, len(t) // SERIES_POINTS)
        series = [{"channel": f"stress.{member}", "unit": "MPa", "t_s": [float(x) for x in t[::step]],
                   "values": [float(x) for x in sigma[w][::step]]}]
        for sid, _ in cols[:: (3 if up is not None else 1)]:
            key = (sid, "z") if up is not None else sid
            series.append({"channel": f"wheel_load.{sid}", "unit": "N", "t_s": [float(x) for x in t[::step]],
                           "values": [float(x) for x in F[key][::step]]})
        series += sn_series(fat, m, n_knee, m2, scaled, member)
        fields = [{"kind": "damage", "label": f"Miner damage over {target / 1000:g} km (largest weld point)", "unit": "1",
                   "per_member": per_member}]
        out = {"results": results, "fields": fields, "series": series, "effects": effects,
               "assumptions": [slope_assumption(m, m2, _var(design, CONSTANT_AMPLITUDE_SLOPE, "1"))]}
        if road is not None:
            out["scene"] = [road]
        return out


SN_POINTS = 61
SPECTRUM_POINTS = 200


def sn_series(fat: float, m: float, n_knee: float, m2: float, cycles: list, member: str) -> list[Record]:
    """The S-N curve (stress range against cycles to failure) and the stress ranges counted at the worst
    point over the distance, largest first, against the cycles that reach at least that range. m2 is the
    slope below the knee that the damage used (2 m - 1 for the road spectrum), so the curve drawn is the
    one the counted ranges are summed against."""
    s_knee = fat * (2e6 / n_knee) ** (1.0 / m)
    N = np.logspace(4.0, 10.0, SN_POINTS)
    S = np.where(N < n_knee, fat * (2e6 / N) ** (1.0 / m), s_knee * (n_knee / N) ** (1.0 / m2))
    out = [{"channel": "sn_curve", "unit": "MPa", "values": [round(float(s), 4) for s in S],
            "x": {"quantity": "cycles_to_failure", "unit": "1", "values": [round(float(n), 1) for n in N]}}]
    kept = sorted(((r, c) for r, _, c in cycles if r > 0), reverse=True)
    if kept:
        ranges = np.array([r for r, _ in kept])
        total = np.cumsum([c for _, c in kept])
        pick = np.unique(np.linspace(0, len(kept) - 1, min(SPECTRUM_POINTS, len(kept))).astype(int))
        out.append({"channel": f"stress_ranges.{member}", "unit": "MPa", "values": [round(float(x), 4) for x in ranges[pick]],
                    "x": {"quantity": "cycles", "unit": "1", "values": [round(float(x), 1) for x in total[pick]]}})
    return out


def _rev(series):
    from ..k1_frame.fatigue import reversals
    return reversals(series)
