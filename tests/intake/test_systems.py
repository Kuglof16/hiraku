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
        none = {rid: check(rid, d, {}) for rid in RULES}
        self.assertTrue(all(r["targets"] == [] and r["pass"] and not r["not_evaluable"] for r in none.values()))
        battery_only = {rid: bool(check(rid, d, {"battery": True})["targets"]) for rid in RULES}
        self.assertEqual({k for k, v in battery_only.items() if v}, {"SY-08", "SY-09"})
        walking = {rid for rid in RULES if check(rid, d, {"self_propelled": True})["targets"]}
        self.assertEqual(walking, {"SY-01", "SY-03"})  # steering needs a rider as well
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
        self.assertEqual((hit_ids(rec), rec["targets"]), (["design"], []))
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
    def test_check_all_gives_records_the_contract_takes(self):
        design = read_data(REPO_ROOT / "data" / "designs" / "offroad-4w-v1.json")
        product = load_product("offroad-4w")
        rules = knowledge.all_rules(knowledge.load_builtin())
        checks = rulecheck.check_all(rules, design, product["requirements"], flags=product["flags"])
        self.assertEqual(len(checks), len(rules))
        for c in checks:
            self.assertEqual(contract.errors("rule_check", c), [], c["rule_id"])
        by = {c["rule_id"]: c for c in checks}
        flags = product["flags"]
        self.assertTrue(flags["ride"] and flags["self_propelled"] and flags["rough"] and flags["battery"])
        # the demo vehicle has no parts for brakes, restraints, a roll bar or a fuse, and says nothing about links
        self.assertEqual(hit_ids(by["SY-03"]), ["design"])
        self.assertEqual(hit_ids(by["SY-05"]), ["seat_R", "seat_L"])
        self.assertEqual(hit_ids(by["SY-06"]), ["design"])
        self.assertEqual(hit_ids(by["SY-09"]), ["battery"])
        self.assertIn("motor", {n["part_id"] for n in by["SY-01"]["not_evaluable"]})
        self.assertEqual(by["SY-01"]["hits"], [])
        flagless = {c["rule_id"]: c for c in rulecheck.check_all(rules, design, product["requirements"])}
        self.assertEqual(flagless["SY-01"]["not_evaluable"], [{"part_id": "design", "missing": ["flags"]}])

    def test_the_flags_are_the_contracts(self):
        for spec in systems.specs().values():
            self.assertTrue(set(systems.expression.names(spec["when"])) <= set(FLAGS))


if __name__ == "__main__":
    unittest.main()
