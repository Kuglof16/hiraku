"""design_to_step, read back with read_step, against volumes and boxes computed here from the design."""

import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku.cad import CadError
from hiraku.cad.step_read import read_step
from hiraku.cad.step_write import design_to_step

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "data" / "designs" / "offroad-4w-v1.json"
TOL = 0.005  # 0.5 %


def _rot(rpy):
    r, p, y = np.radians(rpy)
    cx, sx, cy, sy, cz, sz = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]) @ np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]) \
        @ np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])


def _axes(d):
    """The member's section axes: ez horizontal (d x z), ey = ez x d; vertical: ey = -/+x, ez = -y."""
    c = np.cross(d, [0, 0, 1.0])
    if np.linalg.norm(c) < 1e-9:
        return (np.array([-1.0, 0, 0]) if d[2] > 0 else np.array([1.0, 0, 0])), np.array([0, -1.0, 0])
    ez = c / np.linalg.norm(c)
    return np.cross(ez, d), ez


def expected(design):
    """name -> (volume mm3, bbox min, bbox max) from the design alone."""
    nodes = {n["id"]: np.array(n["xyz_mm"], float) for n in design["nodes"]}
    out = {}
    for m in design["members"]:
        A, B = nodes[m["node_a"]], nodes[m["node_b"]]
        L = float(np.linalg.norm(B - A))
        d = (B - A) / L
        sec = design["sections"][m["section"]]
        dm = sec["dims_mm"]
        if sec["shape"] in ("rect_tube", "rect_solid"):
            b, h = dm["b"], dm["h"]
            inner = (b - 2 * dm["t"]) * (h - 2 * dm["t"]) if sec["shape"] == "rect_tube" else 0.0
            vol = (b * h - inner) * L
            ey, ez = _axes(d)
            pts = [P + sy * h / 2 * ey + sz * b / 2 * ez for P in (A, B) for sy in (-1, 1) for sz in (-1, 1)]
            lo, hi = np.min(pts, axis=0), np.max(pts, axis=0)
        else:
            D = dm["d"]
            inner = (D - 2 * dm["t"]) ** 2 if sec["shape"] == "round_tube" else 0.0
            vol = math.pi / 4 * (D * D - inner) * L
            half = D / 2 * np.sqrt(np.clip(1 - d * d, 0, 1))
            lo, hi = np.minimum(A, B) - half, np.maximum(A, B) + half
        out[m["id"]] = (vol, lo, hi)
    for p in design["parts"]:
        s = np.array(p["box"]["size_mm"], float)
        R = _rot(p["box"].get("rpy_deg") or [0, 0, 0])
        c = np.array(p["box"]["center_mm"], float)
        pts = [c + R @ (np.array([i, j, k]) * s / 2) for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)]
        out[p["id"]] = (float(np.prod(s)), np.min(pts, axis=0), np.max(pts, axis=0))
    return out


class WriteSampleDesignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = json.loads(DESIGN.read_text(encoding="utf-8"))
        cls.text = design_to_step(cls.design)
        cls.solids = read_step(cls.text)

    def test_header_and_one_solid_per_member_and_part(self):
        self.assertTrue(self.text.startswith("ISO-10303-21;"))
        self.assertIn("AUTOMOTIVE_DESIGN", self.text)
        self.assertIn("SI_UNIT(.MILLI.,.METRE.)", self.text)
        want = [m["id"] for m in self.design["members"]] + [p["id"] for p in self.design["parts"]]
        self.assertEqual(len(self.solids), len(want))
        self.assertEqual(sorted(s.name for s in self.solids), sorted(want))

    def test_volume_and_outer_size_within_half_percent(self):
        exp = expected(self.design)
        worst_v, worst_s = 0.0, 0.0
        for s in self.solids:
            vol, lo, hi = exp[s.name]
            worst_v = max(worst_v, abs(s.volume_mm3 - vol) / vol)
            size = hi - lo
            for k in range(3):
                worst_s = max(worst_s, abs(s.size_mm[k] - size[k]) / max(size[k], 1.0))
                self.assertAlmostEqual(s.bbox_min[k], lo[k], delta=max(TOL * size[k], 0.05), msg=s.name)
        self.assertLess(worst_v, TOL)
        self.assertLess(worst_s, TOL)
        print(f"\n[cad] {len(self.solids)} solids: worst volume error {worst_v:.2e}, worst size error {worst_s:.2e}")

    def test_hollow_tubes_have_their_wall_only(self):
        by = {s.name: s for s in self.solids}
        rt = next(m for m in self.design["members"] if self.design["sections"][m["section"]]["shape"] == "rect_tube")
        self.assertEqual(by[rt["id"]].method, "exact")
        ct = next(m for m in self.design["members"] if self.design["sections"][m["section"]]["shape"] == "round_tube")
        self.assertEqual(by[ct["id"]].method, "approximate")


class WriteShapesTest(unittest.TestCase):
    def design(self, sections, parts=()):
        nodes = [{"id": "a", "xyz_mm": [0, 0, 0]}, {"id": "b", "xyz_mm": [300, 200, 100]}, {"id": "c", "xyz_mm": [0, 0, 500]}]
        members = [{"id": f"m_{k}", "node_a": "a", "node_b": "b" if i % 2 == 0 else "c", "section": k, "material": "x"}
                   for i, k in enumerate(sections)]
        return {"name": "t", "nodes": nodes, "members": members, "sections": sections, "parts": list(parts)}

    def test_every_shape_written_reads_back(self):
        d = self.design({"rt": {"shape": "rect_tube", "dims_mm": {"b": 30, "h": 60, "t": 3}},
                         "ct": {"shape": "round_tube", "dims_mm": {"d": 48.6, "t": 3.2}},
                         "rs": {"shape": "rect_solid", "dims_mm": {"b": 20, "h": 10}},
                         "cs": {"shape": "round_solid", "dims_mm": {"d": 25}}},
                        [{"id": "box", "name": "box", "kind": "battery", "mass_kg": 1,
                          "box": {"center_mm": [1, 2, 3], "size_mm": [100, 50, 20], "rpy_deg": [10, 20, 30]}}])
        exp = expected(d)
        solids = read_step(design_to_step(d))
        self.assertEqual(len(solids), 5)
        for s in solids:
            vol, lo, hi = exp[s.name]
            self.assertLess(abs(s.volume_mm3 - vol) / vol, TOL, s.name)
            for k in range(3):
                self.assertAlmostEqual(s.size_mm[k], (hi - lo)[k], delta=TOL * (hi - lo)[k], msg=s.name)

    def test_unknown_shape_is_named(self):
        d = self.design({"ch": {"shape": "channel", "dims_mm": {"b": 30, "h": 60, "t": 3}}})
        with self.assertRaises(CadError) as c:
            design_to_step(d)
        self.assertIn("channel", str(c.exception))
        self.assertIn("m_ch", str(c.exception))

    def test_missing_dims_are_named(self):
        d = self.design({"rt": {"shape": "rect_tube", "dims_mm": {"b": 30, "h": 60}}})
        with self.assertRaises(CadError) as c:
            design_to_step(d)
        self.assertIn("t", str(c.exception))


if __name__ == "__main__":
    unittest.main()
