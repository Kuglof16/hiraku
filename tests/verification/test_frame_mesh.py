"""The analysis dept's shell mesh of a frame (hiraku.solvers.frame_mesh, an optional entry point).

Skipped until the entry point lands. On the demo design the mesh must fit the
contract and check_run's rules for meshes, name every tube member with the wall
thickness of its section, and put the walls where the section says: away from
the joints, each element's centre is between half the narrower mid-line side and
half the mid-line diagonal from the member's axis.
"""

import importlib
import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract
from hiraku.contract.protocols import OPTIONAL_SOLVER_ENTRY_POINTS
from qa import check_run as cr

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "data" / "designs" / "offroad-4w-v1.json"


def frame_mesh_or_skip():
    assert "frame_mesh" in OPTIONAL_SOLVER_ENTRY_POINTS
    try:
        solvers = importlib.import_module("hiraku.solvers")
    except ModuleNotFoundError:
        raise unittest.SkipTest("hiraku.solvers is not in this checkout yet (analysis dept)")
    fn = getattr(solvers, "frame_mesh", None)
    if not callable(fn):
        raise unittest.SkipTest("hiraku.solvers.frame_mesh() is not there yet (analysis dept, optional entry point)")
    return fn


class FrameMesh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frame_mesh = frame_mesh_or_skip()
        if not DESIGN.exists():
            raise unittest.SkipTest("needs data/designs/offroad-4w-v1.json (design dept)")
        cls.design = json.loads(DESIGN.read_text(encoding="utf-8"))
        cls.mesh = dict(frame_mesh(cls.design), mesh_id="mesh-0001")

    def tube_members(self):
        secs = self.design.get("sections", {})
        return {m["id"]: secs[m["section"]] for m in self.design["members"]
                if secs.get(m.get("section"), {}).get("shape") == "rect_tube"}

    def test_fits_the_contract_and_the_mesh_rules(self):
        self.assertEqual(contract.errors("mesh", self.mesh), [])
        self.assertEqual(self.mesh["design_version"], self.design["design_version"])
        checker = cr.RunChecker(ROOT)
        checker.where_of = ["mesh.created"]
        checker.designs[self.design["design_version"]] = (-1, self.design)
        checker.recs["mesh"]["mesh-0001"] = (0, self.mesh)
        checker.check_meshes()
        # warnings (e.g. walls facing opposite ways across an edge) are for people to look at
        self.assertEqual([str(f) for f in checker.findings if f.level == "error"], [],
                         "\n".join(str(f) for f in checker.findings))

    def test_each_tube_member_with_its_wall_thickness(self):
        named = set(self.mesh["element_member"])
        for mid, sec in self.tube_members().items():
            with self.subTest(member=mid):
                self.assertIn(mid, named)
                self.assertAlmostEqual(self.mesh["member_thickness_mm"][mid], sec["dims_mm"]["t"], places=9)

    def test_walls_lie_on_the_section(self):
        nodes = {n["id"]: np.array(n["xyz_mm"], float) for n in self.design["nodes"]}
        pts = np.array(self.mesh["nodes_mm"], float)
        by_member = {m["id"]: m for m in self.design["members"]}
        for mid, sec in self.tube_members().items():
            m = by_member[mid]
            a, b = nodes[m["node_a"]], nodes[m["node_b"]]
            axis = (b - a) / np.linalg.norm(b - a)
            d = sec["dims_mm"]
            bm, hm = d["b"] - d["t"], d["h"] - d["t"]
            lo, hi = min(bm, hm) / 2, math.hypot(bm, hm) / 2
            off = []
            for el, owner in zip(self.mesh["elements"], self.mesh["element_member"]):
                if owner != mid:
                    continue
                c = pts[el].mean(axis=0)
                s = float((c - a) @ axis) / float(np.linalg.norm(b - a))
                if not 0.1 <= s <= 0.9:  # near a joint
                    continue
                r = float(np.linalg.norm((c - a) - ((c - a) @ axis) * axis))
                if not lo - 0.5 <= r <= hi + 0.5:
                    off.append(round(r, 2))
            with self.subTest(member=mid):
                self.assertEqual(off, [], f"element centres {lo:.1f}..{hi:.1f} mm from the axis expected")


if __name__ == "__main__":
    unittest.main()
