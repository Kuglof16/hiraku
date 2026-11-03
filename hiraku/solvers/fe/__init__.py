"""fe: one finite-element core for solids, shells and connections in a single model.

    from hiraku.solvers import fe
    model = fe.FEModel(nodes)                               # model.py: nodes with 0, 3 or 6 DOFs
    model.add(fe.solid.Hex20(conn, E, nu, rho))             # solid.py: HEX20 (3x3x3) / Hex8 (2x2x2)
    model.add(fe.shell.MITC4(conn, t, E, nu, rho))          # shell.py: k1_shell's MITC4 as a group
    model.add(fe.connect.Spring(pairs, k, frames))          # connect.py: springs and bushings
    model.add(fe.connect.rigid_link(m, slaves, model.nodes))         # RBE2
    model.add(fe.connect.distributing_link(ref, ids, model.nodes))   # RBE3
    model.add(fe.connect.shell_to_solid(...)); model.add(fe.connect.tie(...))
    model.fix(nodes, "all")                                 # supports
    sol = fe.StaticSolver(model).solve(F)                   # F: (N, 6) per node or a DOF vector
    sol.displacements, sol.reactions(), sol.element_forces(group)
    out = fe.analysis.run_static(model, F, design_mass_kg, intended)  # quality -> rigid check -> solve -> balance
    fe.quality.record_quality(mesh_record)                  # contract mesh.quality of a shell mesh record
    fe.mesh.parts_mesh(design, size)                        # bricks from the design parts' boxes

Units: N, mm, MPa, tonne (density t/mm^3).
"""

from . import analysis, checks, connect, mesh, precheck, quality, shell, solid
from .model import Constraint, ElementGroup, Equation, FEModel, Layout, ModelError, Solution, StaticSolver, resultant, \
    solve_static

__all__ = ["Constraint", "ElementGroup", "Equation", "FEModel", "Layout", "ModelError", "Solution", "StaticSolver",
           "analysis", "checks", "connect", "mesh", "precheck", "quality", "resultant", "shell", "solid",
           "solve_static"]
