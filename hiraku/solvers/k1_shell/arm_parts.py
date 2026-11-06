"""The suspension arms as the design records them, read by the solver (no import of hiraku.designgen: the
layer order puts solvers below designgen, tests/contract/test_layers.py).

- part ids: the generator's names of a corner's arm parts (lca_<sid>_f, lca_<sid>_r, lca_<sid>_x, uca_<sid>_f,
  uca_<sid>_r, damper_<sid>, bj_lower_<sid>, bj_upper_<sid>). This naming is read here as the contract of the
  generator's part names (the generator writes them, hiraku/designgen/kit/arms.py); the solver reads the
  names, it does not import them. A part is taken for an arm leg only when the design also calls it a link
  of the suspension (kind "link", system "suspension").
- the arm tube's section: the outside diameter from an arm leg's box cross-section (a link's box is
  [length + d, d, d], or the length on the one axis it runs along), the wall from the part's mass, its length
  and the material's density: m = rho * pi * (d - t) * t * L, so t = (d - sqrt(d^2 - 4 m / (rho pi L))) / 2.
  A design without such a part: FALLBACK_D_MM x FALLBACK_T_MM, an assumption, as the generator's default
  (not checked against a source), and a note that says so.
- the lower arm's cross tube ends: recomputed from the hard points with the generator's rule (this repeats it):
  on the front and rear legs (lca_front -> lca_outer, lca_rear -> lca_outer) at damper_lower's side position y,
  clamped to the leg, the leg's middle when the leg does not run sideways.
"""

from __future__ import annotations

import math

import numpy as np

FALLBACK_D_MM = 25.4  # arm tube outside diameter when the design has no arm part (assumption, as the generator's default)
FALLBACK_T_MM = 2.0  # its wall (assumption, as the generator's default)
LEG_ROLES = ("lca_f", "lca_r", "uca_f", "uca_r")  # the arm legs, read for the tube's section


def part_ids(sid: str) -> dict[str, str]:
    """The generator's part names of support sid's arms, by role (the contract of its names, see the module)."""
    return {"lca_f": f"lca_{sid}_f", "lca_r": f"lca_{sid}_r", "lca_x": f"lca_{sid}_x", "uca_f": f"uca_{sid}_f",
            "uca_r": f"uca_{sid}_r", "damper": f"damper_{sid}", "bj_lower": f"bj_lower_{sid}",
            "bj_upper": f"bj_upper_{sid}"}


def tube_mass(length_mm: float, d_mm: float, t_mm: float, rho: float) -> float:
    """kg of a round tube d x t, length_mm long; rho in kg/m^3."""
    return rho * math.pi * (d_mm - t_mm) * t_mm * length_mm * 1e-9


def _on_leg(a: np.ndarray, b: np.ndarray, y: float) -> np.ndarray:
    t = 0.5 if abs(b[1] - a[1]) < 1e-9 else float(np.clip((y - a[1]) / (b[1] - a[1]), 0.0, 1.0))
    return a + t * (b - a)


def cross_ends(hp: dict) -> tuple[np.ndarray, np.ndarray]:
    """The lower arm's cross tube ends (mm), on its front and rear legs at damper_lower's side position (the
    generator's rule, repeated here)."""
    p = {k: np.asarray(hp[k], float) for k in ("lca_front", "lca_rear", "lca_outer", "damper_lower")}
    y = p["damper_lower"][1]
    return _on_leg(p["lca_front"], p["lca_outer"], y), _on_leg(p["lca_rear"], p["lca_outer"], y)


def arm_section(design: dict, sid: str, rho: float) -> tuple[float, float, str]:
    """(outside diameter mm, wall mm, where they come from) of support sid's arm tubes, from the first arm leg
    part of the corner the design has with a box and a mass; else the fallback."""
    by_id = {p.get("id"): p for p in design.get("parts", [])}
    ids = part_ids(sid)
    for role in LEG_ROLES:
        p = by_id.get(ids[role])
        if p is None or str(p.get("kind", "")).lower() != "link" or p.get("system", "suspension") != "suspension":
            continue
        size = (p.get("box") or {}).get("size_mm")
        m = p.get("mass_kg")
        if not size or len(size) != 3 or m is None or float(m) <= 0.0:
            continue
        s = sorted(float(v) for v in size)
        d, L = s[0], s[2] - s[0]  # the box is the length plus d along the tube, d across it
        if L <= 0.0 or abs(s[1] - s[0]) > 1e-6 * s[0]:
            continue
        c = float(m) / (rho * math.pi * L * 1e-9)  # (d - t) t
        disc = d * d - 4.0 * c
        if disc < 0.0:
            continue
        t = (d - math.sqrt(disc)) / 2.0
        return d, t, (f"arm tubes of {sid}: {d:g} mm across from {p['id']}'s box, wall {t:.4g} mm from its "
                      f"{float(m):.4g} kg over {L:.4g} mm at {rho:g} kg/m^3")
    return FALLBACK_D_MM, FALLBACK_T_MM, (
        f"arm tubes of {sid}: {FALLBACK_D_MM:g} x {FALLBACK_T_MM:g} mm, an assumption as the generator's default "
        "(the design has no arm leg part with a box and a mass to read them from)")


__all__ = ["FALLBACK_D_MM", "FALLBACK_T_MM", "arm_section", "cross_ends", "part_ids", "tube_mass"]
