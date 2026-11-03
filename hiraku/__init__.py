"""HIRAKU: predict by hand, analyse with CAE, compare, judge, revise."""

import os

# numpy and scipy each bundle OpenBLAS, which reserves working memory for every
# thread when it loads (about 750 MB each with 32 threads), counted against the
# Windows commit limit even though little of it is touched. HIRAKU's solves are
# sparse (SuperLU, PARDISO) and its dense matrices small, so one BLAS thread is
# enough; this runs before any hiraku module imports numpy. A value already set
# in the environment wins.
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

__version__ = "0.1.0"
