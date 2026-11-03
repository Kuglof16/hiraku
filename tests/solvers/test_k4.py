import copy
import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.solvers.k4_geometry import disassemble, fit, from_box, from_member, interferences, overlap, sweep

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
TOOL = {t.name: t for t in solvers.builtin_tools()}["assembly"]


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


class ObbTest(unittest.TestCase):
    def test_separating_axes(self):
        a = from_box([0, 0, 0], [1, 1, 1])
        self.assertLess(overlap(a, from_box([1.01, 0, 0], [1, 1, 1])), 0)
        self.assertGreater(overlap(a, from_box([0.99, 0, 0], [1, 1, 1])), 0)
        # edge-edge: two cubes turned 45 deg about different axes, apart along the diagonal
        b = from_box([1.2, 1.2, 0], [1, 1, 1], [0, 0, 45])
        self.assertLess(overlap(a, b), 0)  # the face axes alone would not separate them along x and y... the cross axes do
        self.assertAlmostEqual(overlap(a, from_box([0, 0, 0], [1, 1, 1])), 1.0)

    def test_member_box(self):
        m = from_member([0, 0, 0], [1000, 0, 0], 40, 80)
        self.assertTrue(np.allclose(m.half, [500, 40, 20]))
        self.assertTrue(np.allclose(m.axes[1], [0, 0, 1]))  # height is vertical for a horizontal member


class DemoGeometryTest(unittest.TestCase):
    def test_no_static_interference(self):
        self.assertEqual(interferences(DESIGN), [])

    def test_mounts_are_not_interference(self):
        d = copy.deepcopy(DESIGN)
        for p in d["parts"]:
            if p["id"] == "battery":
                p["box"]["size_mm"][1] = 760.0  # wider than the gap between the rails
        hits = interferences(d)
        self.assertTrue(any("battery" in a + b for a, b, _ in hits))

    def test_steering_reaches_the_rails(self):
        sw = sweep(DESIGN)
        self.assertTrue(all("rail" in h[1] for h in sw["hits"]))
        self.assertTrue(all(h[3].startswith("steer") for h in sw["hits"]))
        # rails 80 mm inside the 600 x 200 wheel: free until w cos a + l sin a - w = 81 mm
        self.assertAlmostEqual(sw["free_steer"]["FL"], 16.48, delta=0.01)
        straight = copy.deepcopy(DESIGN)
        straight["variables"]["max_steer_angle"] = {"value": 10.0, "unit": "deg"}
        self.assertEqual(sweep(straight)["hits"], [])

    def test_assembly_order(self):
        asm = disassemble(DESIGN)
        self.assertEqual(asm["blocked"], {})
        order = [k for k, _ in asm["order"]]
        self.assertNotIn("part:occupant_R", order)
        self.assertNotIn("part:cargo", order)
        self.assertLess(order.index("part:susp_FL"), order.index("part:wheel_FL"))  # arms before the wheel

    def test_a_step_without_tool_room_names_what_takes_it(self):
        from hiraku.solvers.k4_geometry.assembly import steps

        cramped = [s for s in steps(DESIGN) if s["no_interference"] and not s["tool_clearance"]]
        self.assertTrue(cramped)
        for s in cramped:
            self.assertNotIn("blocked_by", s)  # that field is for a part that cannot go in at all
            self.assertIn("the tool's opening is taken by ", s["note"])

    def test_people(self):
        f = fit(DESIGN)
        self.assertAlmostEqual(f["shoulder_room"][0], 1300 - 2 * 495.3, places=6)
        self.assertAlmostEqual(f["hip_room.seat_R"][0], 500 - 391, places=6)
        self.assertEqual(f["knee_room.occupant_R"][1], "steering")


def pocket_design():
    """An 80 mm cube under a lid, walled in on both sides and behind, with a post 300 mm ahead:
    straight out is blocked every way; sliding forward past the lid and then lifting is free."""
    def plate(mid, a, b, sec):
        return [{"id": f"{mid}a", "xyz_mm": a}, {"id": f"{mid}b", "xyz_mm": b}], \
            {"id": mid, "node_a": f"{mid}a", "node_b": f"{mid}b", "section": sec, "material": "m"}
    spec = {"bottom": ([-80, 0, -60], [400, 0, -60], "flat"), "lid": ([-80, 0, 60], [80, 0, 60], "flat"),
            "wall_l": ([-80, 60, 0], [400, 60, 0], "side"), "wall_r": ([-80, -60, 0], [400, -60, 0], "side"),
            "back": ([-60, -150, 0], [-60, 150, 0], "side"), "post": ([300, -40, 0], [300, 40, 0], "side")}
    nodes, members = [], []
    for mid, (a, b, sec) in spec.items():
        n, m = plate(mid, a, b, sec)
        nodes += n
        members.append(m)
    return {"design_version": 1, "name": "pocket", "variables": {}, "supports": [], "nodes": nodes, "members": members,
            "parts": [{"id": "inner", "name": "中の箱", "kind": "battery", "box": {"center_mm": [0, 0, 0], "size_mm": [80, 80, 80]},
                       "mass_kg": 1.0}],
            "sections": {"flat": {"shape": "rect_solid", "dims_mm": {"b": 300, "h": 20}},
                         "side": {"shape": "rect_solid", "dims_mm": {"b": 20, "h": 120}}},
            "materials": {"m": {"name": "m", "E_MPa": 1.0, "nu": 0.3, "density_kg_m3": 1.0, "yield_MPa": 1.0, "source": "test",
                                "verified": True}}}


class AssemblyKitTest(unittest.TestCase):
    def test_two_move_route(self):
        asm = disassemble(pocket_design())
        self.assertEqual(asm["blocked"], {})
        moves = asm["routes"]["part:inner"]
        self.assertEqual([d for d, _ in moves], ["+x", "+z"])
        # the back face (40 mm behind the centre) must pass the lid's front edge at x = 80, less the 1 mm
        # counted as touching: 119 mm; the first 25 mm step past that
        self.assertEqual(moves[0][1], 125.0)
        self.assertEqual(asm["order"], [("part:inner", "-x")])  # it slides back into place last

    def test_fastenings_and_welds(self):
        from hiraku.solvers.k4_geometry import bodies
        from hiraku.solvers.k4_geometry.fastening import part_fastenings, text_of, welds
        bs = bodies(DESIGN)
        bat = part_fastenings(DESIGN, "battery", bs)
        self.assertEqual([f["to"] for f in bat], ["node LS1", "node RS1", "node LS2", "node RS2"])
        self.assertEqual({f["kind"] for f in bat}, {"bolt"})
        self.assertEqual([f["to"] for f in part_fastenings(DESIGN, "wheel_FL", bs)], ["part susp_FL"])
        self.assertEqual([f["to"] for f in part_fastenings(DESIGN, "susp_FL", bs)], ["node LA"])
        self.assertEqual(part_fastenings(DESIGN, "cargo", bs), [])
        self.assertEqual(text_of(bat, {}), "LS1・RS1・LS2・RS2 の節点にボルトで留める")
        joints = {w["node"]: w["members"] for w in welds(DESIGN)}
        self.assertEqual(joints["LA"], ["cross_A", "rail_L_AS1", "rail_L_FA"])

    def test_steps_carry_path_fastenings_and_text_when_the_contract_has_them(self):
        from unittest import mock

        from hiraku.solvers.k4_geometry import assembly
        fields = {"order", "part", "direction", "depends_on", "no_interference", "tool_clearance", "hand_reach", "note",
                  "fastenings", "path_mm", "text"}
        with mock.patch.object(assembly, "_step_fields", return_value=fields):
            seq = assembly.steps(DESIGN)
        bat = next(s for s in seq if s["part"] == "battery")
        self.assertEqual(bat["path_mm"][-1], [-725.0, 0.0, 330.0])  # ends where it is fitted
        self.assertGreater(bat["path_mm"][0][2], 330.0)  # comes down from above
        self.assertTrue(bat["text"].startswith("電池"))
        self.assertIn("ボルトで留める", bat["text"])
        plain = assembly.steps(DESIGN)
        allowed = assembly._step_fields()
        self.assertTrue(all(set(s) <= allowed for s in plain))


class AssemblyToolTest(unittest.TestCase):
    def test_checks_and_run(self):
        self.assertEqual([c for c in TOOL.check() if not c["pass"]], [])
        contract.validate("tool_spec", TOOL.spec())
        rec = TOOL.run(DESIGN, {"load_case_id": "H1", "test_id": "H1", "domain": "assembly", "title": "H1",
                                "params": {}, "tolerance_rel": 0.15}, PRED, {})
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        r = results(rec)
        self.assertEqual(r["interference_count"]["value"], 0.0)
        self.assertGreater(r["motion_interference_count"]["value"], 0.0)
        self.assertLess(r["free_steer_angle"]["value"], r["max_steer_angle"]["value"])
        self.assertEqual(r["blocked_parts"]["value"], 0.0)
        seq = rec["assembly"]
        self.assertEqual([s["order"] for s in seq], list(range(1, len(seq) + 1)))
        self.assertTrue(all(s["no_interference"] for s in seq))
        self.assertFalse(any("assembly step" in e["name"] for e in rec.get("effects", [])))
        self.assertEqual({i["state"] for i in rec["interferences"]}, {"motion"})
        self.assertTrue(all(i["b"].startswith("member rail") for i in rec["interferences"]))
        wheel = next(s for s in seq if s["part"] == "wheel_FL")
        self.assertIn("susp_FL", wheel["depends_on"])  # the wheel goes on the arms already in place


class NoInfinityTest(unittest.TestCase):
    def test_infinite_results_are_left_out(self):
        tool = {t.name: t for t in solvers.builtin_tools()}["fatigue"]
        d = copy.deepcopy(DESIGN)
        d["variables"]["fatigue_fat_class"] = {"value": 1e6, "unit": "MPa"}  # no damage: life would be infinite
        rec = tool.run(d, {"load_case_id": "R3", "test_id": "R3", "domain": "strength", "title": "R3", "tolerance_rel": 0.15,
                           "params": {"distance": {"value": 1, "unit": "km"}, "road_class": "A", "speed": {"value": 10, "unit": "km/h"},
                                      "sample_length": {"value": 50, "unit": "m"}}}, PRED, {})
        self.assertTrue(all(math.isfinite(r["value"]) for r in rec["results"]))
        json.dumps(rec, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
