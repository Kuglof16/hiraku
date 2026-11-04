"""ladder_4w_names: every frame member has a person's name (member.name), every variable a module's designer may
change has one (design.variable_names), on the option sets."""

from __future__ import annotations

import unittest

from hiraku import contract
from hiraku.designgen import ladder_4w as GEN

SETS = [{}, {"part_mounts": "brackets", "pedal_mount": "post", "side_guard": "bars", "side_opening": "door",
             "grab_bar": "front_post", "rops": "hoop_stays"},
        {"side_guard": "bars_net", "rops": "four_post_stays", "pedal_mount": "bulkhead"},
        {"side_guard": "net", "rops": "hoop", "fenders": "wheel"}]


class NamesTest(unittest.TestCase):
    def test_every_member_and_variable_named(self):
        for ch in SETS:
            with self.subTest(choices=ch):
                d = GEN.build(GEN.apply_choices(ch, {}))
                self.assertEqual(contract.errors("design", d), [])
                self.assertEqual([m["id"] for m in d["members"] if m["name"] == m["id"]], [])
                owned = {v for m in d["modules"] for v in m["variables"]}
                self.assertEqual(set(d["variable_names"]), owned)
                self.assertEqual([v for v, n in d["variable_names"].items() if n == v], [])

    def test_a_rail_piece_says_from_where_to_where(self):
        d = GEN.build()
        name = {m["id"]: m["name"] for m in d["members"]}
        self.assertEqual(name["rail_L_AS1"], "左のレール (前の塔の後ろの柱から座席の前まで)")
        self.assertEqual(name["cross_A"], "前の車軸の横の部材")
        self.assertEqual(name["tower_arm_LA"], "前左の塔の腕 (ダンパーの上へ)")


if __name__ == "__main__":
    unittest.main()
