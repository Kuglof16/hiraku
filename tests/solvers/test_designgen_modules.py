"""ladder_4w split into modules (hiraku.designgen.modules, ladder_4w_modules): module on every part and member, the
frame's nodes welded, design.modules with owners, variables, choices, envelopes and interfaces; build_version builds a
round's proposals into one version and refuses a variable that is not the module's, a variable set twice, and a part
outside its module's space."""

from __future__ import annotations

import unittest

from hiraku import contract
from hiraku.designgen import ladder_4w as GEN
from hiraku.designgen import modules as M
from hiraku.designgen.ladder_4w_modules import CHOICE_MODULE, VARIABLE_MODULE

SETS = [{}, {"part_mounts": "brackets", "pedal_mount": "post", "side_guard": "bars_net"},
        {"part_mounts": "brackets", "pedal_mount": "post", "side_guard": "bars", "side_opening": "door",
         "grab_bar": "front_post"},
        {"rops": "four_post", "side_guard": "bars", "fenders": "wheel"}]


def q(v, u):
    return {"value": v, "unit": u}


def chg(i, m, **kw):
    return {"change_id": f"chg-{i:04d}", "module_id": m, "agent_id": f"designer.{m}", "reason": "test", **kw}


class SplitTest(unittest.TestCase):
    def test_every_revisable_variable_and_choice_has_one_module(self):
        revisable = {k for k, v in GEN.VARIABLES.items() if v.revisable}
        self.assertEqual(set(VARIABLE_MODULE), revisable)
        self.assertEqual(set(CHOICE_MODULE), set(GEN.CHOICES))

    def test_every_known_kind_has_a_module(self):
        from hiraku.designgen.kit.systems import SYSTEM_OF_KIND
        from hiraku.designgen.ladder_4w_modules import KIND_MODULE

        self.assertEqual(sorted(set(SYSTEM_OF_KIND) - set(KIND_MODULE)), [])

    def test_option_sets(self):
        for ch in SETS:
            with self.subTest(choices=ch):
                d = GEN.build(GEN.apply_choices(ch, {}))
                self.assertEqual(contract.errors("design", d), [])
                self.assertTrue(all("module" in p for p in d["parts"]))
                self.assertTrue(all("module" in m for m in d["members"]))
                self.assertTrue(all(n["joint"] == "weld" for n in d["nodes"]))
                mods = {m["module_id"]: m for m in d["modules"]}
                self.assertEqual(list(mods), ["package", "frame", "suspension", "steering", "occupant", "powertrain",
                                              "body"])
                self.assertEqual(mods["package"]["owner"], "packager")
                self.assertEqual(mods["frame"]["owner"], "designer.frame")
                self.assertEqual(mods["package"]["envelope"], [])
                self.assertIn("wheelbase", mods["package"]["variables"])
                envs = {k: m["envelope"] for k, m in mods.items()}
                self.assertEqual(M.outside(d, envs), [])
                pairs = {tuple(i["between"]) for i in mods["package"]["interfaces"]}
                for pair in (("frame", "suspension"), ("frame", "powertrain"), ("body", "frame"), ("frame", "occupant"),
                             ("frame", "steering")):
                    self.assertIn(pair, pairs)

    def test_module_of_kinds(self):
        d = GEN.build(GEN.apply_choices({"part_mounts": "brackets", "pedal_mount": "post"}, {}))
        mod = {p["id"]: p["module"] for p in d["parts"]}
        self.assertEqual((mod["battery"], mod["seat_R"], mod["floor"], mod["wheel_FL"], mod["tie_rod_FL"],
                          mod["toe_link_RL"], mod["bracket_column"]),
                         ("powertrain", "occupant", "body", "suspension", "steering", "suspension", "frame"))

    def test_unknown_kind_is_refused(self):
        d = GEN.build()
        d["parts"][0] = dict(d["parts"][0], kind="mystery")
        from hiraku.designgen.ladder_4w_modules import split

        with self.assertRaisesRegex(M.ModuleError, "mystery"):
            split(d)


class BuildVersionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d1 = GEN.build(GEN.apply_choices({"part_mounts": "brackets", "pedal_mount": "post",
                                              "side_guard": "bars_net"}, {}))

    def test_two_modules_in_one_version(self):
        n = M.build_version(self.d1, [chg(1, "frame", changes={"rail_t": q(3.2, "mm")}),
                                      chg(2, "occupant", changes={"seat_depth": q(420.0, "mm")})], reason="r")
        self.assertEqual((n["design_version"], n["parent_version"]), (2, 1))
        self.assertEqual(contract.errors("design", n), [])
        self.assertEqual(n["variables"]["rail_t"]["value"], 3.2)
        self.assertEqual([c["change_id"] for c in n["module_changes"]], ["chg-0001", "chg-0002"])

    def test_not_the_modules_variable(self):
        with self.assertRaisesRegex(M.ModuleError, "seat_height is not a variable of module frame"):
            M.build_version(self.d1, [chg(1, "frame", changes={"seat_height": q(320.0, "mm")})])

    def test_same_variable_twice(self):
        with self.assertRaisesRegex(M.ModuleError, "side_bars is changed by both"):
            M.build_version(self.d1, [chg(1, "occupant", changes={"side_bars": q(1.0, "1")}),
                                      chg(2, "occupant", choices={"side_guard": "bars"})])

    def test_outside_the_space(self):
        # new gussets stand out of the frame's space until the packager gives it room
        with self.assertRaisesRegex(M.ModuleError, "outside their module's space: gusset_"):
            M.build_version(self.d1, [chg(1, "frame", choices={"joints": "gusset"})])
        n = M.build_version(self.d1, [chg(1, "frame", choices={"joints": "gusset"})], regrow=True)
        self.assertTrue(any(p["kind"] == "gusset" for p in n["parts"]))

    def test_layout_change_makes_the_spaces_again(self):
        n = M.build_version(self.d1, [], changes={"wheelbase": q(2100.0, "mm")})
        self.assertEqual(n["variables"]["wheelbase"]["value"], 2100.0)
        self.assertEqual(n["module_changes"], [])

    def test_packager_only_changes_layout(self):
        with self.assertRaisesRegex(M.ModuleError, "rail_t is not a variable of module package"):
            M.build_version(self.d1, [], changes={"rail_t": q(3.2, "mm")})


if __name__ == "__main__":
    unittest.main()
