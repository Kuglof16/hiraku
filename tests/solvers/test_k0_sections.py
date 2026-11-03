"""K0 open sections (i_beam, channel, angle) against closed forms and a raster integration."""

import math
import unittest

import numpy as np

from hiraku import contract
from hiraku.solvers.k0_handcalc import sections


def raster(inside, y_range, z_range, n=1200):
    """A, centroid and second moments of a region by summing a fine grid of cells."""
    ys = np.linspace(*y_range, n + 1)
    zs = np.linspace(*z_range, n + 1)
    yc, zc = (ys[:-1] + ys[1:]) / 2.0, (zs[:-1] + zs[1:]) / 2.0
    Y, Z = np.meshgrid(yc, zc, indexing="ij")
    cell = (ys[1] - ys[0]) * (zs[1] - zs[0])
    m = inside(Y, Z)
    A = m.sum() * cell
    y0, z0 = (Y * m).sum() * cell / A, (Z * m).sum() * cell / A
    return {"A": A, "Iz": (((Y - y0) ** 2) * m).sum() * cell, "Iy": (((Z - z0) ** 2) * m).sum() * cell,
            "Iyz": (((Y - y0) * (Z - z0)) * m).sum() * cell}


class OpenSections(unittest.TestCase):
    def test_i_beam_closed_forms(self):
        h, b, tw, tf = 200.0, 100.0, 5.5, 8.0
        p = sections.properties({"shape": "i_beam", "dims_mm": {"h": h, "b": b, "tw": tw, "tf": tf}})
        self.assertAlmostEqual(p["A"], 2 * b * tf + (h - 2 * tf) * tw, places=9)
        self.assertAlmostEqual(p["Iz"], (b * h**3 - (b - tw) * (h - 2 * tf) ** 3) / 12.0, delta=1e-9 * p["Iz"])
        self.assertAlmostEqual(p["Iy"], (2 * tf * b**3 + (h - 2 * tf) * tw**3) / 12.0, delta=1e-9 * p["Iy"])
        self.assertAlmostEqual(p["Iyz"], 0.0, delta=1e-9 * p["Iz"])
        self.assertEqual((p["cy"], p["cz"]), (h / 2.0, b / 2.0))
        # thin plates: J near sum b t^3 / 3
        thin = (2 * b * tf**3 + (h - 2 * tf) * tw**3) / 3.0
        self.assertAlmostEqual(p["J"] / thin, 1.0, delta=0.05)
        self.assertAlmostEqual(p["tau_per_T"], tf / p["J"])

    def test_channel_and_angle_against_a_raster(self):
        h, b, tw, tf = 150.0, 75.0, 6.5, 10.0
        ch = sections.properties({"shape": "channel", "dims_mm": {"h": h, "b": b, "tw": tw, "tf": tf}})
        r = raster(lambda y, z: ((y < tf) | (y > h - tf) | (z < tw)), (0.0, h), (0.0, b))
        for k in ("A", "Iy", "Iz"):
            self.assertAlmostEqual(ch[k] / r[k], 1.0, delta=3e-3, msg=k)
        self.assertAlmostEqual(ch["Iyz"], 0.0, delta=1e-9 * ch["Iz"])
        self.assertAlmostEqual(ch["Iz"], (b * h**3 - (b - tw) * (h - 2 * tf) ** 3) / 12.0, delta=1e-9 * ch["Iz"])
        L, t = 50.0, 5.0
        an = sections.properties({"shape": "angle", "dims_mm": {"h": L, "b": L, "t": t}})
        r = raster(lambda y, z: (y < t) | (z < t), (0.0, L), (0.0, L))
        for k in ("A", "Iy", "Iz", "Iyz"):
            self.assertAlmostEqual(an[k] / r[k], 1.0, delta=3e-3, msg=k)
        # an equal angle: the same Iy and Iz, principal axes at 45 deg, I_min = Iy - |Iyz|
        self.assertAlmostEqual(an["Iy"], an["Iz"], delta=1e-9 * an["Iy"])
        self.assertLess(an["Iyz"], 0.0)  # the legs lie on the low side of both axes of the centroid
        e = (L * t * L / 2.0 + (L - t) * t * t / 2.0) / an["A"]  # centroid from the back of a leg
        self.assertAlmostEqual(an["cy"], L - e, places=9)

    def test_bad_dimensions_and_the_contract_shapes(self):
        with self.assertRaisesRegex(ValueError, "2 tf < h"):
            sections.properties({"shape": "i_beam", "dims_mm": {"h": 10.0, "b": 50.0, "tw": 3.0, "tf": 6.0}})
        with self.assertRaisesRegex(ValueError, "missing tf"):
            sections.properties({"shape": "channel", "dims_mm": {"h": 100.0, "b": 50.0, "tw": 5.0}})
        for shape in ("i_beam", "channel", "angle"):
            self.assertIn(shape, sections.SUPPORTED_SHAPES)
            self.assertEqual(contract.errors("section", {"shape": shape, "dims_mm": {"h": 1.0}}), [])
        # the stress check reads the open sections like the others
        p = sections.properties({"shape": "i_beam", "dims_mm": {"h": 200.0, "b": 100.0, "tw": 5.5, "tf": 8.0}})
        s = sections.corner_von_mises(p, 0.0, 0.0, 1e6, 0.0)
        self.assertAlmostEqual(s, 1e6 * 100.0 / p["Iz"], places=9)
        self.assertTrue(math.isfinite(sections.corner_von_mises(p, 1e3, 1e5, 1e6, 1e4)))


if __name__ == "__main__":
    unittest.main()
