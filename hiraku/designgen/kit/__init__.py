"""Parts kit for building designs from variables and choices, so no coordinate is
written by hand: package (placing parts against the frame and each other, or by
searching the free space) and check (what the design is like before any test).
"""

from hiraku.designgen.kit import check, package
from hiraku.designgen.kit.package import Item, Layout, PackageError, bottom_at, front_at, left_at, rear_at, right_at, top_at

__all__ = ["Item", "Layout", "PackageError", "bottom_at", "check", "front_at", "left_at", "package", "rear_at", "right_at",
           "top_at"]
