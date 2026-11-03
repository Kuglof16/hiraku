"""The shelf tools' weight stages (the contract's tool_spec.stage, HK5: lighter checks first) and the kinds of
design they apply to (tool_spec.applies_to)."""

import unittest

from hiraku import contract, solvers

STAGES = {"assembly": 0, "frame_static": 1, "step_climb": 1, "modal": 1, "drive_energy": 1, "thermal": 1, "crash": 1,
          "bed_static": 1, "actuator_linkage": 1, "frame_shell": 3, "frame_tubes": 3, "fatigue": 3}
APPLIES_TO = {"frame_shell": ["ladder_4w"], "frame_tubes": ["space_frame_utv"], "bed_static": ["care_bed"],
              "actuator_linkage": ["care_bed"]}  # the others: any design


class ToolStagesTest(unittest.TestCase):
    def test_shelf_stages_and_kinds(self):
        tools = {t.name: t for t in solvers.builtin_tools() if t.name in STAGES}
        self.assertEqual(set(tools), set(STAGES))
        for name, t in tools.items():
            with self.subTest(tool=name):
                self.assertEqual(t.describe()["stage"], STAGES[name])
                self.assertEqual(t.spec()["stage"], STAGES[name])
                self.assertEqual(contract.errors("tool_spec", t.spec()), [])
                self.assertEqual(t.spec().get("applies_to"), APPLIES_TO.get(name))
                self.assertEqual(t.describe().get("applies_to"), APPLIES_TO.get(name))


if __name__ == "__main__":
    unittest.main()
