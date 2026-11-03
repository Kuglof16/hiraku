"""Stresses at the element centres of a solved shell model.

Plane stress in each element's local frame (e1, e2, n; see element.py). The
top surface is the side n points to: the outer side of a tube wall when the
elements are ordered counter-clockwise seen from outside (as the mesher
makes them). z = +t/2 is top, 0 mid, -t/2 bottom. Transverse shear stress
(zero at the surfaces) is not part of the von Mises values.

hot_spots reads the structural hot-spot stress along the weld lines of a
mesh made with weld strips (HOT_SPOT_SOURCE).
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


HOT_SPOT_READ_T = (0.5, 1.5)  # read points, in wall thicknesses from the weld line (centres of the strip elements)
CORNER_SKIP_T = 2.0  # read points closer than this many t to a footprint corner along the weld line are not read
HOT_SPOT_SOURCE = ("IIW structural hot-spot stress with shell elements of size t x t: the surface stress square to "
                   "the weld line read at 0.5 t and 1.5 t from it and extrapolated linearly to the line (A. F. "
                   "Hobbacher, Recommendations for Fatigue Design of Welded Joints and Components, 2nd ed., Springer "
                   "2016; the source was not checked in this repository)")


def hot_spot_factors(read_t=HOT_SPOT_READ_T) -> tuple[float, float]:
    """(a, b) with sigma_hs = a sigma(x1) + b sigma(x2): the straight line through the two read points at 0."""
    x1, x2 = read_t
    return x2 / (x2 - x1), -x1 / (x2 - x1)


def extrapolate_hot_spot(s_near, s_far, read_t=HOT_SPOT_READ_T):
    """sigma_hs from the stresses at the two read points (1.5 sigma(0.5 t) - 0.5 sigma(1.5 t) for the IIW points)."""
    a, b = hot_spot_factors(read_t)
    return a * np.asarray(s_near) + b * np.asarray(s_far)


def axis_name(v) -> str:
    """'+x', '-z', ... for a unit vector along a design axis, else its components rounded."""
    v = np.asarray(v, float)
    k = int(np.argmax(np.abs(v)))
    if abs(abs(v[k]) - 1.0) < 1e-6:
        return ("+" if v[k] > 0 else "-") + "xyz"[k]
    return str([round(float(x), 3) for x in v])


def _normal_stress(st: ShellStress, idx: np.ndarray, direction: np.ndarray, surface: str) -> np.ndarray:
    """Surface stress along `direction` (projected on each element's plane) of elements idx."""
    dl = st.frames[idx] @ np.asarray(direction, float)
    nrm = np.linalg.norm(dl[:, :2], axis=1)
    c, s = dl[:, 0] / nrm, dl[:, 1] / nrm
    sx, sy, txy = st.components(surface)[idx].T
    return c * c * sx + s * s * sy + 2 * c * s * txy


def hot_spots(mesh, st: ShellStress, excluded: np.ndarray | None = None, tol: float = 1e-3) -> list[dict]:
    """Structural hot-spot stresses along the weld lines of a mesh made with weld strips (HOT_SPOT_SOURCE).

    For every weld line segment (mesh.weld_lines) the elements of its tube
    whose centres lie in the strip's wall at 0.5 t and 1.5 t from the line,
    and whose foot on the line falls between its ends, are paired along the
    same normal line (the same distance along the line). The footprint's
    corner squares, where the distances to both weld lines that meet at a
    corner are under 2t, are not read: elements beyond the segment's ends,
    and those whose foot is within CORNER_SKIP_T t of either end (the mesher
    puts grid lines at t, 2t and 3t from each corner, so the first read point
    is 2.5t from the corner whatever the element size). Pairs with an
    element in `excluded` (the wheel mounts) are not read. For each pair and
    each surface (top: the wall's outer side; bottom: inner) the stress
    component square to the weld line is extrapolated to it: sigma_hs =
    1.5 sigma(0.5 t) - 0.5 sigma(1.5 t).

    Returns dicts: value (MPa, signed), surface, joint, kind, side ("host" or
    "branch"), place, member (of the element at 0.5 t), position_mm (the
    normal line's foot on the weld line), along_mm (from the segment's
    start), sigma_05t, sigma_15t, elements (at 0.5 t, at 1.5 t), t_mm,
    direction (unit vector square to the line, into the strip) and towards
    (its name, "+x", "-z", ...).
    """
    members = np.asarray(mesh.element_member)
    cand_of: dict[tuple, np.ndarray] = {}
    out: list[dict] = []
    for wl in mesh.weld_lines:
        if wl.members not in cand_of:
            cand_of[wl.members] = np.nonzero(np.isin(members, list(wl.members)))[0]
        cand = cand_of[wl.members]
        u = wl.p1 - wl.p0
        L = float(np.linalg.norm(u))
        u = u / L
        rel = st.centres[cand] - wl.p0
        a, d = rel @ u, rel @ wl.away
        skip = CORNER_SKIP_T * wl.t
        ok = (np.abs(rel @ wl.normal) < tol) & (a > skip - tol) & (a < L - skip + tol)
        first = ok & (np.abs(d - HOT_SPOT_READ_T[0] * wl.t) < tol)
        second = ok & (np.abs(d - HOT_SPOT_READ_T[1] * wl.t) < tol)
        if not first.any() or not second.any():
            continue
        e1, a1 = cand[first], a[first]
        order = np.argsort(a[second])
        e2s, a2s = cand[second][order], a[second][order]
        j = np.searchsorted(a2s, a1)
        below, above = np.clip(j - 1, 0, len(a2s) - 1), np.clip(j, 0, len(a2s) - 1)
        k = np.where(np.abs(a2s[below] - a1) <= np.abs(a2s[above] - a1), below, above)  # nearest along the line
        hit = np.abs(a2s[k] - a1) < tol
        if excluded is not None:
            hit &= ~excluded[e1] & ~excluded[e2s[k]]
        e1, a1, e2 = e1[hit], a1[hit], e2s[k[hit]]
        if len(e1) == 0:
            continue
        for surface in ("top", "bottom"):
            s1 = _normal_stress(st, e1, wl.away, surface)
            s2 = _normal_stress(st, e2, wl.away, surface)
            hs = extrapolate_hot_spot(s1, s2)
            for i in range(len(e1)):
                out.append({"value": float(hs[i]), "surface": surface, "joint": wl.joint, "kind": wl.kind,
                            "side": wl.side, "place": wl.place, "member": str(members[e1[i]]),
                            "position_mm": wl.p0 + a1[i] * u, "along_mm": float(a1[i]),
                            "sigma_05t": float(s1[i]), "sigma_15t": float(s2[i]),
                            "elements": (int(e1[i]), int(e2[i])), "t_mm": float(wl.t), "direction": wl.away,
                            "towards": axis_name(wl.away)})
    return out


def field_record(mesh_id: str, values: np.ndarray, surface: str, label: str) -> dict:
    """A contract `field` of shell von Mises stress, one value per element."""
    if surface not in SURFACES:
        raise ValueError(f"surface must be one of {', '.join(SURFACES)}")
    return {"kind": "stress", "label": label, "unit": "MPa", "mesh_id": mesh_id,
            "per_element": [float(v) for v in values], "surface": surface}
