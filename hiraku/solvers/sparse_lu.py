"""The complete sparse LU that the shell solvers (k1_shell, fe) factorise their stiffness with.

SuperLU through scipy's splu reserves about 20 x nnz(A) entries for each of L and U before it starts (its
fill estimate) and keeps them with the factors: on the demo frame at 10 mm (296,778 free DOF) that is 7.6 GB
of committed memory for factors of 116 M entries (about 1.4 GB). spilu with no dropping (drop_tol 0, the
basic rule only) runs the same SuperLU factorisation but takes its first reservation from fill_factor and
grows it as the factors need. On that matrix: the same fill (116.3 M) and residual (7.4e-16), 2.1 GB at the
peak and 1.6 GB held, the factorisation 11.5 s; on the space frame's tube model (146,502 DOF) 1.2 GB at the
peak instead of 5.9 GB (measured here with scipy 1.18 and one BLAS thread). A zero pivot still raises
RuntimeError, as splu does.
"""

from __future__ import annotations

import scipy.sparse as sp
import scipy.sparse.linalg as spla

LU_FILL_GUESS = 4.0


def superlu(A: sp.csc_matrix):
    """The complete LU of a symmetric matrix by SuperLU (MMD_AT_PLUS_A ordering, symmetric mode, no pivoting
    threshold), reserving LU_FILL_GUESS x nnz(A) to start. Raises RuntimeError when a pivot is zero."""
    return spla.spilu(A, drop_tol=0.0, fill_factor=LU_FILL_GUESS, drop_rule="basic", permc_spec="MMD_AT_PLUS_A",
                      diag_pivot_thresh=0.0, options={"SymmetricMode": True, "ILU_MILU": "SILU"})
