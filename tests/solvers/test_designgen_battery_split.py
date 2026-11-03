"""ladder_4w's battery_split: the battery as one pack (v1) or two, the v1 pack cut in two across y; the packs'
mass, place, mounts and wires; rule U1-04 (a battery 30 kg or less, from the sample notes) passes when split."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract
from hiraku.designgen import DesignGenError
from hiraku.designgen import ladder_4w as GEN
from hiraku.designgen.kit import attach, systems
from hiraku.intake import rulecheck
from hiraku.intake import systems as checks
from hiraku.intake.knowledge import import_text

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
NOTES = (ROOT / "data" / "knowledge" / "samples" / "bending-notes.md").read_text(encoding="utf-8")
# the sample notes' rules as the video's script imports them (tests/intake/test_knowledge.SampleNotesTest)
U1 = import_text("bending-notes.md", NOTES, doc_id="U1", skip={"c3": "x", "c5": "y"}).record["rules"]
U104 = next(r for r in U1 if r["rule_id"] == "U1-04")


def _parts(d):
    return {p["id"]: p for p in d["parts"]}


def _packs(d):
    return [p for p in d["parts"] if p["kind"] == "battery"]


def _u104_hits(d):
    [check] = rulecheck.check_all([U104], d)
    return [h["part_id"] for h in check["hits"]]


def _moment(parts):
    m = sum(p["mass_kg"] for p in parts)
    return m, sum(np.asarray(p["box"]["center_mm"]) * p["mass_kg"] for p in parts) / m


class BatterySplitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.split = GEN.build({"battery_split": 2})
        cls.mounted = GEN.build({"battery_split": 2, **{k: 1 for k in GEN.MOUNT_GROUPS}})

    def test_variable(self):
        spec = {r["name"]: r for r in GEN.spec()}["battery_split"]
        self.assertTrue(spec["revisable"])
        self.assertEqual((spec["default"], spec["min"], spec["max"]), (1.0, 1.0, 2.0))
        self.assertNotIn("battery_split", V1["variables"])
        self.assertEqual(GEN.build({"battery_split": 1}), V1)
        self.assertEqual(self.split["variables"]["battery_split"], {"value": 2.0, "unit": "1"})
        with self.assertRaises(DesignGenError):
            GEN.build({"battery_split": 1.5})
        contract.validate("design", self.split)
        contract.validate("design", self.mounted)

    def test_two_packs_in_the_one_packs_place(self):
        [one] = _packs(V1)
        a, b = _packs(self.split)
        self.assertEqual((a["id"], b["id"]), GEN.BATTERY_IDS)
        for p in (a, b):
            self.assertEqual(p["mass_kg"], one["mass_kg"] / 2.0)
            self.assertEqual(p["box"]["size_mm"], [one["box"]["size_mm"][0], one["box"]["size_mm"][1] / 2.0,
                                                   one["box"]["size_mm"][2]])
            self.assertEqual([p["box"]["center_mm"][i] for i in (0, 2)], [one["box"]["center_mm"][i] for i in (0, 2)])
        # side by side across y, BATTERY_SPLIT_GAP_MM apart, symmetric about the one pack's centre
        self.assertAlmostEqual((b["box"]["center_mm"][1] - b["box"]["size_mm"][1] / 2.0)
                               - (a["box"]["center_mm"][1] + a["box"]["size_mm"][1] / 2.0), GEN.BATTERY_SPLIT_GAP_MM)
        self.assertAlmostEqual(a["box"]["center_mm"][1] + b["box"]["center_mm"][1], 0.0)
        self.assertEqual((a["carried_by"], b["carried_by"]), (["RS1", "RS2"], ["LS1", "LS2"]))
        m1, c1 = _moment(V1["parts"])
        m2, c2 = _moment(self.split["parts"])
        self.assertAlmostEqual(m1, m2, places=9)
        np.testing.assert_allclose(c1, c2, atol=1e-9)  # the same mass in the same place: the centre of mass stays

    def test_everything_else_stays(self):
        d, v1 = _parts(self.split), _parts(V1)
        self.assertEqual(sorted(set(d) - set(v1)), ["battery2"])
        for pid in v1:
            if pid != "battery":
                self.assertEqual(d[pid]["box"], v1[pid]["box"], pid)

    def test_wires_in_series_and_the_systems_checks(self):
        links = {p["id"]: {"connects_to": p.get("connects_to", [])} for p in self.split["parts"]}  # as build() wrote them
        power = {pid: [c["to"] for c in r["connects_to"] if c.get("carries") == "power"] for pid, r in links.items()}
        self.assertEqual(power["battery2"], ["battery"])
        self.assertEqual(power["battery"], ["main_switch"])
        self.assertIn("motor", systems.reach(links, "battery2", "power"))
        for d in (self.split, self.mounted):
            net = checks.Net(d)
            for name in ("power_chain", "disconnect"):
                _, hits, missing = checks.CHECKS[name](net)
                self.assertEqual((hits, missing), ([], []), name)
        _, hits, _ = checks.CHECKS["mounting"](checks.Net(self.mounted))
        self.assertEqual(hits, [])

    def test_each_pack_gets_its_mounts_and_is_held(self):
        mounts = {p["id"]: p for p in self.mounted["parts"] if p["id"].startswith(("mount_battery_", "mount_battery2_"))}
        self.assertEqual(sorted(mounts), ["mount_battery2_1", "mount_battery2_2", "mount_battery_1", "mount_battery_2"])
        for p in mounts.values():
            self.assertEqual(p["kind"], "bracket")
        held, free = attach.free_parts(self.mounted)
        self.assertTrue({"battery", "battery2"} <= held)
        self.assertFalse({"battery", "battery2"} & free)

    def test_u104_passes_when_split(self):
        self.assertEqual(_u104_hits(V1), ["battery"])  # 50 kg
        self.assertEqual(_u104_hits(self.split), [])  # 25 kg each

    def test_refuses_packs_that_do_not_fit(self):
        for vals, words in (({"battery_capacity": 150}, "do not fit between the seat cross members"),
                            ({"battery_x": -900}, "do not fit between the seat cross members")):
            with self.assertRaises(DesignGenError) as e:
                GEN.build({"battery_split": 2, **vals})
            self.assertIn(words, str(e.exception))
        GEN.build({"battery_capacity": 150})  # the one pack is left to K4, as before


if __name__ == "__main__":
    unittest.main()
