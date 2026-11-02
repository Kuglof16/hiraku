"""K1 frame_static against beam closed forms, through the AnalysisTool.run() contract.

The design is a ladder in the contract's own shape: two rails on four wheels,
tied by one cross member at mid-span, with a part hung at the middle of each
rail. Under its weight each rail is a simply supported beam with a centre load:
M = P L / 4, sigma = M c / I (qa.known_answers). The cross member sits where
the rails have zero slope, so by symmetry it carries no moment.

Assumptions about K1 that the test relies on (adjust here, not in K1, if K1
models them differently and says so):
- a wheel's frame_node is held in translation but free to rotate (the wheel
  centre is straight below the node, so its reaction puts no torque on the rail);
- a part's weight is split over its carried_by nodes;
- the load case R1 with no params (no step) carries only the static weight.
Rail self-weight is made negligible with a tiny density.
"""

import copy
import json
import math
import unittest

from hiraku import contract
from qa import known_answers as ka

from ._solvers import tool_or_skip

L_RAIL = 1800.0
TRACK = 800.0
MASS = 100.0  # kg hung at the middle of each rail
SECTION = {"b": 40.0, "h": 80.0, "t": 2.0}


def ladder_design():
    sec = ka.rect_tube(**SECTION)
    nodes = {
        "L0": [0.0, TRACK / 2, 300.0], "L1": [-L_RAIL / 2, TRACK / 2, 300.0], "L2": [-L_RAIL, TRACK / 2, 300.0],
        "R0": [0.0, -TRACK / 2, 300.0], "R1": [-L_RAIL / 2, -TRACK / 2, 300.0], "R2": [-L_RAIL, -TRACK / 2, 300.0],
    }
    # wheel centre straight below its frame node: a sideways offset would twist the rail
    wheel = lambda wid, node, x, y: {
        "id": wid, "kind": "wheel", "position_mm": [x, y, 250.0], "radius_mm": 250.0,
        "suspension": {"type": "rigid"}, "actuation": {"drive": False, "brake": False, "steer": False},
        "frame_node": node,
    }
    return {
        "design_version": 1, "parent_version": None, "name": "qa ladder", "change_reason": "qa check",
        "variables": {"wheelbase": {"value": L_RAIL, "unit": "mm"}, "track": {"value": TRACK, "unit": "mm"}},
        "supports": [wheel("FL", "L0", 0.0, TRACK / 2), wheel("FR", "R0", 0.0, -TRACK / 2),
                     wheel("RL", "L2", -L_RAIL, TRACK / 2), wheel("RR", "R2", -L_RAIL, -TRACK / 2)],
        "parts": [
            {"id": f"mass_{s}", "name": f"mass {s}", "kind": "battery",  # not occupant/cargo: on board in every loading
             "box": {"center_mm": [-L_RAIL / 2, y, 400.0], "size_mm": [100.0, 100.0, 100.0]},
             "mass_kg": MASS, "carried_by": [f"{s}1"]}
            for s, y in (("L", TRACK / 2), ("R", -TRACK / 2))
        ],
        "nodes": [{"id": k, "xyz_mm": v} for k, v in nodes.items()],
        "members": [
            {"id": "rail_L0", "node_a": "L0", "node_b": "L1", "section": "rt", "material": "m"},
            {"id": "rail_L1", "node_a": "L1", "node_b": "L2", "section": "rt", "material": "m"},
            {"id": "rail_R0", "node_a": "R0", "node_b": "R1", "section": "rt", "material": "m"},
            {"id": "rail_R1", "node_a": "R1", "node_b": "R2", "section": "rt", "material": "m"},
            {"id": "cross", "node_a": "L1", "node_b": "R1", "section": "rt", "material": "m"},
        ],
        "sections": {"rt": {"shape": "rect_tube", "dims_mm": dict(SECTION), "A_mm2": sec["A"],
                            "Iy_mm4": sec["Iy"], "Iz_mm4": sec["Iz"], "source": "qa.known_answers.rect_tube"}},
        "materials": {"m": {"name": "qa check material", "E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 1e-6,
                            "yield_MPa": 235.0, "source": "check input, not a material claim", "verified": False}},
    }


def static_load_case():
    """The contract's example load case without its params: the weight alone."""
    lc = json.loads((contract.EXAMPLES_DIR / "load_case.json").read_text(encoding="utf-8"))
    lc["load_case_id"] = "R1-static"
    lc["params"] = {}
    return lc


def prediction_for(lc):
    p = json.loads((contract.EXAMPLES_DIR / "prediction.json").read_text(encoding="utf-8"))
    p["load_case_id"] = lc["load_case_id"]
    return p


class CheckInputs(unittest.TestCase):
    def test_design_and_load_case_fit_the_contract(self):
        self.assertEqual(contract.errors("design", ladder_design()), [])
        self.assertEqual(contract.errors("load_case", static_load_case()), [])


class FrameStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = tool_or_skip("frame_static")

    def run_tool(self, design):
        lc = static_load_case()
        out = self.tool.run(design, lc, [prediction_for(lc)], {})
        self.assertEqual(contract.errors("analysis", dict(out, analysis_id="ana-0001")), [])
        self.assertEqual(out["status"], "ok", out.get("error"))
        return out

    def test_rail_stress_is_the_simply_supported_beam(self):
        out = self.run_tool(ladder_design())
        sec = ka.rect_tube(**SECTION)
        P = MASS * ka.G_N
        M = ka.simply_supported_centre_load(P, L_RAIL, 205000.0, sec["Iy"])["moment"]
        sigma = ka.bending_stress(M, sec["Iy"], sec["c_z"])
        got = {r["quantity"]: r for r in out["results"]}
        self.assertIn("max_stress", got)
        self.assertEqual(got["max_stress"]["unit"], "MPa")
        # pure bending at the corners; if K1 also counted the mean shear V/A in von Mises,
        # it would add sqrt(1 + 3 (V/A)^2 / sigma^2) - 1 = 0.08 % here
        self.assertAlmostEqual(got["max_stress"]["value"] / sigma, 1.0, delta=1e-3)

    def test_reactions_balance_the_weight_and_are_symmetric(self):
        out = self.run_tool(ladder_design())
        reactions = {v["at"]: v["vector_N"] for v in out.get("vectors", []) if v["kind"] == "reaction"}
        if not reactions:
            self.skipTest("frame_static reports no reaction vectors")
        total = 2 * MASS * ka.G_N
        self.assertAlmostEqual(sum(v[2] for v in reactions.values()) / total, 1.0, places=6)
        for a, b in (("FL", "FR"), ("RL", "RR")):
            if a in reactions and b in reactions:
                self.assertAlmostEqual(reactions[a][2], reactions[b][2], places=6)

    def test_mirror_design_gives_the_same_stresses(self):
        d = ladder_design()
        m = copy.deepcopy(d)
        for n in m["nodes"]:
            n["xyz_mm"][1] = -n["xyz_mm"][1]
        for s in m["supports"]:
            s["position_mm"][1] = -s["position_mm"][1]
        for p in m["parts"]:
            p["box"]["center_mm"][1] = -p["box"]["center_mm"][1]
        a, b = self.run_tool(d), self.run_tool(m)
        sa = next(r["value"] for r in a["results"] if r["quantity"] == "max_stress")
        sb = next(r["value"] for r in b["results"] if r["quantity"] == "max_stress")
        self.assertTrue(math.isclose(sa, sb, rel_tol=1e-9), f"{sa} vs mirrored {sb}")
