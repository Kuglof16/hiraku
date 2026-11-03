"""The shelf tools' weight stages (the contract's tool_spec.stage, HK5: lighter checks first)."""

import unittest

from hiraku import contract, solvers

STAGES = {"assembly": 0, "frame_static": 1, "step_climb": 1, "modal": 1, "drive_energy": 1, "thermal": 1, "crash": 1,
          "bed_static": 1, "actuator_linkage": 1, "use_fatigue": 1, "actuator_duty": 1, "frame_shell": 3, "frame_tubes": 3,
          "fatigue": 3}


class ToolStagesTest(unittest.TestCase):
    def test_shelf_stages(self):
        tools = {t.name: t for t in solvers.builtin_tools() if t.name in STAGES}
        self.assertEqual(set(tools), set(STAGES))
        for name, t in tools.items():
            with self.subTest(tool=name):
                self.assertEqual(t.describe()["stage"], STAGES[name])
                self.assertEqual(t.spec()["stage"], STAGES[name])
                self.assertEqual(contract.errors("tool_spec", t.spec()), [])


if __name__ == "__main__":
    unittest.main()
