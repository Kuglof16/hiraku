import copy
import unittest

from hiraku import contract
from hiraku.catalog import REPO_ROOT, read_data
from hiraku.intake import knowledge, load_product, rulecheck, systems
from hiraku.intake.flags import FLAGS

RULES = {r["rule_id"]: r for r in knowledge.all_rules(knowledge.load_builtin()) if r["category"] == "system"}
ALL = {"ride": True, "self_propelled": True, "outdoor": True, "rough": True, "public_road": True, "battery": True}


def link(to, carries=None, via="bolt"):
    return {"to": to, **({"carries": carries} if carries else {}), "via": via}


def part(pid, kind, system, *links):
    return {"id": pid, "name": pid, "kind": kind, "system": system, "connects_to": list(links)}


def vehicle():
    """A small vehicle with every system, joined end to end."""
    parts = [
        part("battery", "battery", "battery", link("fuse1", "power", "wire")),
        part("fuse1", "fuse", "battery", link("controller", "power", "wire")),
        part("controller", "controller", "control", link("motor", "power", "wire")),
        part("motor", "motor", "drive", link("driveshaft", "torque", "spline")),
        part("driveshaft", "shaft", "drive", link("RL", "torque", "spline"), link("RR", "torque", "spline")),
        part("steering", "steering", "steering", link("rack", "steer", "spline")),
        part("rack", "steering_rack", "steering", link("tie_FL", "steer", "ball_joint"), link("tie_FR", "steer", "ball_joint")),
        part("tie_FL", "tie_rod", "steering", link("FL.knuckle", "steer", "ball_joint")),
        part("tie_FR", "tie_rod", "steering", link("FR.knuckle", "steer", "ball_joint")),
        part("pedal", "brake_pedal", "brake", link("master", "brake", "hose")),
        part("master", "master_cylinder", "brake", *[link(f"caliper_{w}", "brake", "hose") for w in ("FL", "FR", "RL", "RR")]),
        *[part(f"caliper_{w}", "brake_caliper", "brake", link(w, "brake")) for w in ("FL", "FR", "RL", "RR")],
        part("seat_R", "seat", "seat", link("rail_R", "load")),
        part("seat_L", "seat", "seat", link("rail_L", "load")),
        part("belt_R", "belt", "restraint", link("seat_R"), link("rail_R")),
        part("belt_L", "belt", "restraint", link("seat_L"), link("rail_L")),
        part("rops", "rollbar", "rops", link("rail_R", "load", "weld")),
        part("headlamp", "headlamp", "lighting", link("controller", "power", "wire")),
        part("taillamp", "taillamp", "lighting", link("controller", "power", "wire")),
        part("stoplamp", "stoplamp", "lighting", link("controller", "power", "wire")),
        part("turn_signal", "turn_signal", "lighting", link("controller", "power", "wire")),
    ]
    act = {"FL": (False, True, True), "FR": (False, True, True), "RL": (True, True, False), "RR": (True, True, False)}
    supports = [{"id": i, "kind": "wheel", "actuation": {"drive": d, "brake": b, "steer": s}} for i, (d, b, s) in act.items()]
    for p in parts:  # every part is bolted to a rail too (the chain tests above are about the other links)
        if not any(x.get("carries") in (None, "load") for x in p["connects_to"]):
            p["connects_to"].append(link("rail_L", "load", "bolt"))
    return {"design_version": 1, "parts": parts, "supports": supports,
            "members": [{"id": "rail_R"}, {"id": "rail_L"}], "nodes": [{"id": "N1"}]}


def without(design, *ids):
    """The design with these parts gone, and every link to them."""
    d = copy.deepcopy(design)
    d["parts"] = [p for p in d["parts"] if p["id"] not in ids]
    for p in d["parts"]:
        p["connects_to"] = [x for x in p["connects_to"] if x["to"] not in ids]
    return d


def unlink(design, pid, to):
    d = copy.deepcopy(design)
    p = next(p for p in d["parts"] if p["id"] == pid)
    p["connects_to"] = [x for x in p["connects_to"] if x["to"] != to]
    return d


def check(rule_id, design, flags=ALL):
    return systems.check_rule(RULES[rule_id], design, flags)


def hit_ids(rec):
    return [h["part_id"] for h in rec["hits"]]


class CompleteDesignTest(unittest.TestCase):
    def test_every_rule_passes_on_a_design_with_every_system(self):
        for rid in RULES:
            with self.subTest(rule=rid):
                rec = check(rid, vehicle())
                self.assertEqual((rec["hits"], rec["not_evaluable"], rec["pass"]), ([], [], True), rec)
                self.assertTrue(rec["targets"])

    def test_the_flags_decide_which_rules_apply(self):
        d = vehicle()
        none = {rid: check(rid, d, {}) for rid in RULES if rid != "SY-10"}  # SY-10 (mounting) is for every product
        self.assertTrue(all(r["targets"] == [] and r["pass"] and not r["not_evaluable"] for r in none.values()))
        battery_only = {rid: bool(check(rid, d, {"battery": True})["targets"]) for rid in RULES}
        self.assertEqual({k for k, v in battery_only.items() if v}, {"SY-08", "SY-09", "SY-10"})
        walking = {rid for rid in RULES if check(rid, d, {"self_propelled": True})["targets"]}
        self.assertEqual(walking, {"SY-01", "SY-03", "SY-10"})  # steering needs a rider as well
        unknown = check("SY-01", d, None)
        self.assertEqual(unknown["not_evaluable"], [{"part_id": "design", "missing": ["flags"]}])
        self.assertTrue(unknown["pass"])


class BrokenChainTest(unittest.TestCase):
    def test_no_drive_shaft(self):
        rec = check("SY-01", without(vehicle(), "driveshaft"))
        self.assertEqual(hit_ids(rec), ["support:RL", "support:RR"])
        message = rec["hits"][0]["message"]
        for word in ("motor", "torque", "connects_to", "support:RL"):
            self.assertIn(word, message)
        self.assertEqual(rec["hits"][0]["values"], {"connected": 0.0})
        self.assertTrue(check("SY-02", without(vehicle(), "driveshaft"))["pass"])  # the other systems are not touched

    def test_a_link_left_out_is_the_gap(self):
        rec = check("SY-01", unlink(vehicle(), "driveshaft", "RR"))
        self.assertEqual(hit_ids(rec), ["support:RR"])  # RL is still joined

    def test_a_link_without_carries_is_not_a_torque_chain(self):
        d = vehicle()
        next(p for p in d["parts"] if p["id"] == "motor")["connects_to"][0].pop("carries")
        self.assertEqual(hit_ids(check("SY-01", d)), ["support:RL", "support:RR"])

    def test_the_hit_says_where_the_chain_is_cut(self):
        # the motor's link to the driveshaft carries "power" instead of "torque": the two sides are linked, with something else
        d = vehicle()
        next(p for p in d["parts"] if p["id"] == "motor")["connects_to"][0]["carries"] = "power"
        msg = check("SY-01", d)["hits"][0]["message"]
        self.assertIn("motor と driveshaft のつながりはありますが、carries が power です", msg)
        self.assertIn("「torque」にすると鎖がつながります", msg)
        # no link at all between the two sides: the last part each side reaches, and the link to write
        msg = check("SY-01", unlink(vehicle(), "driveshaft", "RR"))["hits"][0]["message"]
        self.assertIn("切れ目はこのあたりです", msg)
        self.assertIn("connects_to に", msg)
        self.assertIn("(carries: torque) を足してください", msg)

    def test_no_rack(self):
        rec = check("SY-02", without(vehicle(), "rack"))
        self.assertEqual(hit_ids(rec), ["support:FL", "support:FR"])
        self.assertIn("steer", rec["hits"][0]["message"])

    def test_no_steering_wheel_and_no_steered_wheels(self):
        self.assertEqual(hit_ids(check("SY-02", without(vehicle(), "steering"))), ["design"])
        d = vehicle()
        for s in d["supports"]:
            s["actuation"]["steer"] = False
        self.assertEqual(hit_ids(check("SY-02", d)), ["design"])

    def test_brakes(self):
        self.assertEqual(hit_ids(check("SY-03", without(vehicle(), "caliper_FL"))), ["support:FL"])
        rec = check("SY-03", without(vehicle(), "pedal"))
        self.assertEqual(hit_ids(rec), ["design"])
        self.assertIn("brake_pedal", rec["hits"][0]["message"])

    def test_the_power_chain_and_the_fuse(self):
        rec = check("SY-08", without(vehicle(), "fuse1"))
        self.assertEqual(hit_ids(rec), ["motor"])
        rec = check("SY-09", without(vehicle(), "fuse1"))
        self.assertEqual(hit_ids(rec), ["battery"])
        off_chain = unlink(unlink(vehicle(), "battery", "fuse1"), "fuse1", "controller")
        off_chain["parts"].append(part("battery_to_controller", "wire", "battery", link("battery", "power"), link("controller", "power")))
        off_chain["parts"][1]["connects_to"].append(link("lamp_only", "signal"))  # the fuse is on a chain of its own
        self.assertEqual(hit_ids(check("SY-09", off_chain)), ["battery"])
        self.assertTrue(check("SY-08", off_chain)["pass"])


class MissingPartsTest(unittest.TestCase):
    def test_no_rops(self):
        rec = check("SY-06", without(vehicle(), "rops"))
        self.assertEqual((hit_ids(rec), rec["targets"]), (["design"], ["design"]))
        self.assertIn("ROPS", rec["hits"][0]["message"])
        self.assertEqual(hit_ids(check("SY-06", unlink(vehicle(), "rops", "rail_R"))), ["rops"])  # there, not joined
        d = vehicle()  # a bar made of members of its own: joined to its own tubes is not joined to the frame
        d["members"].append({"id": "hoop", "part": "rops"})
        bar = next(p for p in d["parts"] if p["id"] == "rops")
        bar["connects_to"] = [link("hoop", "load", "weld")]
        self.assertEqual(hit_ids(check("SY-06", d)), ["rops"])
        bar["connects_to"].append(link("rail_L", "load", "weld"))
        self.assertEqual(check("SY-06", d)["hits"], [])

    def test_restraints(self):
        self.assertEqual(hit_ids(check("SY-05", without(vehicle(), "belt_L"))), ["seat_L"])
        on_the_seat = unlink(vehicle(), "belt_R", "rail_R")
        self.assertEqual(check("SY-05", on_the_seat)["hits"], [])  # the belt is on the seat and the seat on the frame: that anchors it
        alone = unlink(on_the_seat, "seat_R", "rail_R")
        rec = check("SY-05", alone)
        self.assertEqual(hit_ids(rec), ["seat_R"])
        self.assertIn("骨組み", rec["hits"][0]["message"])
        unbelted = unlink(vehicle(), "belt_R", "seat_R")  # joined to the frame, not to the seat
        self.assertEqual(hit_ids(check("SY-05", unbelted)), ["seat_R"])

    def test_seats(self):
        self.assertEqual(hit_ids(check("SY-04", unlink(vehicle(), "seat_R", "rail_R"))), ["seat_R"])
        self.assertEqual(hit_ids(check("SY-04", without(vehicle(), "seat_R", "seat_L"))), ["design"])
        no_frame = vehicle()
        no_frame["members"], no_frame["nodes"] = [], []
        self.assertEqual([n["missing"] for n in check("SY-04", no_frame)["not_evaluable"]], [["frame"], ["frame"]])

    def test_a_seat_back_and_a_head_restraint_must_be_held_too(self):
        d = vehicle()
        seat = next(p for p in d["parts"] if p["id"] == "seat_R")
        d["parts"] += [part("back_R", "seatback", "seat", link("seat_R")), part("head_R", "head_restraint", "seat", link("back_R"))]
        seat["connects_to"].append(link("back_R"))
        rec = check("SY-04", d)
        self.assertEqual((rec["hits"], rec["pass"]), ([], True))
        self.assertIn("head_R", rec["targets"])  # they are part of what the rule looked at
        loose = unlink(d, "back_R", "seat_R")
        loose = unlink(loose, "seat_R", "back_R")
        self.assertEqual(sorted(hit_ids(check("SY-04", loose))), ["back_R", "head_R"])  # neither is joined to the seat or the frame
        self.assertIn("頭を受け止める", check("SY-04", loose)["hits"][0]["message"])
        self.assertEqual(hit_ids(check("SY-04", unlink(d, "head_R", "back_R"))), ["head_R"])  # only the head restraint is loose

    def test_lamps(self):
        rec = check("SY-07", without(vehicle(), "taillamp", "stoplamp"))
        self.assertEqual(len(rec["hits"]), 2)
        self.assertTrue(any("尾灯" in h["message"] for h in rec["hits"]) and any("制動灯" in h["message"] for h in rec["hits"]))


class NoLinksTest(unittest.TestCase):
    def bare(self, *gone):
        d = without(vehicle(), *gone)
        for p in d["parts"]:
            p["connects_to"] = []
        return d

    def test_chains_cannot_be_followed_without_connects_to(self):
        d = self.bare()
        for rid in ("SY-01", "SY-02", "SY-03", "SY-08"):
            rec = check(rid, d)
            self.assertEqual(rec["hits"], [], rid)
            self.assertTrue(rec["not_evaluable"] and all(n["missing"] == ["connects_to"] for n in rec["not_evaluable"]), rid)
        self.assertEqual({n["part_id"] for n in check("SY-01", d)["not_evaluable"]}, {"motor", "support:RL", "support:RR"})
        self.assertEqual([n["part_id"] for n in check("SY-04", d)["not_evaluable"]], ["seat_R", "seat_L"])

    def test_a_part_that_is_not_there_is_a_hit_without_connects_to(self):
        self.assertEqual(hit_ids(check("SY-06", self.bare("rops"))), ["design"])
        self.assertEqual(hit_ids(check("SY-05", self.bare("belt_R", "belt_L"))), ["seat_R", "seat_L"])
        self.assertEqual(hit_ids(check("SY-09", self.bare("fuse1"))), ["battery"])
        self.assertEqual(hit_ids(check("SY-03", self.bare("pedal"))), ["design"])
        self.assertEqual(len(check("SY-07", self.bare("headlamp"))["hits"]), 1)


class TargetsTest(unittest.TestCase):
    """Contract: every part_id in hits and not_evaluable is among targets; "design" stands for the whole design."""

    def designs(self):
        base = vehicle()
        yield "complete", base
        for p in base["parts"]:
            yield f"without {p['id']}", without(base, p["id"])
        for p in base["parts"]:
            for x in p["connects_to"]:
                yield f"{p['id']} not to {x['to']}", unlink(base, p["id"], x["to"])
        bare = copy.deepcopy(base)
        for p in bare["parts"]:
            p["connects_to"] = []
        yield "bare", bare
        for p in base["parts"]:
            yield f"bare without {p['id']}", without(bare, p["id"])
        yield "empty", {"design_version": 1, "parts": [], "supports": [], "members": [], "nodes": []}
        yield "no frame", {**base, "members": [], "nodes": []}

    def test_every_hit_and_every_gap_is_a_target(self):
        for name, design in self.designs():
            for flags in (ALL, {}, None):
                for rid in RULES:
                    rec = check(rid, design, flags)
                    where = f"{rid} on {name} with flags {flags}"
                    named = {x["part_id"] for x in rec["hits"]} | {x["part_id"] for x in rec["not_evaluable"]}
                    self.assertLessEqual(named, set(rec["targets"]), where)
                    self.assertEqual(len(rec["targets"]), len(set(rec["targets"])), where)
                    self.assertEqual(rec["pass"], not rec["hits"], where)
                    self.assertFalse({x["part_id"] for x in rec["hits"]} & {x["part_id"] for x in rec["not_evaluable"]}, where)
                    self.assertEqual(contract.errors("rule_check", {**rec, "rule_check_id": "rchk-0001", "design_version": 1}), [], where)

    def test_a_chain_names_the_parts_it_looked_at(self):
        bare = copy.deepcopy(vehicle())
        for p in bare["parts"]:
            p["connects_to"] = []
        self.assertEqual(set(check("SY-01", bare)["targets"]), {"motor", "support:RL", "support:RR"})
        self.assertEqual(set(check("SY-02", bare)["targets"]), {"steering", "support:FL", "support:FR"})
        self.assertEqual(set(check("SY-08", bare)["targets"]), {"battery", "motor"})
        self.assertEqual(set(check("SY-05", bare)["targets"]), {"seat_R", "seat_L", "belt_R", "belt_L"})
        self.assertEqual(set(check("SY-09", bare)["targets"]), {"battery", "fuse1"})
        self.assertIn("motor", check("SY-01", bare)["targets"])

    def test_a_hit_on_the_whole_design_puts_design_among_the_targets(self):
        for rid, gone in (("SY-01", "motor"), ("SY-02", "steering"), ("SY-03", "pedal"), ("SY-06", "rops"), ("SY-08", "battery")):
            rec = check(rid, without(vehicle(), gone))
            self.assertEqual(hit_ids(rec), ["design"], rid)
            self.assertIn("design", rec["targets"], rid)
        self.assertEqual(check("SY-01", vehicle(), None)["targets"], ["design"])
        self.assertEqual(check("SY-01", vehicle(), {})["targets"], [])


class RulesFileTest(unittest.TestCase):
    def test_every_rule_has_its_spec_and_its_function(self):
        specs = systems.specs()
        self.assertEqual(sorted(specs), sorted(RULES))
        for rid, rule in RULES.items():
            self.assertEqual(rule["category"], "system")
            self.assertEqual(rule["origin"], "builtin")
            self.assertEqual(rule["trust"], 3)
            self.assertIn(systems.check_name(rule["check"]), systems.CHECKS)
            self.assertTrue("assumption" in rule["basis"] and ("not checked" in rule["basis"] or "ISO" not in rule["basis"]), rid)
            self.assertEqual(contract.errors("rule", rule), [], rid)
        self.assertEqual(set(systems.CHECKS), {systems.check_name(r["check"]) for r in RULES.values()})

    def test_a_dropped_rule_cannot_name_a_function(self):
        with self.assertRaises(Exception):
            knowledge.make_rule("U1-01", "U1", "system", "x", "system:rops", origin="dropped", check_by="claude", trust=3, basis="b")
        with self.assertRaises(Exception):
            knowledge.make_rule("U1-01", "U1", "battery", "x", "system:nothing", origin="builtin", check_by=None, trust=3, basis="b")


class WithTheOtherRulesTest(unittest.TestCase):
    def setUp(self):
        self.design = read_data(REPO_ROOT / "data" / "designs" / "offroad-4w-v1.json")
        self.product = load_product("offroad-4w")
        self.rules = knowledge.all_rules(knowledge.load_builtin())

    def run_all(self, design, flags=True):
        kwargs = {"flags": self.product["flags"]} if flags else {}
        checks = rulecheck.check_all(self.rules, design, self.product["requirements"], **kwargs)
        self.assertEqual(len(checks), len(self.rules))
        for c in checks:
            self.assertEqual(contract.errors("rule_check", c), [], c["rule_id"])
        return {c["rule_id"]: c for c in checks if c["rule_id"].startswith("SY-")}

    def test_check_all_gives_records_the_contract_takes(self):
        by = self.run_all(self.design)
        flags = self.product["flags"]
        self.assertTrue(flags["ride"] and flags["self_propelled"] and flags["rough"] and flags["battery"])
        self.assertEqual(sorted(by), sorted(RULES))
        flagless = self.run_all(self.design, flags=False)
        self.assertEqual(flagless["SY-01"]["not_evaluable"], [{"part_id": "design", "missing": ["flags"]}])
        self.assertEqual(flagless["SY-01"]["targets"], ["design"])

    # the part that holds the steering column to the frame: welded to dash_cross and dash_post_R (the wheel hangs on
    # the column, the column on this bracket: designgen kit/joints.py)
    COLUMN = ("bracket_column",)

    def moved(self, design, near):
        """The design with the column's bracket placed on the dash cross member (near: the middle of LDT-RDT) or far
        from every member."""
        d = copy.deepcopy(design)
        nodes = {n["id"]: n["xyz_mm"] for n in d["nodes"]}
        at = [(a + b) / 2.0 for a, b in zip(nodes["LDT"], nodes["RDT"])]  # on dash_cross, which the bracket names
        for p in d["parts"]:
            if p["id"] in self.COLUMN:
                p["box"]["center_mm"] = list(at) if near else [5000.0, 5000.0, 5000.0]
        return d

    def test_a_part_drawn_in_the_air_is_found_even_when_it_says_it_is_bolted_on(self):
        far = self.run_all(self.moved(self.design, near=False))["SY-10"]
        # the bracket is far from the members it names; the column and the wheel that hang on it are then not
        # held to the frame either
        self.assertEqual(sorted(hit_ids(far)), sorted(self.COLUMN + ("steering_column", "steering_wheel")))
        self.assertEqual(far["pass"], False)
        for h in far["hits"]:
            if h["part_id"] in self.COLUMN:
                self.assertGreater(h["values"]["gap_mm"], systems.MOUNT_GAP_MM)
                self.assertIn("mm 離れています", h["message"])
            else:
                self.assertEqual(h["values"], {"attached": 0.0})
        self.assertLessEqual(set(hit_ids(far)), set(far["targets"]))
        near = self.run_all(self.moved(self.design, near=True))["SY-10"]
        self.assertEqual((near["hits"], near["pass"]), ([], True))

    def test_a_part_with_no_structural_link_floats_and_a_shaft_held_at_both_ends_does_not(self):
        d = vehicle()
        d["parts"] = [p for p in d["parts"]]
        lone = part("bracket", "body", "body", link("controller", "power", "wire"))  # only a power link: it holds nothing
        d["parts"].append(lone)
        rec = check("SY-10", d)
        self.assertEqual(hit_ids(rec), ["bracket"])
        self.assertEqual(rec["hits"][0]["values"], {"attached": 0.0})
        self.assertNotIn("driveshaft", hit_ids(rec))  # joined to the motor and the wheels: held at both ends
        people = copy.deepcopy(d)
        people["parts"].append({"id": "driver", "name": "driver", "kind": "occupant", "connects_to": []})
        self.assertEqual(hit_ids(check("SY-10", people)), ["bracket"])  # people are carried, not mounted

    def test_the_demo_vehicle_has_every_system_it_needs_and_joins_them(self):
        by = self.run_all(self.moved(self.design, near=True))
        for rid, rec in by.items():
            self.assertEqual((rec["hits"], rec["not_evaluable"]), ([], []), rid)
        self.assertEqual(by["SY-07"]["targets"], [])  # not a road vehicle: no lamps asked for
        for rid in ("SY-01", "SY-02", "SY-03", "SY-04", "SY-05", "SY-06", "SY-08", "SY-09"):
            self.assertTrue(by[rid]["targets"] and by[rid]["pass"], rid)

    def test_the_demo_vehicle_with_a_system_taken_out_says_where(self):
        belts = ("seat_belt_R", "seat_belt_L")
        for rid, gone, expected in (("SY-03", ("pedal_brake",), ["design"]), ("SY-05", belts, ["seat_R", "seat_L"]),
                                    ("SY-06", ("rollbar",), ["design"]), ("SY-09", ("main_switch",), ["battery"])):
            rec = self.run_all(without(self.design, *gone))[rid]
            self.assertEqual(sorted(hit_ids(rec)), sorted(expected), rid)
            self.assertLessEqual(set(hit_ids(rec)), set(rec["targets"]), rid)
        # the pedal's pushrod (designgen kit/joints) goes with it: left alone it would hang on the master cylinder only
        others = self.run_all(without(self.moved(self.design, near=True), "pedal_brake", "pushrod_brake"))
        self.assertTrue(all(r["pass"] for rid, r in others.items() if rid != "SY-03"))

    def test_the_demo_vehicle_without_links_cannot_be_followed(self):
        bare = copy.deepcopy(self.design)
        for p in bare["parts"]:
            p["connects_to"] = []
        by = self.run_all(bare)
        self.assertEqual(by["SY-01"]["hits"], [])
        self.assertIn("motor", {n["part_id"] for n in by["SY-01"]["not_evaluable"]})
        self.assertLessEqual({n["part_id"] for n in by["SY-01"]["not_evaluable"]}, set(by["SY-01"]["targets"]))

    def test_the_flags_are_the_contracts(self):
        for spec in systems.specs().values():
            self.assertTrue(set(systems.expression.names(spec["when"])) <= set(FLAGS))


if __name__ == "__main__":
    unittest.main()
