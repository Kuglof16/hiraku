"""The roll bar: its design variables (designgen), the occupants' space against it (K4), the portal frame
formulas and their inputs (K0), and the beam and shell models with it (K1)."""

import json
import math
import os
import unittest
from pathlib import Path

from hiraku import designgen, solvers
from hiraku.contract.loading import on_board
from hiraku.designgen import ladder_4w
from hiraku.designgen.ladder_4w import DesignGenError
from hiraku.solvers.k0_handcalc.frame_inputs import ladder_twist_inputs, plastic_modulus, portal_frame_inputs
from hiraku.solvers.k0_handcalc.vehicle import mass_summary, member_props
from hiraku.solvers.k4_geometry import AIST, interferences, occupant_clearance, occupant_spaces, sweep

ROOT = Path(__file__).resolve().parents[2]
# the demo vehicle with the roll bar switched off (it is on by default)
V1 = ladder_4w.build({"rollbar": {"value": 0.0, "unit": "1"}})
# the demo vehicle (the roll bar is on by default; the earlier offroad-4w-rollbar.json was the same vehicle)
RB = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
FORMULAS = solvers.formula_registry()
SLOW = os.environ.get("HIRAKU_SLOW_TESTS") == "1"
ROLLBAR_MEMBERS = {"rollbar_foot_L", "rollbar_foot_R", "rollbar_post_L", "rollbar_post_R", "rollbar_top"}


def q(v, u):
    return {"value": float(v), "unit": u}


def xyz(design):
    return {n["id"]: n["xyz_mm"] for n in design["nodes"]}


def box(design, pid):
    p = next(p for p in design["parts"] if p["id"] == pid)
    c, s = p["box"]["center_mm"], p["box"]["size_mm"]
    return [c[i] - s[i] / 2 for i in range(3)], [c[i] + s[i] / 2 for i in range(3)]


def out(formula, **inputs):
    return {k: v["value"] for k, v in FORMULAS.evaluate(formula, inputs)["outputs"].items()}


class RollbarDesignTest(unittest.TestCase):
    def test_switched_off_has_no_roll_bar(self):
        d = V1
        self.assertFalse(any(m["id"].startswith("rollbar") for m in d["members"]))
        self.assertFalse(any(p["kind"] == "rollbar" for p in d["parts"]))
        self.assertEqual(d["variables"]["rollbar"]["value"], 0.0)
        self.assertEqual(set(d["materials"]), {"stkr400"})

    def test_default_has_the_roll_bar(self):
        d = ladder_4w.build()
        self.assertEqual({m["id"] for m in d["members"] if m["id"].startswith("rollbar")}, ROLLBAR_MEMBERS)
        self.assertEqual({m["part"] for m in d["members"] if m["id"] in ROLLBAR_MEMBERS}, {"rollbar"})
        part = next(p for p in d["parts"] if p["id"] == "rollbar")
        self.assertEqual((part["kind"], part["mass_kg"]), ("rollbar", 0.0))
        old = json.loads(json.dumps(V1))  # a design from before the roll bar was on by default: no variable, no members
        old["variables"] = {k: v for k, v in old["variables"].items() if not k.startswith("rollbar")}
        self.assertEqual(ladder_4w.values_of(old)["rollbar"]["value"], 0.0)

    def test_the_demo_carries_the_roll_bar_variables(self):
        for k in ladder_4w.ROLLBAR_VARS:  # its variables, so revise_design can change them
            self.assertIn(k, RB["variables"])
            self.assertTrue(ladder_4w.VARIABLES[k].revisable)

    def test_placed_from_the_frame_and_the_seats(self):
        v = {k: x["value"] for k, x in RB["variables"].items()}
        n = xyz(RB)
        s2 = n["LS2"][0]
        seat_top = box(RB, "seat_R")[1][2]
        self.assertAlmostEqual(n["LH"][0], s2 - v["rollbar_offset"])
        self.assertEqual(n["LH"][1:], [v["rail_spacing"] / 2, v["rail_height"]])  # on the rail
        self.assertEqual(n["LHO"][1:], [v["rollbar_width"] / 2, v["rail_height"]])  # the outrigger's end
        self.assertAlmostEqual(n["LHT"][2], seat_top + v["rollbar_height"])
        self.assertEqual(n["RHT"], [n["LHT"][0], -n["LHT"][1], n["LHT"][2]])
        ids = [m["id"] for m in RB["members"]]
        self.assertNotIn("rail_L_S2B", ids)  # the rail is split at the roll bar's node
        self.assertEqual(ids[ids.index("rail_L_S2H") + 1], "rail_L_HB")
        self.assertEqual(ROLLBAR_MEMBERS, {m for m in ids if m.startswith("rollbar")})
        self.assertEqual(RB["materials"]["stkm13a"]["yield_MPa"], 215.0)
        # the parts are where v1 has them: the roll bar does not move them or their fixing nodes
        self.assertEqual([p for p in RB["parts"] if p["id"] != "rollbar"], V1["parts"])  # and its own part of 0 kg

    def test_revised_variables_move_it(self):
        v2 = designgen.regenerate(RB, {"rollbar_offset": q(150, "mm"), "rollbar_height": q(1000, "mm"),
                                       "rail_height": q(400, "mm")})
        n, n1 = xyz(v2), xyz(RB)
        self.assertAlmostEqual(n["LH"][0], n1["LH"][0] - 70.0)
        self.assertAlmostEqual(n["LHT"][2], n1["LHT"][2] - 100.0 + 20.0)  # with the seats, 20 mm higher rails
        narrow = designgen.regenerate(RB, {"rollbar_width": q(700, "mm")})  # posts on the rails, no outriggers
        self.assertNotIn("rollbar_foot_L", {m["id"] for m in narrow["members"]})
        self.assertEqual(xyz(narrow)["LHT"][1], 350.0)
        sq = designgen.regenerate(RB, {"rollbar_square": q(1, "1")})
        post = next(m for m in sq["members"] if m["id"] == "rollbar_post_L")
        self.assertEqual((post["material"], sq["sections"][post["section"]]["shape"]), ("stkr400", "rect_tube"))
        for bad, why in (({"rollbar_width": q(650, "mm")}, "outside"),
                         ({"wheelbase": q(1700, "mm"), "rollbar_offset": q(350, "mm")}, "rear axle"),
                         ({"rollbar": q(0.5, "1")}, "switch")):
            with self.assertRaisesRegex(DesignGenError, why):
                designgen.regenerate(RB, bad)

    def test_mass_goes_up_by_the_members(self):
        props = member_props(RB)
        added = sum(float(mp["material"]["density_kg_m3"]) * mp["section"]["A"] * mp["length"] * 1e-9
                    for mid, mp in props.items() if mid in ROLLBAR_MEMBERS)
        self.assertAlmostEqual(mass_summary(RB)["mass_kg"] - mass_summary(V1)["mass_kg"], added, places=6)
        self.assertGreater(added, 5.0)

    def test_twist_hand_calculation_counts_only_cross_members(self):
        self.assertEqual(ladder_twist_inputs(RB)["inputs"]["n_c"], ladder_twist_inputs(V1)["inputs"]["n_c"])

    def test_no_interference_with_the_occupants_or_seats(self):
        on = on_board(RB, "two_cargo")
        hits = [h for h in interferences(on) if any(f"member:{m}" in h[:2] for m in ROLLBAR_MEMBERS)]
        self.assertEqual(hits, [])
        moving = [h for h in sweep(on)["hits"] if any(f"member:{m}" in h[:2] for m in ROLLBAR_MEMBERS)]
        self.assertEqual(moving, [])
        before = {(a, b) for a, b, _ in interferences(on_board(V1, "two_cargo"))}
        self.assertEqual({(a, b) for a, b, _ in interferences(on)}, before)


class OccupantSpaceTest(unittest.TestCase):
    """Known answers: one occupant on a seat and one round tube member."""

    def design(self, a, b, d=40.0):
        return {"nodes": [{"id": "A", "xyz_mm": a}, {"id": "B", "xyz_mm": b}],
                "members": [{"id": "rollbar_top", "node_a": "A", "node_b": "B", "section": "ct", "material": "s"}],
                "sections": {"ct": {"shape": "round_tube", "dims_mm": {"d": d, "t": 2.0}}},
                "materials": {"s": {"E_MPa": 2e5, "nu": 0.3, "density_kg_m3": 7850, "yield_MPa": 235}},
                "parts": [{"id": "seat", "kind": "seat", "box": {"center_mm": [0, 0, 100], "size_mm": [500, 500, 200]},
                           "mass_kg": 10},
                          {"id": "occ", "kind": "occupant", "box": {"center_mm": [0, 0, 400], "size_mm": [400, 400, 400]},
                           "mass_kg": 70}]}

    def test_space_from_the_box_and_aist(self):
        sp = occupant_spaces(self.design([0, 0, 0], [1, 0, 0]))[0]
        half = AIST["shoulder_breadth_m95"] / 2  # wider than the 400 mm box
        self.assertEqual(sp["lo_mm"], [-200.0, -half, 200.0])
        self.assertEqual(sp["hi_mm"], [200.0, half, 200.0 + AIST["sitting_height_m95"]])

    def test_clearance_behind_above_and_inside(self):
        top = 200.0 + AIST["sitting_height_m95"]
        d = self.design([-300, -400, top + 50], [-300, 400, top + 50])  # behind and above the space's corner edge
        r = occupant_clearance(d)
        self.assertAlmostEqual(r["clearance_mm"], math.hypot(100.0, 50.0) - 20.0, places=6)
        self.assertEqual((r["member"], r["occupant"]), ("rollbar_top", "occ"))
        # crushed: both ends 160 mm forward and 50 mm down: the tube is in the space, 20 mm deep below its top face...
        crushed = occupant_clearance(d, displacements={"A": [160, 0, -50], "B": [160, 0, -50]})
        self.assertAlmostEqual(crushed["clearance_mm"], -20.0, places=6)
        # ...the same given as moved positions
        pos = occupant_clearance(d, positions={"A": [-140, -400, top], "B": [-140, 400, top]})
        self.assertAlmostEqual(pos["clearance_mm"], -20.0, places=6)
        # a bent shape between the nodes: its middle dips 120 mm into the space
        bent = occupant_clearance(d, curves={"rollbar_top": [[-300, -400, top + 50], [0, 0, top - 70], [-300, 400, top + 50]]})
        self.assertAlmostEqual(bent["clearance_mm"], -70.0 - 20.0, places=6)

    def test_demo_roll_bar_before_it_is_crushed(self):
        r = occupant_clearance(RB)
        n = xyz(RB)
        o = box(RB, "occupant_L")
        r_post = RB["sections"][next(m["section"] for m in RB["members"] if m["id"] == "rollbar_post_L")]["dims_mm"]["d"] / 2
        dx = o[0][0] - n["LHT"][0]  # the post behind the occupant's box
        dy = n["LHT"][1] - (o[0][1] + o[1][1]) / 2 - AIST["shoulder_breadth_m95"] / 2  # outside the shoulders
        self.assertAlmostEqual(r["clearance_mm"], math.hypot(dx, dy) - r_post, places=6)
        self.assertGreater(r["clearance_mm"], 0.0)
        self.assertIn(r["member"], ("rollbar_post_L", "rollbar_post_R"))

    def test_errors_name_what_is_missing(self):
        d = self.design([0, 0, 0], [1, 0, 0])
        with self.assertRaisesRegex(ValueError, "node 'X'"):
            occupant_clearance(d, displacements={"X": [0, 0, 1]})
        d["parts"] = [p for p in d["parts"] if p["kind"] != "occupant"]
        with self.assertRaisesRegex(ValueError, "no occupant"):
            occupant_clearance(d)


E = 205000.0
I = 6.0e4
H, W = 1400.0, 1200.0
MP = 8.0e5


def side(**kw):
    v = dict(E=q(E, "MPa"), h=q(H, "mm"), w=q(W, "mm"), I_c=q(I, "mm^4"), I_b=q(I, "mm^4"), theta_f=q(0, "rad"),
             M_pf=q(MP, "N*mm"), M_pt=q(MP, "N*mm"), E_in=q(875, "J"))
    v.update(kw)
    return out("frame_static.portal_frame_side", **v)


class PortalFormulaTest(unittest.TestCase):
    """Known answers of portal frames (textbook limits) and the plastic collapse loads."""

    def test_side_stiffness_limits(self):
        a = E * I / H**3
        self.assertAlmostEqual(side(I_b=q(I * 1e9, "mm^4"))["k"] / (24 * a), 1.0, places=6)  # two fixed-guided posts
        self.assertAlmostEqual(side(I_b=q(I * 1e-9, "mm^4"))["k"] / (6 * a), 1.0, places=6)  # two cantilevers
        # equal posts and beam, h = w: (12 a / h^2)(a + 6b)/(2a + 3b) with a = b -> 16.8 E I / h^3
        self.assertAlmostEqual(side(w=q(H, "mm"))["k"] / (16.8 * a), 1.0, places=9)
        # pinned feet (a very soft foot spring), rigid beam: two cantilevers, 6 E I / h^3
        self.assertAlmostEqual(side(theta_f=q(1e9, "rad"), I_b=q(I * 1e9, "mm^4"))["k"] / (6 * a), 1.0, places=5)
        # a foot spring in series with a cantilever (beam with no stiffness): 2 / (h^3/(3EI) + h^2 c_f)
        c = 2e-7
        k = side(theta_f=q(c * 1e6, "rad"), I_b=q(I * 1e-9, "mm^4"))["k"]
        self.assertAlmostEqual(k / (2 / (H**3 / (3 * E * I) + H**2 * c)), 1.0, places=6)

    def test_side_collapse_and_energy(self):
        r = side()
        self.assertAlmostEqual(r["H_c"], 4 * MP / H)  # sway mechanism, four hinges
        self.assertEqual(r["n_hinges"], 4.0)
        self.assertEqual(side(M_pf=q(0, "N*mm"))["n_hinges"], 2.0)
        self.assertLess(r["H_first"], r["H_c"])
        dy = r["H_c"] / r["k"]
        self.assertAlmostEqual(r["delta_E"], dy + (875e3 - r["H_c"] * dy / 2) / r["H_c"])
        small = side(E_in=q(1, "J"))  # within the elastic part: E = k d^2 / 2
        self.assertAlmostEqual(small["delta_E"], math.sqrt(2 * 1e3 / small["k"]))
        self.assertEqual(small["plastic"], 0.0)

    def test_rear_lever_and_cantilevers(self):
        base = dict(E=q(E, "MPa"), h=q(H, "mm"), I_c=q(I, "mm^4"), theta_f=q(0, "rad"), M_pf=q(MP, "N*mm"),
                    E_in=q(700, "J"))
        mid = out("frame_static.portal_frame_rear", f=q(0.5, "1"), **base)
        self.assertAlmostEqual(mid["k"], 2 * 3 * E * I / H**3)  # at mid-span: two cantilevers side by side
        self.assertAlmostEqual(mid["P_c"], 2 * MP / H)
        r = out("frame_static.portal_frame_rear", f=q(1 / 6, "1"), **base)
        self.assertAlmostEqual(r["k"], 3 * E * I / H**3 / ((5 / 6) ** 2 + (1 / 6) ** 2))
        self.assertAlmostEqual(r["P_c"], MP / (5 / 6 * H))
        self.assertEqual(r["n_hinges"], 1.0)

    def test_top_beam_limits(self):
        base = dict(E=q(E, "MPa"), h=q(H, "mm"), w=q(W, "mm"), I_b=q(I, "mm^4"), I_min=q(I, "mm^4"), theta_f=q(0, "rad"),
                    M_pb=q(MP, "N*mm"), M_pt=q(MP, "N*mm"), f_y=q(215, "MPa"), F=q(1000, "N"))
        stiff = out("frame_static.portal_frame_top", I_c=q(I * 1e9, "mm^4"), A_c=q(1e12, "mm^2"), **base)
        self.assertAlmostEqual(stiff["delta"] / (1000 * W**3 / (192 * E * I)), 1.0, places=6)  # fixed-fixed beam
        self.assertAlmostEqual(stiff["M_end"] / (1000 * W / 8), 1.0, places=6)
        soft = out("frame_static.portal_frame_top", I_c=q(I * 1e-9, "mm^4"), A_c=q(1e12, "mm^2"), **base)
        self.assertAlmostEqual(soft["delta"] / (1000 * W**3 / (48 * E * I)), 1.0, places=6)  # simply supported
        self.assertAlmostEqual(stiff["F_c"], 8 * MP / W)  # three hinges
        self.assertEqual(stiff["n_hinges"], 3.0)
        posts = out("frame_static.portal_frame_top", I_c=q(I * 1e9, "mm^4"), A_c=q(300, "mm^2"), **base)
        self.assertAlmostEqual(posts["delta_posts"], 500 * H / (E * 300))
        self.assertAlmostEqual(posts["F_squash"], 2 * 300 * 215)

    def test_plastic_modulus(self):
        self.assertAlmostEqual(plastic_modulus({"shape": "round_tube", "dims_mm": {"d": 40, "t": 2}})[0],
                               (40**3 - 36**3) / 6)
        zz, zy = plastic_modulus({"shape": "rect_tube", "dims_mm": {"b": 40, "h": 80, "t": 2}})
        self.assertAlmostEqual(zz, (40 * 80**2 - 36 * 76**2) / 4)
        self.assertAlmostEqual(zy, (80 * 40**2 - 76 * 36**2) / 4)


class PortalInputsTest(unittest.TestCase):
    def test_from_the_demo_roll_bar(self):
        pi = portal_frame_inputs(RB)
        n = xyz(RB)
        self.assertAlmostEqual(pi["side"]["h"]["value"], n["LHT"][2] - n["LHO"][2])
        self.assertAlmostEqual(pi["side"]["w"]["value"], 2 * n["LHT"][1])
        d, t = 42.7, 2.3
        self.assertAlmostEqual(pi["side"]["M_pf"]["value"], (d**3 - (d - 2 * t) ** 3) / 6 * 215.0)
        foot = pi["foot"]
        self.assertEqual(foot["rail_members"], ["rail_L_S2H", "rail_L_HB"])
        self.assertAlmostEqual(pi["side"]["theta_f"]["value"],
                               foot["theta_outrigger_side_per_kNm"] + foot["theta_rail_side_per_kNm"])
        for name, extra in (("side", {"E_in": q(875, "J")}), ("rear", {"E_in": q(700, "J"), "f": q(1 / 6, "1")}),
                            ("top", {"F": q(10000, "N")})):
            FORMULAS.evaluate(f"frame_static.portal_frame_{name}", {**pi[name], **extra})

    def test_posts_on_the_rails(self):
        narrow = designgen.regenerate(RB, {"rollbar_width": q(700, "mm")})
        pi = portal_frame_inputs(narrow)
        self.assertNotIn("outrigger", pi["foot"])
        self.assertAlmostEqual(pi["side"]["theta_f"]["value"], pi["foot"]["theta_rail_side_per_kNm"])

    def test_no_roll_bar(self):
        with self.assertRaisesRegex(ValueError, "no roll bar"):
            portal_frame_inputs(V1)


class RollbarFrameModelsTest(unittest.TestCase):
    def test_beam_model_runs(self):
        t = {t.name: t for t in solvers.builtin_tools()}["frame_static"]
        lc = {"load_case_id": "R1-rest", "test_id": "R1", "domain": "strength", "title": "R1", "params": {},
              "tolerance_rel": 0.15}
        rec = t.run(RB, lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        per = next(f for f in rec["fields"] if f["kind"] == "stress")["per_member"]
        self.assertTrue(ROLLBAR_MEMBERS <= set(per))

    def test_shell_mesh_joints(self):
        """Round tubes: the post and top beam joints are tied (not shared); square tubes make L joints."""
        from hiraku.solvers.k1_shell import build_mesh

        kinds = build_mesh(RB).joint_kinds
        self.assertEqual((kinds["LH"], kinds["LHO"], kinds["LHT"]), ("T", "tied", "tied"))
        sq = build_mesh(designgen.regenerate(RB, {"rollbar_square": q(1, "1")})).joint_kinds
        self.assertEqual((sq["LH"], sq["LHO"], sq["LHT"]), ("T", "L", "L"))

    @unittest.skipUnless(SLOW, "set HIRAKU_SLOW_TESTS=1 for the shell solve with the roll bar (about 13 s)")
    def test_shell_runs(self):
        t = {t.name: t for t in solvers.builtin_tools()}["frame_shell"]
        lc = {"load_case_id": "R1-rest", "test_id": "R1", "domain": "strength", "title": "R1", "params": {},
              "tolerance_rel": 0.15}
        rec = t.run(RB, lc, [{"prediction_id": "pred-0001"}], {"mesh_convergence": "off"})
        self.assertEqual(rec["status"], "ok", rec.get("error"))


if __name__ == "__main__":
    unittest.main()
