"""analysis.hot_spots of the frame tools (frame_static, frame_shell, frame_tubes; shelf.hot_spots)."""

import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from hiraku import contract
from hiraku.catalog import load_catalog, load_requirements
from hiraku.catalog.build import BUILDERS
from hiraku.designgen import space_frame_utv as sf
from hiraku.solvers.shelf import hot_spots
from hiraku.solvers.shelf.frame_shell import FrameShell
from hiraku.solvers.shelf.frame_static import FrameStatic
from hiraku.solvers.shelf.frame_tubes import FrameTubes

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
REQ = load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml")
CATALOG = load_catalog()
SHELL = {"element_size": {"value": 20.0, "unit": "mm"}}  # the coarse shell of test_frame_shell
# test_frame_tubes' QUICK: its coarse mesh with 8 facets per round tube (the rows' shape, not judged values)
TUBES = {"element_size": {"value": 40.0, "unit": "mm"}, "hot_spot_facets": {"value": 8, "unit": "1"}}


def load_case(build: str, test_id: str, design) -> dict:
    return BUILDERS[build](CATALOG.test(test_id), REQ, design)


def pred(quantity=None) -> list:
    return [dict({"prediction_id": "pred-0001"}, **({"quantity": quantity} if quantity else {}))]


def value(rec, quantity):
    return next(r["value"] for r in rec["results"] if r["quantity"] == quantity)


class Shape:
    """Checks every tool's hot_spots has to pass."""

    def check_shape(self, rec):
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        hot = rec["hot_spots"]
        self.assertTrue(0 < len(hot) <= hot_spots.TOP_N)
        u = [h["utilisation"] for h in hot]
        self.assertEqual(u, sorted(u, reverse=True))
        for h in hot:
            self.assertAlmostEqual(h["utilisation"], h["value"]["value"] / h["limit"]["value"], places=12)
        return hot


class HelperTest(unittest.TestCase):
    LC = {"criterion": {"quantity": "max_stress", "op": "<=", "limit": {"value": 200.0, "unit": "MPa"}}}

    def test_judged_quantity_is_the_judges(self):
        from hiraku.tools.judge import judged_quantity

        for tool in (FrameShell(), FrameStatic(), FrameTubes()):
            ctx = SimpleNamespace(specs={tool.name: tool.describe()})
            for q in ("max_stress", "max_hot_spot_stress"):
                self.assertEqual(hot_spots.judged_quantity(tool, q), judged_quantity(ctx, tool.name, q), (tool.name, q))

    def test_judged_quantity_and_limit(self):
        self.assertEqual(hot_spots.judged_limit(FrameShell(), self.LC), ("max_hot_spot_stress", 200.0))
        self.assertEqual(hot_spots.judged_limit(FrameStatic(), self.LC), ("max_stress", 200.0))

    def test_no_rows_without_a_upper_limit_on_a_stress(self):
        for crit in (None, {"quantity": "max_stress", "op": ">=", "limit": {"value": 1.0, "unit": "MPa"}},
                     {"quantity": "unsupported_load", "op": "<=", "limit": {"value": 0.0, "unit": "1"}}):
            self.assertIsNone(hot_spots.judged_limit(FrameStatic(), {"criterion": crit} if crit else {}))

    def test_top_sorts_cuts_and_keeps(self):
        rows = [hot_spots.row(f"m{i}", "q", float(i), 10.0) for i in range(15)]
        keep = hot_spots.row("sharp", "p", 0.5, 10.0, note=hot_spots.SHARP_NOTE)
        got = hot_spots.top(rows, keep=[keep])
        self.assertEqual(len(got), hot_spots.TOP_N)
        self.assertEqual([r["where"] for r in got[:3]], ["m14", "m13", "m12"])
        self.assertEqual(got[-1]["where"], "sharp")

    def test_mark_predicted(self):
        rows = [hot_spots.row("a", "q", 5.0, 10.0), hot_spots.row("b", "q", 7.0, 10.0),
                hot_spots.row("c", "p", 9.0, 10.0)]
        hot_spots.mark_predicted(rows, pred("q"))
        self.assertEqual([r["predicted"] for r in rows], [False, True, False])
        hot_spots.mark_predicted(rows, pred())
        self.assertFalse(any(r["predicted"] for r in rows))

    def test_utilisation_rule(self):
        """Contract 46306b5: value / limit, over 1 exactly when failing; 1000 failing / 0 passing where the ratio
        is undefined or outside 0 to 1000."""
        u = hot_spots.utilisation
        self.assertEqual((u(50.0, 100.0), u(100.0, 100.0), u(150.0, 100.0)), (0.5, 1.0, 1.5))
        self.assertEqual((u(1e9, 1.0), u(5.0, 0.0), u(0.0, 0.0), u(-1.0, 10.0)), (1000.0, 1000.0, 0.0, 0.0))
        self.assertEqual(hot_spots.row("a", "q", 1e9, 1.0)["utilisation"], 1000.0)
        self.assertEqual(hot_spots.row("a", "q", 1.0, 2.0, at_mm=[1, 2, 3])["at_mm"], [1.0, 2.0, 3.0])
        self.assertNotIn("at_mm", hot_spots.row("a", "q", 1.0, 2.0))


class FrameStaticTest(Shape, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lc = load_case("r1_static", "R1", DESIGN)
        cls.rec = FrameStatic().run(DESIGN, cls.lc, pred("max_stress"), {})
        cls.bare = FrameStatic().run(DESIGN, cls.lc, pred(), {})

    def test_rows(self):
        hot = self.check_shape(self.rec)
        limit = self.lc["criterion"]["limit"]["value"]
        self.assertTrue(all(h["quantity"] == "max_stress" and h["limit"]["value"] == limit for h in hot))
        top = next(r for r in self.rec["results"] if r["quantity"] == "max_stress")
        self.assertEqual((hot[0]["where"], hot[0]["value"]["value"]), (top["where"], top["value"]))
        per = {r["where"]: r["value"] for r in self.rec["results"] if r["quantity"].startswith("max_stress.")}
        self.assertTrue(all(per[h["where"]] == h["value"]["value"] for h in hot))

    def test_predicted(self):
        self.assertEqual([h["predicted"] for h in self.rec["hot_spots"]], [True] + [False] * (len(self.rec["hot_spots"]) - 1))
        self.assertFalse(any(h["predicted"] for h in self.bare["hot_spots"]))
        self.assertEqual(self.bare["results"], self.rec["results"])

    def test_no_rows_on_a_criterion_it_does_not_judge(self):
        lc = load_case("r1_joint", "R1s", DESIGN)  # max_hot_spot_stress: the beam has no per-place values of it
        self.assertNotIn("hot_spots", FrameStatic().run(DESIGN, lc, pred(), {}))


class FrameShellTest(Shape, unittest.TestCase):
    """The demo frame on the coarse shell: R1s, and R1 on the frame kept in this process."""

    @classmethod
    def setUpClass(cls):
        cls.lcs = {"R1s": load_case("r1_joint", "R1s", DESIGN), "R1": load_case("r1_static", "R1", DESIGN)}
        cls.recs = {k: FrameShell().run(DESIGN, lc, pred("max_hot_spot_stress"), SHELL) for k, lc in cls.lcs.items()}

    def test_rows(self):
        for k, rec in self.recs.items():
            with self.subTest(k):
                hot = self.check_shape(rec)
                weld = [h for h in hot if h["quantity"] == "max_hot_spot_stress"]
                sharp = [h for h in hot if h["quantity"] == "max_stress"]
                self.assertEqual(len(weld) + len(sharp), len(hot))
                self.assertEqual(weld[0]["value"]["value"], value(rec, "max_hot_spot_stress"))
                self.assertTrue(all(h["note"].startswith("joint ") for h in weld))
                self.assertEqual(len(sharp), 1)
                self.assertEqual(sharp[0]["note"], hot_spots.SHARP_NOTE)
                self.assertIn("尖り", sharp[0]["note"])  # the judge and check_run skip rows whose note says so
                self.assertTrue(all(len(h["at_mm"]) == 3 for h in hot))  # the weld-line point / the element centre
                self.assertEqual(sharp[0]["value"]["value"], value(rec, "max_stress"))
                self.assertEqual(hot[0]["value"]["value"], max(value(rec, "max_stress"), value(rec, "max_hot_spot_stress")))

    def test_the_sharp_point_is_not_the_judged_quantity(self):
        # the judge's red: predicted false, the judged quantity, utilisation > 1; the sharp row is never of it
        for k, lc in self.lcs.items():
            q = hot_spots.judged_quantity(FrameShell(), lc["criterion"]["quantity"])
            self.assertEqual(q, "max_hot_spot_stress")
            sharp = next(h for h in self.recs[k]["hot_spots"] if h.get("note") == hot_spots.SHARP_NOTE)
            self.assertNotEqual(sharp["quantity"], q)

    def test_predicted(self):
        hot = self.recs["R1s"]["hot_spots"]
        weld = [h for h in hot if h["quantity"] == "max_hot_spot_stress"]
        self.assertTrue(weld[0]["predicted"])
        self.assertFalse(any(h["predicted"] for h in hot if h is not weld[0]))
        again = copy.deepcopy(hot)
        hot_spots.mark_predicted(again, pred())
        self.assertFalse(any(h["predicted"] for h in again))

    def test_untied_joints_in_the_assumptions(self):
        said = [a for a in self.recs["R1s"]["assumptions"] if a.startswith("判定していない継ぎ手: ")]
        self.assertEqual(len(said), 1)
        self.assertIn("RB", said[0].split(" (")[0].split(": ")[1].split(", "))


class FrameTubesTest(Shape, unittest.TestCase):
    """The space frame on test_frame_tubes' coarse mesh: R1 (judged on max_stress: frame_tubes has no judged_as)
    and R1s (max_hot_spot_stress)."""

    @classmethod
    def setUpClass(cls):
        cls.d = sf.build()
        cls.lcs = {"R1": load_case("r1_static", "R1", cls.d), "R1s": load_case("r1_joint", "R1s", cls.d)}
        cls.recs = {k: FrameTubes().run(cls.d, lc, pred(lc["criterion"]["quantity"]), TUBES)
                    for k, lc in cls.lcs.items()}

    def test_r1_rows_per_member(self):
        rec = self.recs["R1"]
        hot = self.check_shape(rec)
        self.assertTrue(all(h["quantity"] == "max_stress" for h in hot))
        self.assertEqual(hot[0]["value"]["value"], value(rec, "max_stress"))
        self.assertEqual([h["predicted"] for h in hot], [True] + [False] * (len(hot) - 1))

    def test_r1s_rows_per_weld_line(self):
        rec = self.recs["R1s"]
        hot = self.check_shape(rec)
        weld = [h for h in hot if h["quantity"] == "max_hot_spot_stress"]
        self.assertEqual(weld[0]["value"]["value"], value(rec, "max_hot_spot_stress"))
        self.assertTrue(weld[0]["predicted"])
        self.assertFalse(any(h["predicted"] for h in hot if h is not weld[0]))
        sharp = [h for h in hot if h["quantity"] == "max_stress"]
        self.assertEqual([h["note"] for h in sharp], [hot_spots.SHARP_NOTE])
        self.assertEqual(sharp[0]["value"]["value"], value(rec, "max_stress"))


if __name__ == "__main__":
    unittest.main()
