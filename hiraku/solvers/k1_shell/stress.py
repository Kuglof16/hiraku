"""Stresses at the element centres of a solved shell model.

Plane stress in each element's local frame (e1, e2, n; see element.py). The
top surface is the side n points to: the outer side of a tube wall when the
elements are ordered counter-clockwise seen from outside (as the mesher
makes them). z = +t/2 is top, 0 mid, -t/2 bottom. Transverse shear stress
(zero at the surfaces) is not part of the von Mises values.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import element

SURFACES = ("top", "mid", "bottom", "max")


@dataclass
class ShellStress:
    """sigma (E, 3, 3): [surface top/mid/bottom, (sxx, syy, sxy)] in local axes; frames (E, 3, 3) rows e1, e2, n."""

    sigma: np.ndarray
    frames: np.ndarray
    centres: np.ndarray

    def components(self, surface: str) -> np.ndarray:
        return self.sigma[:, ("top", "mid", "bottom").index(surface), :]

    def von_mises(self, surface: str = "max") -> np.ndarray:
        if surface == "max":
            return np.maximum(self.von_mises("top"), self.von_mises("bottom"))
        s = self.components(surface)
        return von_mises(s)

    def in_plane(self, direction, surface: str) -> np.ndarray:
        """(s11, s22, s12) (E, 3) in axes d1 = direction projected on each element's plane, d2 = n x d1."""
        d = np.asarray(direction, float)
        d = np.broadcast_to(d, self.centres.shape)
        dl = np.einsum("eij,ej->ei", self.frames, d)[:, :2]
        nrm = np.linalg.norm(dl, axis=1)
        c = np.where(nrm > 0, dl[:, 0] / np.where(nrm > 0, nrm, 1), 1.0)
        s = np.where(nrm > 0, dl[:, 1] / np.where(nrm > 0, nrm, 1), 0.0)
        sx, sy, txy = self.components(surface).T
        s11 = c * c * sx + s * s * sy + 2 * c * s * txy
        s22 = s * s * sx + c * c * sy - 2 * c * s * txy
        s12 = -c * s * sx + c * s * sy + (c * c - s * s) * txy
        return np.stack([s11, s22, s12], axis=1)


def von_mises(s: np.ndarray) -> np.ndarray:
    """Plane-stress von Mises for rows (sxx, syy, sxy)."""
    sx, sy, txy = s[..., 0], s[..., 1], s[..., 2]
    return np.sqrt(np.maximum(sx * sx - sx * sy + sy * sy + 3.0 * txy * txy, 0.0))


def element_stresses(model, U: np.ndarray) -> ShellStress:
    """Centre stresses of every element for one displacement vector U (n_dof,)."""
    xyz = model.element_xyz()
    ue = np.asarray(U, float)[model.element_dofs()]
    eps, kap, _, R = element.centre_strains(xyz, ue)
    D = element.plane_stress_matrix(model.E, model.nu)
    h = model.thickness / 2.0
    sig = np.stack([np.einsum("eab,eb->ea", D, eps + h[:, None] * kap),
                    np.einsum("eab,eb->ea", D, eps),
                    np.einsum("eab,eb->ea", D, eps - h[:, None] * kap)], axis=1)
    return ShellStress(sig, R, xyz.mean(axis=1))


def along_line(centres: np.ndarray, values: np.ndarray, p0, p1, radius: float):
    """Values of the elements whose centres lie within `radius` of the segment p0-p1.

    Returns (t_mm, index, value) sorted by t, the distance from p0 along the
    segment of each centre's projection.
    """
    p0 = np.asarray(p0, float)
    d = np.asarray(p1, float) - p0
    L = float(np.linalg.norm(d))
    u = d / L
    rel = centres - p0
    t = rel @ u
    off = np.linalg.norm(rel - t[:, None] * u[None, :], axis=1)
    sel = np.nonzero((off <= radius) & (t >= -radius) & (t <= L + radius))[0]
    order = np.argsort(t[sel])
    idx = sel[order]
    return t[idx], idx, np.asarray(values)[idx]


def field_record(mesh_id: str, values: np.ndarray, surface: str, label: str) -> dict:
    """A contract `field` of shell von Mises stress, one value per element."""
    if surface not in SURFACES:
        raise ValueError(f"surface must be one of {', '.join(SURFACES)}")
    return {"kind": "stress", "label": label, "unit": "MPa", "mesh_id": mesh_id,
            "per_element": [float(v) for v in values], "surface": surface}
