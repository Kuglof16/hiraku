"""A hand-checkable mount bracket for the shell tests: one clevis on the top wall of a straight, thick rect tube
(100 x 100 x 6 mm, 1000 mm long, held at both ends), its base line across the tube inside the top wall, the pin
along x. Used by tests/solvers/test_shell_brackets.py."""

from __future__ import annotations

import numpy as np

B_MM, E_MM, H_MM = 60.0, 60.0, 80.0  # plate base width, pin height above the base face, plate height (box)
JOINT = {"bolt_d_mm": 10.0, "bolts": 1, "plate_t_mm": 3.2, "weld_leg_mm": 3.0}
TOP = 50.0  # the tube's outer top surface (z)


def design(b: float = B_MM, e: float = E_MM, h: float = H_MM) -> dict:
    nodes = [{"id": "A", "xyz_mm": [-500.0, 0.0, 0.0]}, {"id": "C", "xyz_mm": [0.0, 0.0, 0.0]},
             {"id": "B", "xyz_mm": [500.0, 0.0, 0.0]}]
    members = [{"id": "m1", "node_a": "A", "node_b": "C", "section": "rt100", "material": "steel"},
               {"id": "m2", "node_a": "C", "node_b": "B", "section": "rt100", "material": "steel"}]
    hp = [0.0, 0.0, TOP + e]
    return {
        "nodes": nodes, "members": members,
        "sections": {"rt100": {"shape": "rect_tube", "dims_mm": {"b": 100.0, "h": 100.0, "t": 6.0}}},
        "materials": {"steel": {"E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0, "yield_MPa": 245.0}},
        "supports": [{"id": "S", "frame_node": "C", "position_mm": [0.0, 0.0, 0.0],
                      "suspension": {"hardpoints_mm": {"uca_front": hp}}}],
        "parts": [{"id": "bracket_uca_S1", "kind": "bracket", "carried_by": ["C"], "mass_kg": 0.2,
                   "box": {"center_mm": [0.0, 0.0, TOP + h / 2.0], "size_mm": [50.0, b, h]}, "joint": dict(JOINT)}],
    }


def solve(d: dict, force, size: float = 10.0):
    """(mesh, model, U, values) with the tube's end rings held and `force` (N) on the bracket's pin."""
    from hiraku.solvers.k1_shell.brackets import bracket_values, pin_load
    from hiraku.solvers.k1_shell.mesh import build_mesh
    from hiraku.solvers.k1_shell.solve import ShellModel, StaticSolver, dofs_of
    from hiraku.solvers.k1_shell.stress import element_stresses

    ms = build_mesh(d, size)
    model = ShellModel.from_mesh(ms, d)
    ends = np.nonzero(np.abs(np.abs(ms.nodes[:, 0]) - 500.0) < 1e-6)[0]
    solver = StaticSolver(model, dofs_of(ends, "all"))
    F = pin_load(ms.nodes, ms.brackets[0], np.asarray(force, float), model.n_dof)
    U = solver.solve(F)
    vm = element_stresses(model, U).von_mises("max")
    return ms, model, U, bracket_values(model, ms, U, vm)
