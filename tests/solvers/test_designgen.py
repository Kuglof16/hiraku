import json
import unittest
from pathlib import Path

from hiraku import contract, designgen, solvers

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
GEN = designgen.generator("offroad-4w")
PRED = [{"prediction_id": "pred-0001"}]


def mm(v):
    return {"value": v, "unit": "mm"}


def results(rec):
    return {r["quantity"]: r["value"] for r in rec["results"]}


class DesignGenTest(unittest.TestCase):
    def test_defaults_build_v1(self):
        self.assertEqual(GEN.build(), V1)

    def test_spec(self):
        names = set()
        for v in GEN.spec():
            names.add(v["name"])
            self.assertIn(v["unit"], contract.UNITS, v["name"])
            if v["revisable"]:
                self.assertLessEqual(v["min"], v["default"])
                self.assertLessEqual(v["default"], v["max"])
        optional = {n for group in GEN.OPTIONAL.values() for n in group}  # written only when switched on
        self.assertTrue(names - optional <= set(V1["variables"]))
        self.assertFalse(optional & set(V1["variables"]))
        for needed in ("wheelbase", "track", "rail_spacing", "rail_b", "rail_h", "rail_t", "max_steer_angle", "seat_x",
                       "seat_y", "steering_x", "battery_x", "front_spring_rate", "rear_spring_rate"):
            self.assertIn(needed, names)

    def test_revise_round_trip(self):
        v2 = GEN.build(GEN.values_of(V1), version=2, parent=1, reason="same variables")
        self.assertEqual({k: v for k, v in v2.items() if k not in ("design_version", "parent_version", "name", "change_reason")},
                         {k: v for k, v in V1.items() if k not in ("design_version", "parent_version", "name", "change_reason")})
        self.assertEqual((v2["design_version"], v2["parent_version"]), (2, 1))

    def test_every_range_end_builds_or_says_why(self):
        for v in GEN.spec():
            if not v["revisable"]:
                continue
            for end in (v["min"], v["max"]):
                try:
                    d = GEN.build({v["name"]: {"value": end, "unit": v["unit"]}})
                except designgen.DesignGenError as e:
                    self.assertTrue(str(e))
                    continue
                contract.validate("design", d)
                if v["name"] not in d["variables"]:  # an optional variable at its default is not written
                    self.assertEqual(end, v["default"])
                    continue
                self.assertEqual(d["variables"][v["name"]]["value"], end)

    def test_refusals(self):
        with self.assertRaisesRegex(designgen.DesignGenError, "outside"):
            GEN.build({"wheelbase": mm(3000)})
        with self.assertRaisesRegex(designgen.DesignGenError, "unknown variable"):
            GEN.build({"wheel_base": mm(1700)})
        with self.assertRaisesRegex(designgen.DesignGenError, "rails reach the wheels"):
            GEN.build({"rail_spacing": mm(900), "track": mm(900)})
        with self.assertRaisesRegex(designgen.DesignGenError, "stations"):
            GEN.build({"seat_x": mm(-1300), "wheelbase": mm(1400)})

    def test_variables_fix_the_known_defects(self):
        """v1: the front wheels touch the rails before 30 deg of steer, the driver's knees hit
        the steering column by 69 mm. A wider track and the steering 90 mm forward clear both."""
        tool = {t.name: t for t in solvers.builtin_tools()}["assembly"]
        d = GEN.build({**GEN.values_of(V1), "track": mm(1220), "steering_x": mm(-480)}, version=2, parent=1, reason="fix")
        contract.validate("design", d)
        r = results(tool.run(d, {"load_case_id": "H", "test_id": "H1", "domain": "assembly", "title": "H", "params": {},
                                 "tolerance_rel": 0.05}, PRED, {}))
        self.assertGreaterEqual(r["free_steer_angle"], 30.0)
        self.assertGreaterEqual(r["knee_room"], 0.0)
        self.assertEqual(r["interference_count"], 0.0)

    def test_regenerate(self):
        d = designgen.regenerate(V1, {"track": mm(1220)})
        self.assertEqual(d["variables"]["track"]["value"], 1220.0)
        self.assertEqual([s["position_mm"][1] for s in d["supports"]], [610.0, -610.0, 610.0, -610.0])
        for k in ("design_version", "parent_version", "name", "change_reason"):
            self.assertEqual(d[k], V1[k])
        # the driveshafts' inner joints are lowered as far as the (wider) track's shafts need: derived, not an input
        moved = ("track", "driveshaft_inner_drop")
        self.assertEqual({k: v for k, v in d["variables"].items() if k not in moved},
                         {k: v for k, v in V1["variables"].items() if k not in moved})
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            designgen.regenerate(V1, {"occupant_mass": {"value": 60, "unit": "kg"}})
        with self.assertRaisesRegex(ValueError, "outside 900 to 1400 mm"):
            designgen.regenerate(V1, {"track": mm(2000)})
        with self.assertRaisesRegex(ValueError, "unknown variable"):
            designgen.regenerate(V1, {"trak": mm(1200)})
        with self.assertRaisesRegex(ValueError, "match no design generator"):
            designgen.regenerate({"variables": {}}, {})

    def test_older_design_without_newer_variables(self):
        """A design saved before some variables existed can still be listed and revised."""
        old = json.loads(json.dumps(V1))
        for k in ("tyre_damping_ratio", "seat_x", "wheel_inertia"):
            del old["variables"][k]
        got = {v["name"]: v for v in designgen.variables(old)}
        self.assertEqual(got["seat_x"]["value"], -1045.0)  # the default
        self.assertNotIn("wheel_inertia", got)
        d = designgen.regenerate(old, {"track": mm(1220)})
        self.assertEqual(d["variables"]["seat_x"]["value"], -1045.0)
        self.assertEqual(d["variables"]["track"]["value"], 1220.0)

    def test_archetypes_and_propose(self):
        from hiraku.catalog import load_requirements
        from hiraku.catalog.build import check_design_loading
        req = load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml")
        kinds = designgen.archetypes(req["flags"])
        self.assertEqual([k["name"] for k in kinds], ["ladder_4w", "space_frame_utv"])  # Claude picks one
        self.assertEqual(kinds[0]["choices"]["joints"], ["butt", "gusset", "through"])
        self.assertEqual(kinds[0]["choices"]["part_mounts"], ["none", "brackets"])  # test_designgen_options
        self.assertEqual(designgen.archetypes({"self_propelled": False, "ride": True}), [])
        d = designgen.propose("ladder_4w", {}, {}, req)
        self.assertEqual({k: v for k, v in d.items() if k != "change_reason"},
                         {k: v for k, v in V1.items() if k != "change_reason"})  # the demo requirements give v1
        check_design_loading(req, d, "two_cargo")
        heavy = dict(req, requirements=[dict(r, value=90) if r["key"] == "Q33" else r for r in req["requirements"]])
        d2 = designgen.propose("ladder_4w", {}, {"track": mm(1220)}, heavy)
        people: dict = {}  # each person's body parts add up to the requirement
        for p in d2["parts"]:
            if p["kind"] == "occupant":
                people[p["id"].split(".", 1)[0]] = people.get(p["id"].split(".", 1)[0], 0.0) + p["mass_kg"]
        self.assertEqual({round(m, 9) for m in people.values()}, {90.0})
        self.assertEqual(d2["variables"]["track"]["value"], 1220.0)
        with self.assertRaisesRegex(ValueError, "comes from requirement Q33"):
            designgen.propose("ladder_4w", {}, {"occupant_mass": {"value": 70, "unit": "kg"}}, req)
        with self.assertRaisesRegex(ValueError, "no choice"):
            designgen.propose("ladder_4w", {"drive": "front"}, {}, req)
        with self.assertRaisesRegex(ValueError, "no kind of design"):
            designgen.propose("monocoque", {}, {}, req)

    def test_cargo_height_is_set_by_the_load_not_the_designer(self):
        from hiraku.catalog import load_requirements
        req = load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml")
        d = designgen.propose("ladder_4w", {}, {"cargo_height": mm(1240)}, req)
        cargo = next(p for p in d["parts"] if p["kind"] == "cargo")
        deck = next(p for p in d["parts"] if p["id"] == "deck")
        deck_top = deck["box"]["center_mm"][2] + deck["box"]["size_mm"][2] / 2
        self.assertAlmostEqual(cargo["box"]["center_mm"][2] - deck_top, 620.0, places=9)
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            designgen.regenerate(d, {"cargo_height": mm(300)})

    def test_variables_listing(self):
        got = {v["name"]: v for v in designgen.variables(V1)}
        self.assertEqual(got["track"]["value"], 1100.0)
        self.assertEqual((got["track"]["min"], got["track"]["max"]), (900.0, 1400.0))
        self.assertFalse(got["occupant_mass"]["revisable"])
        json.dumps(list(got.values()), allow_nan=False)

    def test_derived_variables_follow_the_wheel(self):
        d = designgen.regenerate(V1, {"wheel_radius": mm(340)})
        self.assertEqual(d["variables"]["tyre_relaxation_length"], {"value": 340.0, "unit": "mm"})
        wheel = next(p for p in d["parts"] if p["kind"] == "wheel")
        self.assertAlmostEqual(d["variables"]["wheel_inertia"]["value"], wheel["mass_kg"] * 0.34**2 / 2, places=12)
        self.assertNotIn("wheel_inertia", GEN.values_of(d))  # not an input: rebuilt every time
        with self.assertRaisesRegex(ValueError, "unknown variable"):
            designgen.regenerate(V1, {"wheel_inertia": {"value": 1.0, "unit": "kg*m^2"}})

    def test_motor_mass_follows_its_rating(self):
        def motor(**ch):  # the drive: motor, reducer and driveshafts (kit/drivetrain.py splits the v1 25 kg)
            return sum(p["mass_kg"] for p in GEN.build(ch)["parts"] if p["kind"] in ("motor", "reducer", "driveshaft"))
        self.assertAlmostEqual(motor(), 25.0, places=9)
        self.assertAlmostEqual(motor(motor_peak_power={"value": 12, "unit": "kW"}), 30.0)
        self.assertAlmostEqual(motor(motor_peak_torque={"value": 75, "unit": "N*m"}), 37.5)
        self.assertAlmostEqual(motor(motor_peak_power={"value": 12, "unit": "kW"}, motor_peak_torque={"value": 75, "unit": "N*m"}), 37.5)
        self.assertAlmostEqual(motor(motor_peak_power={"value": 8, "unit": "kW"}), 25.0)  # the torque keeps it at 50 N*m

    def test_battery_follows_its_capacity(self):
        d = GEN.build({"battery_capacity": {"value": 150, "unit": "Ah"}})
        bat = next(p for p in d["parts"] if p["id"] == "battery")
        self.assertAlmostEqual(bat["mass_kg"], 75.0)
        self.assertAlmostEqual(bat["box"]["size_mm"][0], 750.0)


def one(v):
    return {"value": v, "unit": "1"}


class JointReinforcementTest(unittest.TestCase):
    """CHOICES["joints"]: butt (v1), gusset, through."""

    @classmethod
    def setUpClass(cls):
        cls.gusset = designgen.regenerate(V1, {"joints": "gusset"})
        cls.through = designgen.regenerate(V1, {"joints": "through"})

    def test_butt_is_v1_byte_for_byte(self):
        raw = (ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8")
        self.assertEqual(json.dumps(GEN.build({"gusset_leg": mm(0), "cross_members_through": one(0)}), ensure_ascii=False,
                                    indent=2), json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
        self.assertEqual(designgen.regenerate(self.gusset, {"joints": "butt"}), V1)
        self.assertEqual(designgen.regenerate(self.gusset, {"gusset_leg": mm(0)}), V1)
        self.assertEqual(designgen.regenerate(self.through, {"cross_members_through": one(0)}), V1)
        self.assertEqual(GEN.choices_of(V1)["joints"], "butt")

    def test_the_variables_alone_make_the_same_designs(self):
        self.assertEqual(designgen.regenerate(V1, {"cross_members_through": {"value": 1, "unit": "1"}}), self.through)
        self.assertEqual(designgen.regenerate(V1, {"gusset_leg": mm(60)}), self.gusset)
        both = designgen.regenerate(V1, {"gusset_leg": mm(60), "cross_members_through": one(1)})
        self.assertEqual(GEN.choices_of(both)["joints"], "gusset+through")

    def test_ranges_and_meanings(self):
        spec = {v["name"]: v for v in designgen.variables(V1)}
        for name, lo, hi in (("gusset_leg", 0.0, 150.0), ("gusset_thickness", 1.6, 4.5), ("cross_members_through", 0.0, 1.0)):
            self.assertTrue(spec[name]["revisable"], name)
            self.assertEqual((spec[name]["min"], spec[name]["max"]), (lo, hi))
            self.assertTrue(spec[name]["meaning"])
        self.assertNotIn("gussets", spec)
        with self.assertRaisesRegex(ValueError, "0 \\(no gussets\\) or 30"):
            designgen.regenerate(V1, {"gusset_leg": mm(10)})
        with self.assertRaisesRegex(ValueError, "lower than the rails"):
            designgen.regenerate(V1, {"cross_members_through": one(1), "cross_h": mm(80)})
        with self.assertRaisesRegex(ValueError, "unknown variable 'gussets'"):
            designgen.regenerate(V1, {"gussets": one(1)})

    def test_earlier_designs_with_the_gusset_switch(self):
        """A design that still carries gussets = 0 with a gusset_leg is read as no gussets."""
        old = json.loads(json.dumps(V1))
        old["variables"].update(gussets=one(0), gusset_leg=mm(60), gusset_thickness=mm(2.3))
        self.assertEqual(GEN.choices_of(old)["joints"], "butt")
        self.assertEqual(designgen.regenerate(old, {})["parts"], V1["parts"])
        old["variables"]["gussets"] = one(1)
        self.assertEqual(designgen.regenerate(old, {})["parts"], self.gusset["parts"])

    def test_gusset_parts(self):
        d = self.gusset
        contract.validate("design", d)
        g = [p for p in d["parts"] if p["kind"] == "gusset"]
        self.assertEqual(len(g), 4 * 2 * 4)  # four T stations (A, S1, S2, B), both sides, four plates each
        # every part of version 1 unchanged (by id: the joints' fittings, kit/joints.py, come after the gussets)
        self.assertEqual([p for p in d["parts"] if p["id"] in {q["id"] for q in V1["parts"]}], V1["parts"])
        self.assertEqual({k: d["variables"][k]["value"] for k in ("gusset_leg", "gusset_thickness")},
                         {"gusset_leg": 60.0, "gusset_thickness": 2.3})
        self.assertNotIn("gussets", d["variables"])
        rho = V1["materials"]["stkr400"]["density_kg_m3"]
        p = next(x for x in g if x["id"] == "gusset_LA_front_upper")
        self.assertEqual(p["carried_by"], ["LA"])
        self.assertEqual(p["box"]["size_mm"], [60.0, 60.0, 2.3])
        self.assertAlmostEqual(p["mass_kg"], rho * 1e-9 * 60 * 60 / 2 * 2.3, places=12)
        # corner: front face of cross_A (x = 18.85 mid-surface), inner wall of the left rail (y = 331.15),
        # upper wall of cross_A (z = 398.85); centroid a third of each leg in
        self.assertEqual([round(v, 6) for v in p["box"]["center_mm"]], [18.85 + 20.0, 331.15 - 20.0, 398.85])
        self.assertEqual(GEN.choices_of(d)["joints"], "gusset")
        big = designgen.regenerate(d, {"gusset_leg": mm(90), "gusset_thickness": mm(3.2)})
        q = next(x for x in big["parts"] if x["id"] == "gusset_RB_rear_lower")
        self.assertEqual(q["box"]["size_mm"], [90.0, 90.0, 3.2])

    def test_through(self):
        d = self.through
        contract.validate("design", d)
        v1_ids = {q["id"] for q in V1["parts"]}
        # every part of version 1 unchanged (by id: the joints' fittings, kit/joints.py, come after the through pieces)
        self.assertEqual([p for p in d["parts"] if p["id"] in v1_ids], V1["parts"])
        self.assertEqual(d["members"], V1["members"])  # the beam model is unchanged
        self.assertEqual(d["variables"]["cross_members_through"], one(1.0))
        self.assertNotIn("gusset_leg", d["variables"])
        self.assertEqual(GEN.choices_of(d)["joints"], "through")
        extra = [p for p in d["parts"] if p["id"] not in v1_ids]
        self.assertEqual({p["id"] for p in extra},
                         {f"through_cross_{st}_{s}{st}" for st in ("A", "S1", "S2", "B") for s in "LR"})
        rho = V1["materials"]["stkr400"]["density_kg_m3"] * 1e-9
        p = next(x for x in extra if x["id"] == "through_cross_S1_LS1")
        xyz = next(x["xyz_mm"] for x in V1["nodes"] if x["id"] == "LS1")
        self.assertEqual((p["kind"], p["carried_by"], p["box"]["center_mm"]), ("frame", ["LS1"], xyz))
        self.assertEqual(p["box"]["size_mm"], [40.0, 40.0 - 2.3, 40.0])
        area = 40 * 40 - (40 - 4.6) ** 2
        self.assertAlmostEqual(p["mass_kg"], rho * (area * (40 - 2.3) - 2.3 * (40 - 2.3) ** 2), places=12)

    def test_reinforcement_mass_in_the_whole_vehicle(self):
        from hiraku.solvers.k0_handcalc.vehicle import mass_summary
        base = mass_summary(V1)["mass_kg"]
        for d in (self.gusset, self.through):
            extra = sum(p["mass_kg"] for p in d["parts"] if p["kind"] == "gusset" or p["id"].startswith("through_"))
            self.assertGreater(extra, 0.0)
            self.assertAlmostEqual(mass_summary(d)["mass_kg"] - base, extra, places=9)

    def test_through_parts_do_not_clash_with_the_frame(self):
        from hiraku.solvers.k4_geometry.model import interferences
        for d in (self.through, designgen.regenerate(V1, {"gusset_leg": mm(60), "cross_members_through": one(1)})):
            for a, b, _ in interferences(d):
                self.assertFalse("through_" in a + b and ("member:" in a + b or "gusset" in a + b), (a, b))

    def test_propose_with_a_choice(self):
        from hiraku.catalog import load_requirements
        req = load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml")
        d = designgen.propose("ladder_4w", {"joints": "gusset"}, {"gusset_leg": mm(80)}, req)
        self.assertEqual(GEN.choices_of(d)["joints"], "gusset")
        self.assertEqual(d["variables"]["gusset_leg"]["value"], 80.0)
        with self.assertRaisesRegex(ValueError, "must be one of"):
            designgen.propose("ladder_4w", {"joints": "rivet"}, {}, req)
        with self.assertRaisesRegex(ValueError, "come from the choices"):
            designgen.propose("ladder_4w", {"joints": "gusset"}, {"cross_members_through": one(1)}, req)
        with self.assertRaisesRegex(ValueError, "gusset needs gusset_leg > 0"):
            designgen.propose("ladder_4w", {"joints": "gusset"}, {"gusset_leg": mm(0)}, req)
        with self.assertRaisesRegex(ValueError, "switch"):
            GEN.build({"cross_members_through": one(0.5)})

    def test_gussets_do_not_clash_with_the_frame(self):
        """K4: a gusset may sink into the members at its joint (MOUNT_MM), so none counts against a member."""
        from hiraku.solvers.k4_geometry.model import MOUNT_MM, bodies, interferences
        from hiraku.solvers.k4_geometry.obb import overlap
        bs = bodies(self.gusset)
        for a, b, _ in interferences(self.gusset, bs):
            self.assertFalse("gusset" in a + b and "member:" in a + b, (a, b))
        cross = bs["member:cross_A"]
        self.assertLessEqual(overlap(bs["part:gusset_LA_front_upper"].obb, cross.obb), MOUNT_MM)


if __name__ == "__main__":
    unittest.main()
