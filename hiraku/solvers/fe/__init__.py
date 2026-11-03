"""fe: one finite-element core for solids, shells and connections in a single model.

    from hiraku.solvers import fe
    model = fe.FEModel(nodes)                               # model.py: nodes with 0, 3 or 6 DOFs
    model.add(group)                                        # an element group (HEX20, MITC4 shells, springs ...)
    model.add(constraint)                                   # equations (rigid link, distributing link, tie ...)
    model.fix(nodes, "all")                                 # supports
    sol = fe.StaticSolver(model).solve(F)                   # F: (N, 6) per node or a DOF vector
    sol.displacements, sol.reactions(), sol.element_forces(group)
    fe.precheck.rigid_body_check(model)                     # free rigid-body motions, before factorising
    fe.precheck.equilibrium(sol), fe.precheck.mass_check(model, design_mass_kg)

Units: N, mm, MPa, tonne (density t/mm^3).
"""

from . import precheck
from .model import Constraint, ElementGroup, Equation, FEModel, Layout, ModelError, Solution, StaticSolver, resultant, \
    solve_static

__all__ = ["Constraint", "ElementGroup", "Equation", "FEModel", "Layout", "ModelError", "Solution", "StaticSolver",
           "precheck", "resultant", "solve_static"]
