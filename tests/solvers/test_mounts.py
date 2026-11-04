"""The suspension's mounts on the frame (designgen kit/mounts.py), the footwell and dash, and the occupants'
bodies as parts, on the demo vehicle (data/designs/offroad-4w-v1.json)."""

import json
import unittest
from pathlib import Path

import numpy as np

from hiraku.contract.loading import on_board, parts_on_board, person_of
from hiraku.designgen import ladder_4w
from hiraku.designgen.kit import drivetrain, mounts, systems
from hiraku.solvers.k1_frame.analyze import mount_carriers
from hiraku.solvers.k2_vehicle.model import hardpoints_for, hardpoints_of
from hiraku.solvers.k4_geometry import bodies, fit, interferences
from hiraku.solvers.k4_geometry.motion import _hits
from hiraku.solvers.k4_geometry.people import occupants

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
BODY_MOUNTS = ("lca_front", "lca_rear", "uca_front", "uca_rear", "damper_upper", "tie_inner")


def part(design, pid):
    return next(p for p in design["parts"] if p["id"] == pid)


def nodes(design):
    return {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}


class HardpointsTest(unittest.TestCase):
    def test_arms_and_dampers_clear_the_frame_through_the_travel(self):
        """GAP_MM off the rails (the rule the hard points are moved by); off every other member at all."""
        travel = V1["variables"]["suspension_travel"]["value"]
        rail = {"y": 350.0, "z": 380.0, "b": 40.0, "h": 80.0}
        for s in V1["supports"]:
            c = mounts.rail_clearances(s["suspension"]["hardpoints_mm"], float(s["radius_mm"]), travel, rail)
            self.assertGreaterEqual(c["upper_arm_mm"], mounts.ARM_RADIUS_MM + mounts.GAP_MM - 1e-6, s["id"])
            self.assertGreaterEqual(c["damper_mm"], mounts.DAMPER_RADIUS_MM + mounts.GAP_MM - 1e-6, s["id"])
        for sid, res in mounts.link_clearances(V1, travel).items():
            for link, (gap, member) in res.items():
                self.assertGreater(gap, 0.0, f"{sid} {link} {gap:.1f} mm from {member}")

    def test_moved_from_k2s_builtin_points(self):
        for s in V1["supports"]:
            hp = s["suspension"]["hardpoints_mm"]
            builtin = hardpoints_for(s)
            self.assertGreater(hp["uca_outer"][2], builtin["uca_outer"][2] + 20.0)  # the upper arm raised
            self.assertGreater(abs(hp["damper_upper"][1]), abs(builtin["damper_upper"][1]))  # the damper's top out
            self.assertGreater(hp["damper_upper"][2], builtin["damper_upper"][2])  # and up, over the ball joint
            for k in ("lca_front", "lca_rear", "lca_outer", "wheel_center"):  # the lower arm stays
                self.assertEqual(hp[k], [float(v) for v in builtin[k]])
            self.assertEqual(hardpoints_of(V1, s["id"])["uca_outer"], hp["uca_outer"])  # K2 runs these points

    def test_rail_clearance_of_k2s_example(self):
        """K2's example for v1's front left wheel (upper joint 455 mm, damper top at y 410 mm): the upper arm
        25 to 28 mm over the rail and the damper 35 to 37 mm off its outer wall, at rest."""
        s = V1["supports"][0]
        rail = {"y": 350.0, "z": 380.0, "b": 40.0, "h": 80.0}
        hp = hardpoints_for(s, upper_joint_z_mm=455.0, damper_upper_y_mm=410.0)
        c = mounts.rail_clearances(hp, float(s["radius_mm"]), 0.0, rail)
        self.assertAlmostEqual(c["upper_arm_mm"], 24.8, delta=0.5)
        self.assertAlmostEqual(c["damper_mm"], 35.2, delta=0.5)


class MountMembersTest(unittest.TestCase):
    def test_every_body_mount_is_carried_by_the_frame(self):
        """mount_carriers finds a member's node (tower top, leg end) or a bracket for each of K2's body mounts."""
        carried = mount_carriers(V1)
        brackets = {p["id"]: p for p in V1["parts"] if p["kind"] == "bracket"}
        for s in V1["supports"]:
            for k in BODY_MOUNTS:
                self.assertTrue(carried[s["id"]][k], f"{s['id']} {k} goes to the wheel mount")
            n = nodes(V1)
            hp = s["suspension"]["hardpoints_mm"]
            rear_pivot = ("lca_front", "lca_rear") if not s["actuation"]["steer"] else ()  # at the rear: brackets
            for k in ("lca_front", "lca_rear", "damper_upper"):  # these are member nodes
                if k in rear_pivot:
                    continue
                at = carried[s["id"]][k][0]
                self.assertLess(float(np.linalg.norm(n[at] - np.asarray(hp[k]))), 1e-6)
            held = systems.body_mounts(V1, s)
            for k in ("uca_front", "uca_rear", "tie_inner", *rear_pivot):  # these are in brackets on rail nodes
                self.assertIn(held[k], brackets)
                self.assertEqual(carried[s["id"]][k], brackets[held[k]]["carried_by"])

    def test_towers_legs_and_cross_members(self):
        ids = {m["id"] for m in V1["members"]}
        for st in ("LA", "RA"):
            for m in (f"tower_post_{st}1", f"tower_post_{st}2", f"tower_top_{st}1", f"tower_top_{st}2", f"tower_arm_{st}",
                      f"lower_leg_{st}1", f"lower_leg_{st}2"):
                self.assertIn(m, ids)
        # the rails either side of the rear axle stay whole (the hand calculations read them): one post on the
        # axle's node, brackets for the pivots
        for st in ("LB", "RB"):
            self.assertTrue({f"tower_post_{st}", f"tower_arm_{st}"} <= ids)
            self.assertFalse({f"tower_post_{st}1", f"tower_post_{st}2", f"lower_leg_{st}1", f"lower_leg_{st}2"} & ids)
            post = next(m for m in V1["members"] if m["id"] == f"tower_post_{st}")
            self.assertEqual(post["node_a"], st)
        for m, ends in (("rail_L_BE", ("LB", "LE")), ("rail_L_HB", ("LH", "LB")), ("rail_L_S2H", ("LS2", "LH"))):
            be = next(x for x in V1["members"] if x["id"] == m)
            self.assertEqual((be["node_a"], be["node_b"]), ends)
        # at the rear the pivots are in brackets (not member nodes): lower cross members only at the front
        self.assertEqual({m for m in ids if m.startswith("lower_cross_")}, {"lower_cross_A1", "lower_cross_A2"})
        self.assertIn("rail_L_BE", ids)  # the rail piece at the rear end keeps its name
        off = ladder_4w.build({"lower_cross": {"value": 0, "unit": "1"}})
        self.assertFalse(any(m["id"].startswith("lower_cross_") for m in off["members"]))

    def test_brackets(self):
        n = nodes(V1)
        rail_nodes = {k for k, q in n.items() if abs(abs(q[1]) - 350.0) < 1e-6 and abs(q[2] - 380.0) < 1e-6}
        # (the joints' struts, kit/joints.py, are brackets too, named fit_*: not these mounts)
        brackets = [p for p in V1["parts"] if p["kind"] == "bracket" and not p["id"].startswith("fit_")]
        # two upper arm pivots and a tie rod end per wheel, the rear lower arms' two pivots, the column; and the
        # clevises kit/arms.pivot_brackets welds on bare nodes: the front lower arms' pivots (the lower legs'
        # ends) and every damper's top (the tower arm's tip)
        clevis = {f"bracket_lca_{s}{i}" for s in ("FL", "FR") for i in (1, 2)} | {f"bracket_damper_{s}" for s in
                                                                                ("FL", "FR", "RL", "RR")}
        self.assertEqual(len(brackets), 4 * 3 + 2 * 2 + 1 + len(clevis))
        mass = sum(p["mass_kg"] for p in brackets)
        self.assertGreater(mass, 1.0)
        self.assertLess(mass, 15.0)  # 11.6 kg on v1: the rear lower arms' pivot brackets hang 200 mm from the rails
        for p in brackets:
            self.assertEqual(p["system"], "frame")
            self.assertTrue(all(c["via"] == "weld" for c in p["connects_to"]))
            if p["id"] in clevis:
                self.assertEqual(len(p["carried_by"]), 1, p["id"])
            elif p["id"] != "bracket_column":
                self.assertTrue(set(p["carried_by"]) <= rail_nodes, p["id"])

    def test_no_part_id_is_a_support_id_and_links_name_what_is_there(self):
        sup = {s["id"] for s in V1["supports"]}
        targets = sup | {p["id"] for p in V1["parts"]} | {m["id"] for m in V1["members"]} | set(nodes(V1))
        targets |= {f"{s['id']}.{k}" for s in V1["supports"] for k in s["suspension"]["hardpoints_mm"]}
        for p in V1["parts"]:
            self.assertNotIn(p["id"], sup)
            for c in p.get("connects_to", []):
                self.assertIn(c["to"], targets, f"{p['id']} -> {c['to']}")

    def test_suspension_parts_join_their_mounts(self):
        from hiraku.designgen.kit.arms import part_ids

        for s in V1["supports"]:  # each arm's leg and the damper to the bracket holding its pivot (kit/arms.py)
            ids = part_ids(s["id"])
            held = systems.body_mounts(V1, s)
            for mount, role in (("lca_front", "lca_f"), ("lca_rear", "lca_r"), ("uca_front", "uca_f"),
                                ("uca_rear", "uca_r"), ("damper_upper", "damper")):
                self.assertIn(held[mount], {c["to"] for c in part(V1, ids[role])["connects_to"]}, (s["id"], mount))


class FootwellAndDashTest(unittest.TestCase):
    def test_column_held_by_the_dash_cross_member(self):
        col = part(V1, "steering_column")
        # the column is bolted to its bracket, the bracket welded to the dash cross member (kit/joints.py: the joint
        # is where the two touch)
        self.assertIn(("bracket_column", "bolt"), {(c["to"], c["via"]) for c in col["connects_to"]})
        self.assertIn(("dash_cross", "weld"), {(c["to"], c["via"]) for c in part(V1, "bracket_column")["connects_to"]})
        dash = next(m for m in V1["members"] if m["id"] == "dash_cross")
        self.assertEqual(sorted(col["carried_by"]), sorted([dash["node_a"], dash["node_b"]]))
        self.assertEqual(part(V1, "bulkhead")["kind"], "body")
        bh = part(V1, "bulkhead")["box"]
        # on its front face, ahead of the brake pedal, pushed through the sheet by its pushrod (kit/joints.py)
        self.assertAlmostEqual(part(V1, "brake_master")["box"]["center_mm"][0] - 60.0,
                               bh["center_mm"][0] + bh["size_mm"][0] / 2.0, places=6)

    def test_the_three_layout_errors_are_gone(self):
        """The right leg was 5 mm into the front wheel, the feet 146 mm off the footwell floor, and the rear
        driveshafts met the rails by 12 mm at full bump."""
        on = on_board(V1, "two_cargo")
        f = fit(on)
        self.assertGreater(f["leg_clearance.occupant_R"][0], 0.0)
        self.assertGreater(f["foot_support.occupant_R"][0], 0.0)
        self.assertGreater(f["foot_room.occupant_R"][0], 0.0)
        bs = bodies(on)
        for sid in ("RL", "RR"):
            hits = [h for h in _hits(on, bs, sid, 75.0, 0.0) if "driveshaft" in h[0]]
            self.assertEqual(hits, [], sid)
        self.assertGreater(V1["variables"][drivetrain.DROP_VARIABLE]["value"], 0.0)
        self.assertEqual(interferences(on), [])

    def test_h3_numbers_stay_for_the_revision(self):
        """The small driver's leg to the pedal is H3's check (the revision fixes it), not a layout error: she
        reaches it (pedal_reach), with the knee straighter than the comfortable range (knee_angle_room)."""
        f = fit(on_board(V1, "two_cargo"))
        self.assertGreater(f["pedal_reach.occupant_R"][0], 0.0)
        self.assertLess(f["knee_angle_room.occupant_R"][0], 0.0)
        self.assertIn("wheel_reach.occupant_R", f)


class BodyPartsTest(unittest.TestCase):
    def test_people_are_their_segments(self):
        occ = [p for p in V1["parts"] if p["kind"] == "occupant"]
        people = {person_of(p) for p in occ}
        self.assertEqual(people, {"occupant_R", "occupant_L"})
        self.assertGreater(len(occ), 2)
        m = V1["variables"]["occupant_mass"]["value"]
        for who in people:
            self.assertAlmostEqual(sum(p["mass_kg"] for p in occ if person_of(p) == who), m, places=9)
        self.assertEqual([p["id"] for p in occupants(V1)], ["occupant_R", "occupant_L"])
        driver = [p for p in parts_on_board(V1, "driver") if p["kind"] == "occupant"]
        self.assertEqual({person_of(p) for p in driver}, {"occupant_R"})
        self.assertFalse(any("." in k.split(".", 1)[1] for k in fit(on_board(V1, "two_cargo")) if "." in k))

    def test_assembly_record_says_its_rules(self):
        from hiraku.solvers.shelf.assembly import HAND_WAY_RULE, PEDAL_RULE, Assembly

        d = {"design_version": 1, "name": "check", "variables": {}, "supports": [], "nodes": [], "members": [],
             "sections": {}, "materials": {},
             "parts": [{"id": "pedal", "name": "pedal", "kind": "brake_pedal",
                        "box": {"center_mm": [0, 0, 0], "size_mm": [100, 80, 20]}, "mass_kg": 1.0}]}
        out = Assembly().analyse(d, {"params": {"loading": "empty"}}, {})
        self.assertEqual(out["assumptions"], [PEDAL_RULE, HAND_WAY_RULE])
        self.assertIn("レールの外の面", PEDAL_RULE)
        self.assertIn("継ぎ目", HAND_WAY_RULE)


if __name__ == "__main__":
    unittest.main()
