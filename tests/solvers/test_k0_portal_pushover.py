"""K0 portal formulas (frame_static.portal_frame_side, _rear, _top on portal_pushover) against closed forms:
portal sway stiffness with the beam's stiffness ratio, cantilever posts, foot springs in series, plastic mechanism
loads and the energy under the force-deflection polyline. No fitting: each value is the textbook expression."""

import math
import unittest

from hiraku import solvers
from hiraku.solvers.k0_handcalc import library_portal as lp
from hiraku.solvers.k0_handcalc import portal_pushover as pp
from hiraku.solvers.k0_handcalc.library_portal import COMMON

FORMULAS = solvers.formula_registry()
E, NU = 205000.0, 0.3
G = E / (2 * (1 + NU))
H, W = 1400.0, 1200.0
I = 6.0e4  # mm^4
MP = 8.0e5  # N*mm
# factors for "rigid" and "none" (not more: the mechanism check scales the stiffness matrix by its diagonal,
# so a far stiffer member next to a soft mode would make the mode look null)
BIG, TINY = 1e6, 1e-6
A0 = 1e6  # mm^2, axially near rigid
UNITS = {"E": "MPa", "nu": "1", "f": "1", "E_in": "J", "F": "N", "f_y": "MPa"}


def unit(k):
    if k in UNITS:
        return UNITS[k]
    head = k.split("_")[0]
    return {"A": "mm^2", "Iy": "mm^4", "Iz": "mm^4", "J": "mm^4"}.get(head, "N*mm" if head in ("Mpy", "Mpz", "Tp")
                                                                        else "mm")


def group(g, I_=I, A=A0, J=None, mp=MP, Iy=None, Iz=None, Tp=None):
    return {f"A_{g}": A, f"Iy_{g}": I_ if Iy is None else Iy, f"Iz_{g}": I_ if Iz is None else Iz,
            f"J_{g}": 2 * I_ if J is None else J, f"Mpy_{g}": mp, f"Mpz_{g}": mp, f"Tp_{g}": mp if Tp is None else Tp}


def base(**kw):
    """Fixed feet (no outrigger, no rail), equal posts and beam, axially rigid."""
    v = {"E": E, "nu": NU, "h": H, "w": W, "e": 0.0, "L_a": 0.0, "L_b": 0.0,
         **group("c"), **group("b"), **group("o"), **group("r")}
    v.update(kw)
    assert set(COMMON) <= set(v)
    return v


def run(fid, v, **extra):
    vals = {**v, **extra}
    ev = FORMULAS.evaluate(f"frame_static.portal_frame_{fid}", {k: {"value": float(x), "unit": unit(k)}
                                                                for k, x in vals.items()})
    return {k: o["value"] for k, o in ev["outputs"].items()}


def side(v, E_in=1e-3):
    return run("side", v, E_in=E_in)


def rear(v, f=0.5, E_in=1e-3):
    return run("rear", v, f=f, E_in=E_in)


def top(v, F=1.0, f_y=235.0):
    return run("top", v, F=F, f_y=f_y)


class StiffnessTest(unittest.TestCase):
    # "rigid" and "none" are factors of 1e6 and 1e-6, so the closed forms hold to about 1e-5 (places=4)

    def test_fixed_base_portal_sway_with_the_beam_ratio(self):
        """H / delta = (24 E I_c / h^3) (6 rho + 1) / (6 rho + 4), rho = (I_b / w) / (I_c / h): 24 E I / h^3 with a
        rigid beam, 6 E I / h^3 with none (slope-deflection, fixed-base portal, sway only)."""
        a = E * I / H**3
        for Ib in (I * BIG, I * TINY, I, 3 * I):
            rho = (Ib / W) / (I / H)
            k = side(base(Iz_b=Ib))["k"]
            self.assertAlmostEqual(k / (24 * a * (6 * rho + 1) / (6 * rho + 4)), 1.0, places=4, msg=f"I_b {Ib}")

    def test_cantilever_post(self):
        """Load along the vehicle at the left corner, the top beam with no stiffness out of the portal's plane: the
        left post alone is a cantilever, P / delta = 3 E I / h^3. At mid-span the corners turn alike, so the beam
        turns about its axis without twisting and the posts' tops are free: two cantilevers, 6 E I / h^3 (the beam
        rigid about the vertical)."""
        v = base(Iy_b=I * TINY, J_b=I * TINY)
        self.assertAlmostEqual(rear(v, f=0.0)["k"] / (3 * E * I / H**3), 1.0, places=4)
        self.assertAlmostEqual(rear(base(Iy_b=I * BIG), f=0.5)["k"] / (6 * E * I / H**3), 1.0, places=4)

    def test_foot_spring_of_the_rail_in_series(self):
        """Post on a rail held at L_a and L_b; the rail rigid but for its twist: the foot's rotational spring is
        G J_r (1 / L_a + 1 / L_b); with no beam bending each post is a cantilever on it, delta / H_post = h^3 / (3 E I)
        + h^2 / k_t (springs in series)."""
        La, Lb, Jr = 900.0, 500.0, 4.0e4
        v = base(Iz_b=I * TINY, L_a=La, L_b=Lb, **group("r", I_=I * BIG, J=Jr, mp=MP * BIG))
        kt = G * Jr * (1 / La + 1 / Lb)
        self.assertAlmostEqual(side(v)["k"] / (2 / (H**3 / (3 * E * I) + H**2 / kt)), 1.0, places=4)

    def test_foot_spring_of_the_outrigger_in_series(self):
        """Foot on an outrigger of length e across the vehicle, the rail rigid: the outrigger bends vertically as a
        cantilever from the root, the foot turning M e / (E I_o) in the portal's plane (in series with the post)."""
        e, Io = 250.0, 3.0e4
        v = base(Iz_b=I * TINY, e=e, L_a=800.0, L_b=800.0, **group("r", I_=I * BIG, mp=MP * BIG),
                 **group("o", I_=I * BIG, Iz=Io, mp=MP * BIG))
        self.assertAlmostEqual(side(v)["k"] / (2 / (H**3 / (3 * E * I) + H**2 * e / (E * Io))), 1.0, places=4)


class PlasticTest(unittest.TestCase):
    def test_sway_mechanism(self):
        """Fixed-base portal, equal M_p, load at the corner: hinges at both feet and both corners, H_c = 4 M_p / h
        (B. G. Neal, sway mechanism). At a corner the post's top and the beam's end carry the same moment and are
        counted as two hinges formed together."""
        r = side(base())
        self.assertAlmostEqual(r["H_c"] / (4 * MP / H), 1.0, places=6)
        self.assertLess(r["H_first"], r["H_c"])
        # the beam weaker than the posts: the corner hinges are in the beam, still 4 M_p / h with M_p the posts' at
        # the feet and the beam's at the corners: H_c = (2 M_p,post + 2 M_p,beam) / h
        r = side(base(Mpz_b=0.5 * MP))
        self.assertAlmostEqual(r["H_c"] / ((2 * MP + 2 * 0.5 * MP) / H), 1.0, places=6)

    def test_sway_mechanism_on_outriggers(self):
        """Feet on outriggers (length e) with the rail rigid and strong, the outriggers weaker in vertical bending:
        the foot hinges are at the outriggers' roots. Virtual work of the sway (each side, outrigger and post, turns
        theta about its root, so the corners rise and fall e theta and the beam turns 2 e theta / w the same way,
        the corner hinges turning theta (1 - 2 e / w)): H_c = (2 M_p,o + 2 M_p (1 - 2 e / w)) / h."""
        e = 250.0
        v = base(e=e, L_a=800.0, L_b=800.0, **group("r", I_=I * BIG, mp=MP * BIG),
                 **group("o", I_=I * BIG, mp=0.25 * MP, Tp=MP * BIG))
        r = side(v)
        self.assertAlmostEqual(r["H_c"] / ((2 * 0.25 * MP + 2 * MP * (1 - 2 * e / W)) / H), 1.0, places=6)

    def test_rear_feet_hinged(self):
        """Load along the vehicle at mid-span: the posts' tops are free to turn (above), a hinge at each foot
        makes the mechanism (the frame turns about the line through the feet), P_c = 2 M_p / h."""
        r = rear(base())
        self.assertAlmostEqual(r["P_c"] / (2 * MP / H), 1.0, places=6)
        self.assertAlmostEqual(r["mech_near"], 1.0, places=6)
        self.assertAlmostEqual(r["mech_far"], 1.0, places=6)

    def test_beam_mechanism(self):
        """Down at mid-span, posts far stronger and stiffer: hinges at the beam's ends and middle, F_c = 8 M_p / w;
        under 1 N the beam is a fixed-fixed beam, delta = F w^3 / (192 E I)."""
        v = base(**group("c", I_=I * BIG, mp=MP * BIG))
        r = top(v)
        self.assertAlmostEqual(r["F_c"] / (8 * MP / W), 1.0, places=6)
        self.assertAlmostEqual(r["delta"] / (W**3 / (192 * E * I)), 1.0, places=4)
        self.assertEqual(r["collapsed"], 0.0)
        over = top(v, F=2 * 8 * MP / W)
        self.assertEqual(over["collapsed"], 1.0)
        self.assertEqual(over["delta"], H)  # collapse below F: taken as the posts' length

    def test_energy_elastic_perfectly_plastic(self):
        """Mid-span rear load, the beam rigid about the vertical: both feet hinge together, so the polyline is
        elastic then flat (elastic, perfectly plastic): k = 6 E I / h^3, P_c = 2 M_p / h; E = k d^2 / 2 below the
        yield, d = d_y + (E - P_c d_y / 2) / P_c above (the area of the polyline)."""
        v = base(Iy_b=I * BIG, Mpy_b=MP * BIG)
        k, Pc = 6 * E * I / H**3, 2 * MP / H
        dy = Pc / k
        e_small = 0.5 * Pc * dy / 2  # N*mm, half the energy at the yield
        small = rear(v, f=0.5, E_in=e_small / 1000)
        self.assertAlmostEqual(small["delta_E"] / math.sqrt(2 * e_small / k), 1.0, places=4)
        self.assertEqual(small["plastic"], 0.0)
        big = rear(v, f=0.5, E_in=50.0)
        self.assertAlmostEqual(big["P_c"] / Pc, 1.0, places=6)
        self.assertEqual(big["n_steps"], 1.0)
        self.assertAlmostEqual(big["delta_E"] / (dy + (50e3 - Pc * dy / 2) / Pc), 1.0, places=4)

    def test_energy_is_the_polyline_area(self):
        """Hinges one by one (the beam weaker than the posts, so the corners hinge before the feet): the trapezoids
        under the path plus the plateau at H_c out to delta_E are E_in."""
        v = base(Iz_b=2 * I, Mpz_b=0.7 * MP)
        res, _, _ = lp._run(v, "side")
        self.assertGreater(len(res["path"]), 2)
        r = side(v, E_in=40.0)
        path = res["path"]
        area, (f_end, d_end) = pp.energy_at(path)[-1], path[-1]
        self.assertAlmostEqual(r["H_c"], f_end)
        self.assertAlmostEqual(area + f_end * (r["delta_E"] - d_end), 40e3, delta=1e-6 * 40e3)

    def test_a_frame_with_no_stiffness_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "must be positive"):
            side(base(J_c=0.0))


if __name__ == "__main__":
    unittest.main()
