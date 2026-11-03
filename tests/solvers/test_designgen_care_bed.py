"""The care bed's design generator (hiraku.designgen.care_bed): variables, propose and regenerate, the
contract, systems and links, interference and the meshes."""

import math
import unittest
from pathlib import Path

import yaml

from hiraku import contract, designgen
from hiraku.designgen import care_bed

ROOT = Path(__file__).resolve().parents[2]
G0 = 9.80665


def bed_requirements() -> dict:
    """data/requirements/products/care-bed.yaml with the sample answers' numeric values (Q40)."""
    req = yaml.safe_load((ROOT / "data" / "requirements" / "products" / "care-bed.yaml").read_text(encoding="utf-8"))
    ans = yaml.safe_load((ROOT / "data" / "requirements" / "products" / "care-bed.answers.yaml").read_text(encoding="utf-8"))
    for key, a in ans["answers"].items():
        req["requirements"].append({"key": key, "name": key, "value": a["value"], **({"unit": a["unit"]} if "unit" in a else {}),
                                    "source": "hearing", "trust": a.get("trust", 1), "basis": a.get("basis", "")})
    return req


def mm(v):
    return {"value": v, "unit": "mm"}


class CareBedGeneratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = care_bed.build()

    def test_default_design_fits_the_contract(self):
        contract.validate("design", self.d)
        self.assertIs(designgen.generator("care-bed"), care_bed)
        self.assertIs(designgen.generator_of(self.d), care_bed)
        self.assertEqual({s["kind"] for s in self.d["supports"]}, {"caster"})
        for s in self.d["supports"]:
            self.assertEqual(s["actuation"], {"drive": False, "brake": True, "steer": False})
            self.assertIn(s["frame_node"], {n["id"] for n in self.d["nodes"]})

    def test_spec(self):
        names = set()
        for v in care_bed.spec():
            names.add(v["name"])
            self.assertIn(v["unit"], contract.UNITS, v["name"])
            self.assertTrue(v["meaning"], v["name"])
            if v["revisable"]:
                self.assertLessEqual(v["min"], v["default"])
                self.assertLessEqual(v["default"], v["max"])
        self.assertEqual(names, set(self.d["variables"]))
        for fixed in ("occupant_load", "safe_working_load", "back_angle_max"):
            self.assertFalse(care_bed.VARIABLES[fixed].revisable)

    def test_every_range_end_builds_or_says_why(self):
        for v in care_bed.spec():
            if not v["revisable"]:
                continue
            for end in (v["min"], v["max"]):
                try:
                    d = care_bed.build({v["name"]: {"value": end, "unit": v["unit"]}})
                except designgen.DesignGenError as e:
                    self.assertTrue(str(e))
                    continue
                contract.validate("design", d)
                self.assertEqual(d["variables"][v["name"]]["value"], end)

    def test_refusals(self):
        with self.assertRaisesRegex(designgen.DesignGenError, "outside"):
            care_bed.build({"bed_length": mm(2500)})
        with self.assertRaisesRegex(designgen.DesignGenError, "unknown variable"):
            care_bed.build({"bed_lenght": mm(1900)})
        with self.assertRaisesRegex(designgen.DesignGenError, "leg board"):
            care_bed.build({"bed_length": mm(1800), "back_length": mm(850), "thigh_length": mm(450), "seat_length": mm(300)})
        with self.assertRaisesRegex(designgen.DesignGenError, "stations"):
            care_bed.build({"back_actuator_reach": mm(560)})  # the anchor A lands on the thigh-leg hinge T

    def test_regenerate_and_variables(self):
        d2 = designgen.regenerate(self.d, {"deck_height": mm(600)})
        self.assertEqual(d2["variables"]["deck_height"]["value"], 600.0)
        top = max(p["box"]["center_mm"][2] + p["box"]["size_mm"][2] / 2 for p in d2["parts"] if p["kind"] == "deck")
        self.assertAlmostEqual(top, 600.0, places=6)
        self.assertEqual({k: v for k, v in d2["variables"].items() if k != "deck_height"},
                         {k: v for k, v in self.d["variables"].items() if k != "deck_height"})
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            designgen.regenerate(self.d, {"occupant_load": {"value": 1000, "unit": "N"}})
        got = {v["name"]: v for v in designgen.variables(self.d)}
        self.assertEqual((got["deck_height"]["min"], got["deck_height"]["max"]), (300.0, 700.0))

    def test_archetypes_and_propose_from_the_requirements(self):
        req = bed_requirements()
        kinds = designgen.archetypes(req["flags"])
        self.assertEqual([k["name"] for k in kinds], ["care_bed"])
        self.assertNotIn("care_bed", [k["name"] for k in designgen.archetypes({"self_propelled": True, "ride": True,
                                                                               "standing": True, "lean": True, "electric": True})])
        d = designgen.propose("care_bed", {}, {"deck_height": mm(500)}, req)
        contract.validate("design", d)
        from hiraku.contract.loading import person_of

        occ = [p for p in d["parts"] if p["kind"] == "occupant"]
        self.assertEqual({person_of(p) for p in occ}, {"occupant"})  # one person in four pieces
        self.assertAlmostEqual(sum(p["mass_kg"] for p in occ), 1350.0 / G0, places=9)
        self.assertAlmostEqual(sum(p["mass_kg"] for p in d["parts"] if p["kind"] == "mattress"), 350.0 / G0, places=9)
        back = next(p for p in occ if p["id"] == "occupant.back")
        self.assertAlmostEqual(back["mass_kg"], 1350.0 / G0 * 700.0 / 1910.0, places=9)  # in proportion to the board
        self.assertEqual(d["variables"]["back_angle_max"], {"value": 70.0, "unit": "deg"})
        self.assertEqual(d["variables"]["deck_height"]["value"], 500.0)
        with self.assertRaisesRegex(ValueError, "comes from requirement Q33"):
            designgen.propose("care_bed", {}, {"occupant_load": {"value": 1000, "unit": "N"}}, req)
        with self.assertRaisesRegex(ValueError, "no choice"):
            designgen.propose("care_bed", {"joints": "butt"}, {}, req)
        low = dict(req, requirements=[dict(r, value=1000) if r["key"] == "Q40" else r for r in req["requirements"]])
        with self.assertRaisesRegex(ValueError, "at least Q33"):
            designgen.propose("care_bed", {}, {}, low)

    def test_every_part_has_a_system_and_the_chains_close(self):
        parts = {p["id"]: p for p in self.d["parts"]}
        for p in parts.values():
            self.assertIn("system", p, p["id"])
            self.assertTrue(p.get("connects_to"), p["id"])
        reached = care_bed.power_reach(self.d)
        self.assertLessEqual({"main_switch", "control_box", "actuator_back", "actuator_knee", "actuator_lift"}, reached)
        self.assertEqual([c["to"] for c in parts["power_cord"]["connects_to"] if c["carries"] == "power"], ["main_switch"])
        self.assertEqual([c["to"] for c in parts["main_switch"]["connects_to"] if c["carries"] == "power"], ["control_box"])
        self.assertIn({"to": "control_box", "via": "wire", "carries": "signal"}, parts["hand_switch"]["connects_to"])
        for tag, board in (("back", "deck_back"), ("knee", "deck_thigh")):
            to = {c["to"] for c in parts[f"actuator_{tag}"]["connects_to"]}
            self.assertEqual(to, {f"bracket_{tag}", board})
        nodes = {n["id"] for n in self.d["nodes"]}
        ids = set(parts) | nodes | {s["id"] for s in self.d["supports"]}
        for p in parts.values():
            for c in p["connects_to"]:
                self.assertIn(c["to"], ids, (p["id"], c))

    def test_masses_and_layout(self):
        from hiraku.solvers.k0_handcalc.vehicle import mass_summary
        from hiraku.solvers.k4_geometry import interferences

        self.assertEqual(interferences(self.d), [])
        ms = mass_summary(self.d)
        bed = ms["mass_kg"] - (1700.0 / G0)
        self.assertGreater(bed, 40.0)  # frame, boards, actuators: a plausible bed, not a formula check
        self.assertLess(bed, 150.0)
        st = care_bed.stations({k: q["value"] for k, q in self.d["variables"].items()})
        self.assertEqual(list(st), ["H", "P1", "KA", "B", "K", "A", "T", "P2", "F"])
        self.assertAlmostEqual(st["H"] - st["F"], 1910.0)
        side = [m for m in self.d["members"] if m.get("part") == "side_rail_L"]
        self.assertEqual(len(side), 4)

    def test_meshes(self):
        from hiraku import solvers

        shell = solvers.frame_mesh(self.d)
        self.assertEqual(shell["element_type"], "shell")
        solid = care_bed.bracket_mesh(self.d, size=4.0)
        contract.validate("mesh", dict(solid, mesh_id="mesh-0001"))
        self.assertEqual(set(solid["element_member"]), {"bracket_back", "bracket_knee"})
        v = self.d["variables"]
        parts = {p["id"]: p for p in self.d["parts"]}
        b = parts["bracket_back"]
        vol = 2 * v["bracket_t"]["value"] * b["box"]["size_mm"][0] * b["box"]["size_mm"][2]
        rho = self.d["materials"][care_bed.PLATE]["density_kg_m3"]
        self.assertTrue(math.isclose(b["mass_kg"], rho * 1e-9 * vol, rel_tol=1e-12))


if __name__ == "__main__":
    unittest.main()
