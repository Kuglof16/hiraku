"""R4: frame_static.rops_occupant_clearance, the gaps it takes (rops_inputs) against k4_geometry.occupant_clearance,
and the R4 prediction list (portal formulas, then the clearance)."""

import json
import math
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.catalog import load_catalog
from hiraku.catalog.builders import BUILDERS, PLANS
from hiraku.catalog.builders.analysis import R4_PREDICTIONS, r4_inputs
from hiraku.contract.loading import on_board
from hiraku.solvers.k0_handcalc.frame_inputs import portal_frame_inputs
from hiraku.solvers.k0_handcalc.rops_inputs import displacements, motions, rops_gaps, rops_inputs
from hiraku.solvers.k0_handcalc.rops_inputs import _rollbar  # the geometry the gaps are measured on
from hiraku.solvers.k0_handcalc.vehicle import DesignError
from hiraku.solvers.k4_geometry import occupant_clearance, occupant_spaces
from tests.solvers.test_builders import requirements

ROOT = Path(__file__).resolve().parents[2]
RB = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))  # roll bar on by default
FORMULAS = solvers.formula_registry()
FID = "frame_static.rops_occupant_clearance"
E, I, H, W, D = 205000.0, 6.0e4, 1400.0, 1200.0, 40.0  # MPa, mm^4, post length, span, tube diameter
R = D / 2.0


def q(v, u):
    return {"value": float(v), "unit": u}


def out(formula, **inputs):
    return {k: v["value"] for k, v in FORMULAS.evaluate(formula, inputs)["outputs"].items()}


def clearance(g, d):
    return out(FID, **{f"g_{k}": q(g[k], "mm") for k in g}, **{f"d_{k}": q(d[k], "mm") for k in d})


def portal(box_lo, box_hi):
    """An upright portal (feet fixed at z = 0, corners LT/RT at z = H, y = +-W/2, x = 0) of round tubes and one
    occupant box with no seat, larger than the AIST sizes, so the space is the box itself."""
    nodes = {"LF": [0, W / 2, 0], "LT": [0, W / 2, H], "RF": [0, -W / 2, 0], "RT": [0, -W / 2, H]}
    members = [("rollbar_post_L", "LF", "LT"), ("rollbar_post_R", "RF", "RT"), ("rollbar_top", "LT", "RT")]
    c = [(a + b) / 2 for a, b in zip(box_lo, box_hi)]
    s = [b - a for a, b in zip(box_lo, box_hi)]
    return {"nodes": [{"id": k, "xyz_mm": v} for k, v in nodes.items()],
            "members": [{"id": m, "node_a": a, "node_b": b, "section": "ct", "material": "s", "part": "rollbar"}
                        for m, a, b in members],
            "sections": {"ct": {"shape": "round_tube", "dims_mm": {"d": D, "t": 2.0}}},
            "materials": {"s": {"E_MPa": E, "nu": 0.3, "density_kg_m3": 7850, "yield_MPa": 235}},
            "parts": [{"id": "occ", "kind": "occupant", "box": {"center_mm": c, "size_mm": s}, "mass_kg": 70}]}


def moved(design, direction, delta, rear_fraction=1 / 6, name=None):
    """k4_geometry.occupant_clearance with the corners moved as rops_inputs moves them for that direction."""
    geo = _rollbar(design)
    m = next(m for m in motions(geo["corners"], rear_fraction)
             if m["direction"] == direction and (name is None or m["name"] == name))
    return occupant_clearance(design, displacements=displacements(geo, m, delta))["clearance_mm"]


class FormulaTest(unittest.TestCase):
    def test_contract_and_ledger(self):
        f = {x["formula_id"]: x for x in FORMULAS.list_formulas()}[FID]
        contract.validate("formula", f)
        self.assertEqual(set(f["inputs"]), {"g_side", "g_rear", "g_top", "d_side", "d_rear", "d_top"})
        self.assertTrue(all(v["unit"] == "mm" for v in f["inputs"].values()))
        self.assertEqual(set(f["outputs"]), {"rops_occupant_clearance", "c_side", "c_rear", "c_top", "governing"})

    def test_smallest_direction_is_the_primary_output(self):
        for g, d, worst, gov in (({"side": 100, "rear": 300, "top": 200}, {"side": 10, "rear": 20, "top": 30}, 90, 1),
                                 ({"side": 100, "rear": 300, "top": 200}, {"side": 10, "rear": 290, "top": 30}, 10, 2),
                                 ({"side": 100, "rear": 300, "top": 50}, {"side": 10, "rear": 20, "top": 80}, -30, 3)):
            r = clearance(g, d)
            self.assertAlmostEqual(r["rops_occupant_clearance"], worst)
            self.assertEqual(r["governing"], gov)
            for k in g:
                self.assertAlmostEqual(r[f"c_{k}"], g[k] - d[k])
        ev = FORMULAS.evaluate(FID, {**{f"g_{k}": q(100, "mm") for k in ("side", "rear", "top")},
                                     **{f"d_{k}": q(0.01, "m") for k in ("side", "rear", "top")}})
        self.assertAlmostEqual(ev["outputs"]["rops_occupant_clearance"]["value"], 90.0)  # units converted
        with self.assertRaisesRegex(ValueError, "below the range"):
            clearance({"side": 1, "rear": 1, "top": 1}, {"side": -1, "rear": 0, "top": 0})


class KnownAnswerTest(unittest.TestCase):
    """A box space whose gap is known and a portal whose deflection is known: the formula against
    k4_geometry.occupant_clearance at the moved nodes."""

    def test_crush_fixed_fixed_beam_over_the_space(self):
        gap = 150.0  # the beam's surface over the box's top face
        d = portal([-300, -400, 0], [300, 400, H - R - gap])
        g = rops_gaps(d)
        self.assertAlmostEqual(g["g_top"]["value"], gap, places=5)
        # stiff posts: the top beam fixed at both ends, delta = F w^3 / (192 E I)
        top = out("frame_static.portal_frame_top", E=q(E, "MPa"), h=q(H, "mm"), w=q(W, "mm"), I_c=q(I * 1e9, "mm^4"),
                  I_b=q(I, "mm^4"), I_min=q(I, "mm^4"), A_c=q(1e12, "mm^2"), theta_f=q(0, "rad"), M_pb=q(8e5, "N*mm"),
                  M_pt=q(8e5, "N*mm"), f_y=q(235, "MPa"), F=q(3000, "N"))
        self.assertAlmostEqual(top["delta"] / (3000 * W**3 / (192 * E * I)), 1.0, places=6)
        c = clearance({"side": g["g_side"]["value"], "rear": g["g_rear"]["value"], "top": gap},
                      {"side": 0, "rear": 0, "top": top["delta"]})
        self.assertAlmostEqual(c["c_top"], moved(d, "top", top["delta"]), places=5)
        self.assertAlmostEqual(c["c_top"], gap - top["delta"], places=5)

    def test_side_sway_rigid_beam_beside_the_space(self):
        gap = 120.0  # the left post's surface to the box's face across the vehicle
        y0 = W / 2 + R + gap
        d = portal([-300, y0, 0], [300, y0 + 600, 2 * H])
        g = rops_gaps(d)
        self.assertAlmostEqual(g["g_side"]["value"], gap, places=5)
        self.assertEqual([m["contact"] for m in g["motions"] if m["direction"] == "side"], [True, False])  # +y, -y
        # rigid beam, fixed feet: two fixed-guided posts, k = 24 E I / h^3; small energy: elastic
        s = out("frame_static.portal_frame_side", E=q(E, "MPa"), h=q(H, "mm"), w=q(W, "mm"), I_c=q(I, "mm^4"),
                I_b=q(I * 1e9, "mm^4"), theta_f=q(0, "rad"), M_pf=q(8e5, "N*mm"), M_pt=q(8e5, "N*mm"), E_in=q(20, "J"))
        self.assertAlmostEqual(s["k"] / (24 * E * I / H**3), 1.0, places=6)
        self.assertEqual(s["plastic"], 0.0)
        self.assertAlmostEqual(s["delta_E"], math.sqrt(2 * 20e3 / s["k"]))
        c = clearance({"side": gap, "rear": g["g_rear"]["value"], "top": g["g_top"]["value"]},
                      {"side": s["delta_E"], "rear": 0, "top": 0})
        self.assertAlmostEqual(c["c_side"], moved(d, "side", s["delta_E"], name="side +y"), places=5)

    def test_rear_two_cantilevers_ahead_of_the_space(self):
        gap = 90.0  # the top beam's surface to the box's rear face
        x0 = R + gap
        d = portal([x0, -900, 0], [x0 + 500, 900, 2 * H])
        g = rops_gaps(d, rear_fraction=0.5)
        self.assertAlmostEqual(g["g_rear"]["value"], gap, places=5)
        # load at mid-span: the two posts as cantilevers side by side, k = 2 * 3 E I / h^3
        r = out("frame_static.portal_frame_rear", E=q(E, "MPa"), h=q(H, "mm"), I_c=q(I, "mm^4"), theta_f=q(0, "rad"),
                M_pf=q(8e5, "N*mm"), f=q(0.5, "1"), E_in=q(5, "J"))
        self.assertAlmostEqual(r["k"], 6 * E * I / H**3)
        c = clearance({"side": g["g_side"]["value"], "rear": gap, "top": g["g_top"]["value"]},
                      {"side": 0, "rear": r["delta_E"], "top": 0})
        self.assertAlmostEqual(c["c_rear"], moved(d, "rear", r["delta_E"], rear_fraction=0.5,
                                                  name="rear +x, near corner L"), places=5)

    def test_rear_off_centre_reaches_zero_with_k4(self):
        """f = 1/6: the near corner travels (1 - f)/((1 - f)^2 + f^2) times the load point, so the formula is in the
        load point's travel; it reaches 0 where k4 does, and the sign agrees either side."""
        f = 1 / 6
        near = (1 - f) / ((1 - f) ** 2 + f**2)
        gap = 90.0
        d = portal([R + gap, -900, 0], [R + gap + 500, 900, 2 * H])
        g = rops_gaps(d, rear_fraction=f)["g_rear"]["value"]
        self.assertAlmostEqual(g, gap / near, places=5)
        k4 = lambda delta: moved(d, "rear", delta, rear_fraction=f, name="rear +x, near corner L")  # noqa: E731
        self.assertAlmostEqual(k4(g), 0.0, places=5)
        for delta in (0.5 * g, 0.99 * g, 1.01 * g, 1.3 * g):
            self.assertEqual(k4(delta) > 0, g - delta > 0)
            self.assertAlmostEqual(k4(delta), near * (g - delta), places=5)  # the near corner closes straight on

    def test_turning_posts_drop_and_pass_beside(self):
        """Side and rear turn the posts about their feet: the corners drop h - sqrt(h^2 - u^2). A space under the
        top beam's line is reached only by the drop; a space beside the bar's path is never reached."""
        gap_z = 60.0
        d = portal([-300, -530, 0], [900, 530, H - R - gap_z])  # under the beam, between the posts, long along x
        geo = _rollbar(d)
        m = next(m for m in motions(geo["corners"], 0.5) if m["name"] == "rear +x, near corner L")
        u = displacements(geo, m, 300.0)["LT"]
        self.assertAlmostEqual(u[2], -(H - math.sqrt(H**2 - 300.0**2)))
        g = rops_gaps(d, rear_fraction=0.5)
        rear = [x for x in g["motions"] if x["name"] == "rear +x, near corner L"][0]
        self.assertTrue(rear["contact"])
        self.assertAlmostEqual(H - math.sqrt(H**2 - rear["gap_mm"] ** 2), gap_z, places=4)  # touches when it has dropped gap_z
        self.assertAlmostEqual(moved(d, "rear", rear["gap_mm"], rear_fraction=0.5, name=rear["name"]), 0.0, places=4)
        far = portal([200, -900, 0], [600, 900, 2 * H])  # ahead of the bar, tall: the crush passes behind it
        top = [x for x in rops_gaps(far)["motions"] if x["direction"] == "top"][0]
        self.assertFalse(top["contact"])
        self.assertAlmostEqual(top["gap_mm"], H)  # the end of the travel: the posts' length

    def test_already_inside_and_errors(self):
        d = portal([-300, -400, 0], [300, 400, H + 10])  # the top beam is in the space as built
        g = rops_gaps(d)
        self.assertAlmostEqual(g["g_top"]["value"], occupant_clearance(d)["clearance_mm"])
        self.assertLess(g["g_top"]["value"], 0.0)
        tilted = portal([-300, -400, 0], [300, 400, 500])
        tilted["nodes"][1]["xyz_mm"] = [100, W / 2, H]
        with self.assertRaisesRegex(DesignError, "upright"):
            rops_gaps(tilted)
        with self.assertRaisesRegex(DesignError, "no roll bar"):
            rops_gaps({**d, "members": d["members"][:2]})
        with self.assertRaisesRegex(DesignError, "rear_fraction"):
            rops_gaps(d, rear_fraction=0.7)


class DemoRollbarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lc = BUILDERS["R4"](load_catalog().test("R4"), requirements(), RB)
        cls.on = on_board(RB, cls.lc["params"]["loading"])
        cls.r = rops_inputs(RB, loading=cls.lc["params"]["loading"])

    def test_same_space_and_same_clearance_as_k4(self):
        g = self.r["gaps"]
        self.assertEqual(g["spaces"], occupant_spaces(self.on))
        self.assertAlmostEqual(g["as_built_clearance_mm"], occupant_clearance(self.on)["clearance_mm"], places=9)
        geo = _rollbar(self.on)
        for m in g["motions"]:
            if m["contact"]:  # at the gap the moved bar just touches, by k4
                at = displacements(geo, m, m["gap_mm"])
                self.assertAlmostEqual(occupant_clearance(self.on, displacements=at)["clearance_mm"], 0.0, places=4)
                before = displacements(geo, m, m["gap_mm"] - 1.0)
                self.assertGreater(occupant_clearance(self.on, displacements=before)["clearance_mm"], 0.0)
        self.assertTrue(any(m["contact"] for m in g["motions"]))
        for d in ("side", "rear", "top"):
            self.assertEqual(g[f"g_{d}"]["value"], min(m["gap_mm"] for m in g["motions"] if m["direction"] == d))

    def test_portal_inputs_are_frame_inputs(self):
        pi = portal_frame_inputs(RB)
        for d in ("side", "rear", "top"):
            for k, v in pi[d].items():
                self.assertEqual(self.r["inputs"][f"{d}_{k}"], v)

    def test_prediction_list_resolves_in_order(self):
        """R4_PREDICTIONS as the scripted run resolves them: from.<name> off r4_inputs, lc.<param>, and pred.<quantity>
        of the predictions before it; the last one is the criterion's quantity."""
        self.assertNotIn("R4", PLANS)  # still no plan: the tool is made in the run
        self.assertEqual(R4_PREDICTIONS[-1]["quantity"], self.lc["criterion"]["quantity"])
        given = r4_inputs(RB, self.lc)
        preds = {}
        for p in R4_PREDICTIONS:
            def ref(r):
                head, _, rest = r.partition(".")
                return {"from": given, "lc": self.lc["params"], "pred": preds}[head][rest]
            ev = FORMULAS.evaluate(p["formula_id"], {k: ref(r) for k, r in p["inputs"].items()})
            preds[p["quantity"]] = ev["outputs"][p["output"]]
            self.assertEqual(set(p["inputs"]), set(FORMULAS.get(p["formula_id"]).inputs))
        c = preds["rops_occupant_clearance"]["value"]
        expect = min(given[f"g_{d}"]["value"] - preds[f"rops_{d}_deflection"]["value"] for d in ("side", "rear", "top"))
        self.assertAlmostEqual(c, expect)


if __name__ == "__main__":
    unittest.main()
