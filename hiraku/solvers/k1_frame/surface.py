"""K1 beam results painted on the shell mesh of the frame (a contract `mesh` record).

The beam's internal forces along a member (N, My, Mz, T at n_points
stations, as member_stress reads them) give the stress at any point of the
tube wall by beam theory: normal stress -N/A + Mz y/Iz + My z/Iy (PyNite:
N is positive in compression, positive Mz puts local +y in tension and
positive My local +z) and the torsional shear |T| * tau_per_T (Bredt for
closed tubes). Each element gets the von Mises stress at the point of the
tube's outer surface over its centre, so a beam result is no longer one
colour per member. Shear from the transverse forces is left out, as in
member_stress.
"""

from __future__ import annotations

import numpy as np

from ..k0_handcalc.vehicle import DesignError
from .model import COMBO, FrameModel, to_design

N_POINTS = 41
LABEL = "梁の要素から計算した表面の応力 (von Mises、管の外の面)"


def member_axes(fm: FrameModel, member_id: str) -> np.ndarray:
    """Rows: the member's local x, y, z axes (PyNite's) in design coordinates."""
    T = fm.model.members[member_id].T()[:3, :3]
    return np.array([to_design(row) for row in T])


def _outer(dims: dict, shape: str, y: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The point of the outer surface over each mid-surface point (y, z) of the wall."""
    t = float(dims["t"])
    if shape == "round_tube":
        r = np.hypot(y, z)
        k = (float(dims["d"]) / 2.0) / np.where(r > 0, r, 1.0)
        return y * k, z * k
    h, b = float(dims["h"]), float(dims["b"])
    on_flange = np.abs(np.abs(y) - (h - t) / 2.0) <= np.abs(np.abs(z) - (b - t) / 2.0)  # top or bottom wall
    yo = np.where(on_flange, np.sign(y) * h / 2.0, y)
    zo = np.where(on_flange, z, np.sign(z) * b / 2.0)
    return yo, zo


def wall_von_mises(fm: FrameModel, member_id: str, points_mm, n_points: int = N_POINTS) -> np.ndarray:
    """Von Mises stress (MPa) on the outer surface of member_id's tube wall over mid-surface points (design mm)."""
    mp = fm.props[member_id]
    sec = fm.design["sections"][mp["member"]["section"]]
    shape = sec.get("shape")
    if shape not in ("rect_tube", "round_tube"):
        raise DesignError(f"member {member_id!r} has a {shape} section; only rect_tube and round_tube walls are painted")
    p = np.asarray(points_mm, float) - mp["a"]
    ax = member_axes(fm, member_id)
    s, y, z = p @ ax[0], p @ ax[1], p @ ax[2]
    y, z = _outer(sec["dims_mm"], shape, y, z)
    pm = fm.model.members[member_id]
    x, N = pm.axial_array(n_points, COMBO)
    My = pm.moment_array("My", n_points, COMBO)[1]
    Mz = pm.moment_array("Mz", n_points, COMBO)[1]
    T = pm.torque_array(n_points, COMBO)[1]
    sp = mp["section"]
    sigma = (-np.interp(s, x, N) / sp["A"] + np.interp(s, x, Mz) * y / sp["Iz"]
             + np.interp(s, x, My) * z / sp["Iy"])
    tau = np.abs(np.interp(s, x, T)) * sp["tau_per_T"]
    return np.sqrt(sigma**2 + 3.0 * tau**2)


def surface_field(fm: FrameModel, mesh: dict, n_points: int = N_POINTS) -> dict:
    """A stress field with one value per element of the mesh: the beam stress on the outer surface."""
    nodes = np.asarray(mesh["nodes_mm"], float)
    members = np.asarray(mesh["element_member"])
    centres = np.array([nodes[e].mean(axis=0) for e in mesh["elements"]])
    values = np.zeros(len(centres))
    for mid in np.unique(members):
        if mid not in fm.props:
            raise DesignError(f"mesh {mesh.get('mesh_id')} has elements of {mid!r}, which is not a member of this design")
        at = members == mid
        values[at] = wall_von_mises(fm, str(mid), centres[at], n_points)
    if not np.all(np.isfinite(values)):
        raise DesignError("the beam stress painted on the mesh is not finite")
    return {"kind": "stress", "label": LABEL, "unit": "MPa", "mesh_id": mesh["mesh_id"],
            "per_element": [float(v) for v in values], "surface": "top"}
