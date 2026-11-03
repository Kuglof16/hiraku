"""K1 shell: mid-surface shell mesh of the frame tubes, MITC4 elements, static solution and stresses.

    mesh = build_mesh(design, element_size_mm=10.0)        # mesh.py
    model = ShellModel.from_mesh(mesh, design)             # solve.py
    solver = StaticSolver(model, fixed_dofs)               # factorised once
    U = solver.solve(F)                                    # (n_dof,) or (n_dof, n_cases)
    st = element_stresses(model, U[:, 0])                  # stress.py; st.von_mises("top" | "mid" | "bottom" | "max")
    shell_known_answers()                                  # checks.py, tool_check records
"""

from .checks import shell_known_answers
from .mesh import ShellMesh, build_mesh
from .solve import ShellModel, StaticSolver, dofs_of, spread_load
from .stress import ShellStress, along_line, element_stresses, field_record

__all__ = ["ShellMesh", "ShellModel", "ShellStress", "StaticSolver", "along_line", "build_mesh", "dofs_of",
           "element_stresses", "field_record", "shell_known_answers", "spread_load"]
