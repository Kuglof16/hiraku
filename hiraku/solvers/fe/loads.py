"""Load types on the fe core: prescribed displacements, pressure on shell and solid faces, temperature.

Every function returns per-node loads (N, 6) (forces, then moments, in global axes) that add to any other
load array, except `prescribe`, which sets values on the model.

- prescribe(model, nodes, comps, value): given displacements (or rotations) of DOFs. The solver
  eliminates them (u = T u_r + g); Solution.reactions() gives the force each one needs, including what
  rigid links and other constraints carry to it.
- shell_pressure: consistent nodal forces of a pressure on one face of MITC4 elements,
  f_a = integral N_a p n dA (MITC4.pressure_forces), moments none.
- solid_pressure: a pressure on solid faces (quad8 faces of HEX20, quad4 of HEX8), the traction -p n with n
  the outward normal, integrated with the face's own shape functions (solid.surface_load). For the
  8-node face this puts -1/12 of a flat square face's load on each corner and 1/3 on each mid-edge node
  (the well-known consistent loads of the serendipity quadrilateral, e.g. Zienkiewicz, Taylor & Zhu 2005,
  not checked here; the test checks the numbers computed here against these fractions).
- beam_thermal, shell_thermal, solid_thermal: equivalent nodal forces of a free thermal strain
  (Cook, Malkus, Plesha & Witt 2002, the chapter on initial strains: f = integral B^T D eps_0 dV; not
  checked here). The stresses of a solution with a thermal load must take off the thermal strain:
  MITC4.stresses(..., thermal=(alpha, T_mid, dT)), solid_stresses(sol, g, thermal=(alpha, T[, nodal])),
  Beam.axial_force(..., thermal=(alpha, dT)).
"""

from __future__ import annotations

import numpy as np

from .model import FEModel
from .solid import surface_load


def scatter(model: FEModel, group, fe: np.ndarray) -> np.ndarray:
    """Per-node loads (N, 6) from element vectors fe (E, n * n_comp) of a group (node-major)."""
    out = np.zeros((len(model.nodes), 6))
    nc = group.n_comp
    f = fe.reshape(len(group.conn), group.conn.shape[1], nc)
    np.add.at(out[:, :nc], group.conn.ravel(), f.reshape(-1, nc))
    return out


def prescribe(model: FEModel, nodes, comps, value: float) -> None:
    """Give the displacement `value` (mm, or rad for comps 3-5) to the listed components of the nodes."""
    for n in np.atleast_1d(np.asarray(nodes, int)):
        for k in np.atleast_1d(np.asarray(comps, int)):
            model.prescribe(int(n), int(k), value)


def shell_pressure(model: FEModel, group, p, side: str = "bottom", elements=None) -> np.ndarray:
    """Pressure p (MPa; one value or per element of the group) on the 'bottom' face (pushes along the
    element normal, e.g. an internal pressure in a tube whose normals point out) or the 'top' face.
    elements: indices of the group's elements that carry it (all when None)."""
    fe = group.pressure_forces(model.nodes, p, side)
    if elements is not None:
        keep = np.zeros(len(group.conn), bool)
        keep[np.asarray(elements, int)] = True
        fe = fe * keep[:, None]
    return scatter(model, group, fe)


def solid_pressure(model: FEModel, faces, p: float, points: int = 3) -> np.ndarray:
    """Pressure p (MPa) on solid faces (F, 8 or 4), corners counter-clockwise seen from outside: the
    traction -p n on the face, n its outward normal (positive p pushes into the solid)."""
    return surface_load(model.nodes, faces, lambda x, n: -p * n, len(model.nodes), points)


def beam_thermal(model: FEModel, group, alpha, dT) -> np.ndarray:
    """Uniform temperature change dT (per element or one value) of beams."""
    return scatter(model, group, group.thermal_forces(model.nodes, alpha, dT))


def shell_thermal(model: FEModel, group, alpha, T_mid, dT=0.0, nodal: bool = False) -> np.ndarray:
    """Mid-surface temperature change T_mid and through-thickness difference dT = T_top - T_bottom of
    MITC4 shells (per element, or per node with nodal=True)."""
    return scatter(model, group, group.thermal_forces(model.nodes, alpha, T_mid, dT, nodal))


def solid_thermal(model: FEModel, group, alpha, T, nodal: bool = False) -> np.ndarray:
    """Temperature change T of solids: per element (E,) or one value, or per node (N,) with nodal=True."""
    return scatter(model, group, group.thermal_forces(model.nodes, alpha, T, nodal))


__all__ = ["beam_thermal", "prescribe", "scatter", "shell_pressure", "shell_thermal", "solid_pressure",
           "solid_thermal"]
