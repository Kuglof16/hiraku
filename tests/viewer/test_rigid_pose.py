"""HV.rigidPose (viewer/js/core.js): a part box follows the linear motion k (t + w x r) on both halves of the swing.

The function is taken out of core.js and run in Node with a small stand-in for three.js's Quaternion and Vector3
(axis-angle and rotation of a vector, the two calls it makes). Skipped where Node is not installed."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")

STUB = """
class Vector3 { constructor(x, y, z) { this.x = x; this.y = y; this.z = z; }
  normalize() { const n = Math.hypot(this.x, this.y, this.z); this.x /= n; this.y /= n; this.z /= n; return this; }
  applyQuaternion(q) { const [x, y, z] = [this.x, this.y, this.z], [qx, qy, qz, qw] = [q.x, q.y, q.z, q.w];
    const ix = qw * x + qy * z - qz * y, iy = qw * y + qz * x - qx * z, iz = qw * z + qx * y - qy * x, iw = -qx * x - qy * y - qz * z;
    this.x = ix * qw + iw * -qx + iy * -qz - iz * -qy; this.y = iy * qw + iw * -qy + iz * -qx - ix * -qz;
    this.z = iz * qw + iw * -qz + ix * -qy - iy * -qx; return this; } }
class Quaternion { constructor() { this.x = 0; this.y = 0; this.z = 0; this.w = 1; }
  setFromAxisAngle(a, ang) { const s = Math.sin(ang / 2); this.x = a.x * s; this.y = a.y * s; this.z = a.z * s; this.w = Math.cos(ang / 2); return this; } }
const window = { THREE: { Vector3, Quaternion } }; const HV = {};
"""


@unittest.skipUnless(NODE, "Node is not installed")
class RigidPoseTest(unittest.TestCase):
    def run_pose(self, args) -> dict:
        src = (ROOT / "viewer" / "js" / "core.js").read_text(encoding="utf-8")
        fn = re.search(r"  HV\.rigidPose = function.*?\n  };\n", src, re.S).group(0)
        js = STUB + fn + f"const out = {json.dumps(args)}.map(a => HV.rigidPose(...a));" \
            "console.log(JSON.stringify(out.map(o => ({off: o.off, q: o.q && [o.q.x, o.q.y, o.q.z, o.q.w]}))));"
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_both_halves_of_the_swing_turn_the_box(self):
        # a box 1156 mm from the fit centre turned about y by 2.8 deg at k = 150 (the v5 mode 4 head in the report)
        pc, c, t = [0.0, 0.0, 1156.0], [0.0, 0.0, 0.0], [0.0, 0.01, 0.0]
        w = [0.0, 0.049 / 150 * 1.0, 0.0]
        k = 150.0
        plus, minus = self.run_pose([[pc, c, t, w, k], [pc, c, t, w, -k]])
        r = [pc[i] - c[i] for i in range(3)]
        lin = [t[0] + w[1] * r[2] - w[2] * r[1], t[1] + w[2] * r[0] - w[0] * r[2], t[2] + w[0] * r[1] - w[1] * r[0]]
        for got, s in ((plus, 1.0), (minus, -1.0)):
            self.assertIsNotNone(got["q"])
            for a in range(3):  # within the rotation's second order: r (1 - cos) at 2.8 deg is under 1.4 mm
                self.assertAlmostEqual(got["off"][a], s * k * lin[a], delta=1.5)
        # the two halves mirror each other (a sine swing about rest)
        for a in range(3):
            self.assertAlmostEqual(plus["off"][a] + minus["off"][a], 0.0, delta=3.0)
        self.assertAlmostEqual(plus["q"][1], -minus["q"][1], places=12)

    def test_no_turn_without_rotation(self):
        (out,) = self.run_pose([[[1.0, 2.0, 3.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.0], -2.0]])
        self.assertIsNone(out["q"])
        self.assertEqual(out["off"], [-2.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
