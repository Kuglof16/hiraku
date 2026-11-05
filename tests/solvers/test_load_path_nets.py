"""load_path: nets (parts of a kind in NET_KINDS) as tension-only cables between their fastenings, solved
geometrically non-linear (hiraku.solvers.shelf.load_path.net_parts, net_mesh, Cable, newton_solve)."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import designgen
from hiraku.solvers.shelf import load_path as LP
from tests.solvers.test_load_path import VALUES, quantity

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
SET2 = {"part_mounts": "brackets", "pedal_mount": "bulkhead", "side_guard": "bars_net"}
SQ = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.0}}
RHO = 7850.0


def square_net_design(side: float = 600.0, d: float = 3.0, pitch: float = 50.0, edges=("bottom", "top", "left", "right"),
                      wall: float = 2.0):
    """A net in the x-z plane inside a square of four members (side x side between the axes), touching those of
    `edges`; the net's mass is two directions of wires d at pitch over its area."""
    h = side / 2.0
    nodes = {"A": (-h, 0.0, -h), "B": (h, 0.0, -h), "C": (h, 0.0, h), "D": (-h, 0.0, h)}
    members = {"bottom": ("A", "B"), "right": ("B", "C"), "top": ("C", "D"), "left": ("D", "A")}
    inner = side - 40.0  # between the tubes' faces
    mass = 2.0 * math.pi * d * d / 4.0 / pitch * RHO * 1e-9 * inner * inner
    net = {"id": "net", "name": "net", "kind": "side_net", "box": {"center_mm": [0.0, 0.0, 0.0],
                                                                    "size_mm": [inner, d, inner]},
           "mass_kg": mass, "carried_by": ["A"], "material": "steel"}
    d_ = LP._bare(nodes, [(m, *members[m]) for m in edges], [net], dict(SQ, dims_mm=dict(SQ["dims_mm"], t=wall)))
    d_["materials"]["steel"] = dict(LP._steel(), density_kg_m3=RHO)
    return d_


def push_middle(design, prestress=LP.NET_PRESTRESS_MPA, P=500.0):
    pts = {"net": [np.array([0.0, 0.0, 0.0])]}
    cs = LP.find_contacts(design)
    holds = {n["id"]: "all" for n in design["nodes"]}
    lp = LP.build(design, cs, holds=holds, plate_points=pts, net_prestress=prestress)
    node = lp.net_node("net", [0.0, -1.5, 0.0])
    F = np.zeros((len(lp.model.nodes), 6))
    F[node, 1] = P
    (U,), _ = LP.solve_cases(lp, [F])
    return lp, float(U[node, 1])


class CableKnownAnswerTest(unittest.TestCase):
    def test_taut_cable(self):
        rs = LP.cable_known_answers()
        self.assertEqual([r["quantity"] for r in rs], ["linear_deflection", "sag"])
        for r in rs:
            self.assertTrue(r["pass"], r)
            self.assertIn(r, LP.load_path_known_answers())
        # the stretch makes the cable far stiffer than its pretension alone
        self.assertLess(rs[1]["got"], rs[0]["got"] / 5.0)

    def test_taut_cable_formula_limits(self):
        # no stretch (EA 0): the exact string P = 2 T0 w / sqrt(a^2 + w^2)
        P, L, T0 = 100.0, 1000.0, 400.0
        w = LP.taut_cable_deflection(P, L, 0.0, T0)
        self.assertAlmostEqual(2 * T0 * w / math.hypot(L / 2, w), P, places=6)
        # no pretension: w^3 ~ P a^3 / EA for a small angle
        EA = 205000.0 * 7.0
        w = LP.taut_cable_deflection(P, L, EA, 0.0)
        self.assertAlmostEqual(w, (P * (L / 2) ** 3 / EA) ** (1 / 3), delta=0.01 * w)

    def test_tension_only(self):
        c = LP.Cable([(0, 1)], [1000.0], [10.0], np.array([[0.0, 0, 0], [100.0, 0, 0]]))
        c.disp = np.array([[[0.0, 0, 0], [-2.0, 0, 0]]])  # shortened 2 %: 10 - 20 < 0
        self.assertEqual(float(c.tensions(np.array([[0.0, 0, 0], [100.0, 0, 0]]))[0]), 0.0)
        x = np.array([[0.0, 0, 0], [100.0, 0, 0]])
        K = c.stiffness(x)
        # slack: a millionth of the axial stiffness, and the floor across (Newton's step stays defined)
        self.assertAlmostEqual(K[0, 0, 0], LP.SLACK_FLOOR * 1000.0 / 100.0)
        self.assertAlmostEqual(K[0, 1, 1], LP.SLACK_FLOOR * 1000.0 / 98.0)
        # the force: the pretension released, and a millionth of the -10 N push
        np.testing.assert_allclose(c.forces(x), [[10.0 + 1e-5, 0, 0, -10.0 - 1e-5, 0, 0]], rtol=1e-9)


class GeometryTest(unittest.TestCase):
    def test_hull_and_chords(self):
        tri = LP.convex_hull([(0, 0), (2, 0), (0, 2), (1, 0), (0.5, 0.5)])
        self.assertEqual(len(tri), 3)
        self.assertAlmostEqual(LP.polygon_area(tri), 2.0)
        self.assertEqual(LP._chord(tri, 1, 1.0), (0.0, 1.0))
        self.assertTrue(LP.inside_polygon(tri, (0.5, 0.5)))
        self.assertFalse(LP.inside_polygon(tri, (1.5, 1.5)))

    def test_levels_take_the_load_points(self):
        lv = LP._levels(0.0, 300.0, 50.0, [110.0])
        self.assertIn(110.0, lv)
        self.assertNotIn(100.0, lv)  # a regular line within h / 4 of a load point's line is dropped
        self.assertIn(150.0, lv)
        self.assertAlmostEqual(sum(LP._widths(lv, 0.0, 300.0)), 300.0)


class SquareNetTest(unittest.TestCase):
    def test_net_read_from_the_part(self):
        d = square_net_design(pitch=50.0)
        lp, w = push_middle(d)
        m = lp.nets["net"]
        self.assertAlmostEqual(m.net.d, 3.0)
        # the wire area per width from the mass: pi d^2 / 4 / pitch (the hull is the whole box: four edges held)
        self.assertAlmostEqual(m.a, math.pi * 9.0 / 4.0 / 50.0, places=3)
        self.assertAlmostEqual(LP.polygon_area(m.hull), 560.0 ** 2, delta=1.0)
        self.assertGreater(w, 0.0)

    def test_stiffer_with_more_wire_and_more_pretension(self):
        _, w = push_middle(square_net_design())
        _, w_wire = push_middle(square_net_design(d=4.0))
        _, w_tight = push_middle(square_net_design(), prestress=100.0)
        self.assertLess(w_wire, w)
        self.assertLess(w_tight, w)

    def test_sag_between_linear_and_cable(self):
        # on a stiff square (19.9 mm walls: the frame hardly bends) the push spreads over many wires: less sag than
        # one line through the point alone, held at fixed ends, would give
        lp, w = push_middle(square_net_design(wall=19.9))
        m = lp.nets["net"]
        one_line = LP.taut_cable_deflection(500.0, 560.0, 205000.0 * m.a * LP.NET_CELL_MM, m.N0 * LP.NET_CELL_MM)
        self.assertLess(w, one_line)

    def test_two_edges_on_a_line_are_slack(self):
        # held only along the bottom member: the fastenings are on a line, the net cannot be taut
        d = square_net_design(edges=("bottom",))
        lp = LP.build(d, LP.find_contacts(d), holds={"A": "all", "B": "all"})
        self.assertEqual(lp.slack_nets, ["net"])
        self.assertEqual(LP.free_parts(lp), ["net"])

    def test_three_edges_hull(self):
        d = square_net_design(edges=("bottom", "left", "right"))
        lp = LP.build(d, LP.find_contacts(d), holds={k: "all" for k in "ABCD" if k in {n["id"] for n in d["nodes"]}})
        self.assertAlmostEqual(LP.polygon_area(lp.nets["net"].hull), 560.0 ** 2, delta=1.0)


class Version2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = designgen.regenerate(V1, SET2)

    def test_side_nets_are_nets(self):
        nets = LP.net_parts(self.d)
        self.assertEqual(set(nets), {"side_net_L", "side_net_R"})
        lp = LP.build(self.d, LP.find_contacts(self.d))
        for pid, m in lp.nets.items():
            self.assertAlmostEqual(math.pi * m.net.d ** 2 / 4.0 / m.a, 50.0, places=6)  # the design's pitch
        self.assertEqual(lp.slack_nets, [])

    def test_push_and_effect(self):
        out = LP.analyse_load_path(self.d, "push", VALUES)
        self.assertEqual(out["free"], [])
        self.assertEqual(quantity(out, "occupant_push_open")["value"], 0.0)
        eff = [e for e in out["effects"] if e["name"].startswith("網は")]
        self.assertEqual(len(eff), 1)
        self.assertGreater(eff[0]["value"], LP.NET_PRESTRESS_MPA)  # the push stretches the wires


if __name__ == "__main__":
    unittest.main()
