"""The lattice, the plain CPU collision and the shapes: nothing here needs a GPU.

The geometry checks use predicates and root formulas written here, not the ones in geometry.py, so a wrong
formula on one side does not hide behind the same wrong formula on the other.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import numpy as np

from hiraku.cfd.geometry import Cylinder, Grid, Sphere, box, geometry_report, link_arrays, prism, voxelize
from hiraku.cfd.lattice import C, CS2, OPP, W, collide, equilibrium_h, moments
from hiraku.cfd.solver import Flow, Units, ground_cf, sponge_profile

KERNELS = Path(__file__).resolve().parents[2] / "hiraku" / "cfd" / "kernels.cu"


def random_state(rng: np.random.Generator, shape: tuple[int, ...] = (4, 5, 6), amplitude: float = 1e-3) -> np.ndarray:
    """A nonuniform h with a nonequilibrium part in every moment (the ghost moments as well)."""
    dr = 0.02 * rng.normal(size=shape)
    u = 0.04 * rng.normal(size=(3,) + shape)
    noise = amplitude * W.reshape((19,) + (1,) * len(shape)) * rng.normal(size=(19,) + shape)
    return equilibrium_h(dr, u) + noise


def second_moment(h: np.ndarray) -> np.ndarray:
    return np.stack([np.stack([sum(int(C[q, a] * C[q, b]) * h[q] for q in range(19)) for b in range(3)]) for a in range(3)])


def nonequilibrium_stress(h: np.ndarray) -> np.ndarray:
    dr, u = moments(h)
    return second_moment(h - equilibrium_h(dr, u))


class LatticeAlgebra(unittest.TestCase):
    def test_the_nineteen_velocities(self):
        self.assertEqual(C.shape, (19, 3))
        self.assertEqual(len({tuple(c) for c in C}), 19)
        speeds = (C * C).sum(axis=1)
        self.assertEqual([int((speeds == s).sum()) for s in (0, 1, 2)], [1, 6, 12])

    def test_weights(self):
        speeds = (C * C).sum(axis=1)
        self.assertAlmostEqual(float(W.sum()), 1.0, places=14)
        self.assertTrue(np.allclose(W[speeds == 0], 1.0 / 3.0))
        self.assertTrue(np.allclose(W[speeds == 1], 1.0 / 18.0))
        self.assertTrue(np.allclose(W[speeds == 2], 1.0 / 36.0))

    def test_opposites(self):
        self.assertTrue(np.array_equal(C[OPP], -C))
        self.assertTrue(np.array_equal(OPP[OPP], np.arange(19)))

    def test_odd_moments_vanish(self):
        self.assertTrue(np.allclose(W @ C, 0.0, atol=1e-15))
        third = np.einsum("q,qa,qb,qc->abc", W, C, C, C)
        self.assertTrue(np.allclose(third, 0.0, atol=1e-15))

    def test_second_moment_is_isotropic(self):
        second = np.einsum("q,qa,qb->ab", W, C, C)
        self.assertTrue(np.allclose(second, CS2 * np.eye(3), atol=1e-15))

    def test_fourth_moment_is_isotropic(self):
        fourth = np.einsum("q,qa,qb,qc,qd->abcd", W, C, C, C, C)
        d = np.eye(3)
        want = CS2 ** 2 * (np.einsum("ab,cd->abcd", d, d) + np.einsum("ac,bd->abcd", d, d) + np.einsum("ad,bc->abcd", d, d))
        self.assertTrue(np.allclose(fourth, want, atol=1e-15))

    def test_kernel_tables_have_the_same_order(self):
        text = KERNELS.read_text(encoding="utf-8")
        for axis, name in enumerate("XYZ"):
            for table in (f"C{name}", f"C{name}F"):
                found = re.search(rf"{table}\[19\] = \{{([^}}]*)\}}", text)
                self.assertIsNotNone(found, table)
                values = [int(v) for v in found.group(1).split(",")]
                self.assertEqual(values, [int(v) for v in C[:, axis]], table)

    def test_equilibrium_has_the_right_moments(self):
        rng = np.random.default_rng(1)
        dr = 0.03 * rng.normal(size=(3, 4))
        u = 0.05 * rng.normal(size=(3, 3, 4))
        h = equilibrium_h(dr, u)
        got_dr, got_u = moments(h)
        self.assertTrue(np.allclose(got_dr, dr, atol=1e-14))
        self.assertTrue(np.allclose(got_u, u, atol=1e-14))
        want = np.einsum("ab,...->ab...", np.eye(3), dr * CS2) + (1.0 + dr) * np.einsum("a...,b...->ab...", u, u)
        self.assertTrue(np.allclose(second_moment(h), want, atol=1e-14))


class CpuCollision(unittest.TestCase):
    def test_conserves_mass_and_momentum(self):
        rng = np.random.default_rng(2)
        h = random_state(rng)
        dr0, u0 = moments(h)
        j0 = (1.0 + dr0) * u0
        for cs2 in (0.0, 0.17 ** 2):
            for tau in (0.5001, 0.515, 0.8, 1.7):
                out = collide(h, tau, cs2)
                dr1, u1 = moments(out)
                self.assertLess(np.abs(dr1 - dr0).max(), 1e-13)
                self.assertLess(np.abs((1.0 + dr1) * u1 - j0).max(), 1e-13)

    def test_equilibrium_is_a_fixed_point(self):
        rng = np.random.default_rng(3)
        dr = 0.03 * rng.normal(size=(5, 5))
        u = 0.05 * rng.normal(size=(3, 5, 5))
        h = equilibrium_h(dr, u)
        for cs2 in (0.0, 0.17 ** 2):
            self.assertLess(np.abs(collide(h, 0.6, cs2) - h).max(), 1e-14)

    def test_stress_relaxes_by_the_time_constant(self):
        rng = np.random.default_rng(4)
        h = random_state(rng)
        for tau in (0.515, 0.9):
            before = nonequilibrium_stress(h)
            after = nonequilibrium_stress(collide(h, tau))
            self.assertLess(np.abs(after - (1.0 - 1.0 / tau) * before).max(), 1e-13)

    def test_regularization_removes_the_higher_moments(self):
        rng = np.random.default_rng(5)
        h = random_state(rng)
        out = collide(h, 0.7)
        dr, u = moments(out)
        neq = out - equilibrium_h(dr, u)
        third = np.stack([sum(int(C[q, 0] * C[q, 0] * C[q, a]) * neq[q] for q in range(19)) for a in range(3)])
        self.assertLess(np.abs(third).max(), 1e-14)
        # the nonequilibrium part after collision is exactly the second-order Hermite term of its own stress
        Pi = second_moment(neq)
        trace = Pi[0, 0] + Pi[1, 1] + Pi[2, 2]
        for q in range(19):
            q_pi = sum(int(C[q, a] * C[q, b]) * Pi[a, b] for a in range(3) for b in range(3)) - trace / 3.0
            self.assertLess(np.abs(neq[q] - 4.5 * W[q] * q_pi).max(), 1e-13)

    def test_smagorinsky_closure_is_the_consistent_root(self):
        # tau solves tau (tau - tau0) = 9/2 Cs^2 sqrt(2 Pi:Pi) / rho, with Pi the stress before collision
        rng = np.random.default_rng(6)
        h = random_state(rng, amplitude=3e-2)
        tau0, cs2 = 0.51, 0.17 ** 2
        before = nonequilibrium_stress(h)
        after = nonequilibrium_stress(collide(h, tau0, cs2))
        rho = 1.0 + moments(h)[0]
        pp = (before * before).sum(axis=(0, 1))
        # the ratio of the stresses is (1 - 1/tau), taken on the largest component of each node
        ratio = (after * before).sum(axis=(0, 1)) / pp
        tau = 1.0 / (1.0 - ratio)
        self.assertLess(np.abs(tau * (tau - tau0) - 4.5 * cs2 * np.sqrt(2.0 * pp) / rho).max(), 1e-10)
        self.assertTrue((tau > tau0).all())

    def test_cs_zero_gives_the_molecular_tau(self):
        rng = np.random.default_rng(7)
        h = random_state(rng)
        self.assertLess(np.abs(collide(h, 0.6, 0.0) - collide(h, 0.6)).max(), 1e-15)


def line_samples(rng: np.random.Generator, lo: np.ndarray, hi: np.ndarray, n: int) -> np.ndarray:
    return lo + rng.random((n, 3)) * (hi - lo)


class ShapesAgainstPointSampling(unittest.TestCase):
    """inside and clip against a separately written predicate, on random points and along random lines."""

    def check(self, shape, predicate, seed: int) -> None:
        rng = np.random.default_rng(seed)
        lo, hi = shape.bounds()
        pad = 0.4 * (hi - lo).max()
        points = line_samples(rng, lo - pad, hi + pad, 20000)
        want = predicate(points)
        self.assertGreater(want.mean(), 0.02, "the sample must hit the shape")
        self.assertLess(want.mean(), 0.98, "the sample must miss the shape as well")
        self.assertTrue(np.array_equal(shape.inside(points), want))
        # the bounds hold every inside point and are not loose
        inner = points[want]
        self.assertTrue((inner >= lo - 1e-12).all() and (inner <= hi + 1e-12).all())
        self.assertLess(np.abs(inner.max(axis=0) - hi).max(), 0.25 * (hi - lo).max())
        self.assertLess(np.abs(inner.min(axis=0) - lo).max(), 0.25 * (hi - lo).max())
        directions = [rng.normal(size=3) for _ in range(8)] + [C[q].astype(float) for q in range(1, 19)]
        axis = getattr(shape, "axis", None)
        if axis is not None:
            directions.append(np.asarray(axis, float))
        scale = float((hi - lo).max())
        ts = np.linspace(-3.0, 3.0, 601)
        step = float(ts[1] - ts[0])
        for d in directions:
            d = d / np.linalg.norm(d) * scale
            starts = line_samples(rng, lo - 0.3 * scale, hi + 0.3 * scale, 200)
            t_in, t_out = shape.clip(starts, d)
            for s, a, b in zip(starts, t_in, t_out):
                inside = predicate(s + ts[:, None] * d)
                hit = inside.any()
                met = a <= b
                if hit:
                    self.assertTrue(met, "the sampling hits the shape, clip says no meeting")
                    self.assertGreaterEqual(ts[inside].min() + 1e-9, a - step * 1e-6)
                    self.assertLessEqual(ts[inside].max() - 1e-9, b + step * 1e-6)
                    self.assertLessEqual(a, ts[inside].min() + 1e-9)
                    self.assertGreaterEqual(a, ts[inside].min() - step - 1e-9)
                    self.assertGreaterEqual(b, ts[inside].max() - 1e-9)
                    self.assertLessEqual(b, ts[inside].max() + step + 1e-9)
                elif met:
                    # a chord shorter than the sampling step can fall between two samples, never a longer one
                    self.assertLess(b - a, step + 1e-9)

    def test_sphere(self):
        c, r = np.array([0.3, -0.2, 0.5]), 0.7
        self.check(Sphere(c, r), lambda p: np.linalg.norm(p - c, axis=1) <= r, 11)

    def test_cylinder_oblique(self):
        c = np.array([0.1, 0.4, -0.2])
        a = np.array([1.0, 2.0, -0.5])
        a /= np.linalg.norm(a)
        r, w = 0.3, 1.1

        def pred(p):
            rel = p - c
            s = rel @ a
            return (np.abs(s) <= w / 2.0) & (np.linalg.norm(rel - np.outer(s, a), axis=1) <= r)

        self.check(Cylinder(c, a, r, w), pred, 12)

    def test_cylinder_along_a_grid_axis(self):
        c = np.array([0.0, 0.0, 0.0])
        r, w = 0.5, 0.8

        def pred(p):
            return (np.abs(p[:, 2]) <= w / 2.0) & (np.hypot(p[:, 0], p[:, 1]) <= r)

        self.check(Cylinder(c, (0.0, 0.0, 1.0), r, w), pred, 13)

    def test_box_turned(self):
        c = np.array([0.2, 0.1, -0.3])
        size = np.array([1.0, 0.4, 0.7])
        th = 0.6
        Rz = np.array([[np.cos(th), -np.sin(th), 0.0], [np.sin(th), np.cos(th), 0.0], [0.0, 0.0, 1.0]])
        ph = 0.3
        Rx = np.array([[1.0, 0.0, 0.0], [0.0, np.cos(ph), -np.sin(ph)], [0.0, np.sin(ph), np.cos(ph)]])
        R = Rz @ Rx

        def pred(p):
            local = (p - c) @ R
            return (np.abs(local) <= size / 2.0).all(axis=1)

        self.check(box(c, size, R), pred, 14)

    def test_box_along_the_axes(self):
        c = np.array([0.0, 0.5, 0.0])
        size = np.array([0.6, 1.0, 0.2])
        self.check(box(c, size), lambda p: (np.abs(p - c) <= size / 2.0).all(axis=1), 15)

    def test_prism(self):
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([1.2, 0.2, 0.3])
        d = (b - a) / np.linalg.norm(b - a)
        ey = np.cross(d, (0.0, 0.0, 1.0))
        ey /= np.linalg.norm(ey)
        ez = np.cross(d, ey)
        section = [(-0.2, -0.1), (0.3, -0.15), (0.35, 0.1), (0.0, 0.3), (-0.25, 0.15)]
        area_sign = sum(section[i][0] * section[(i + 1) % 5][1] - section[(i + 1) % 5][0] * section[i][1] for i in range(5))
        self.assertGreater(area_sign, 0.0, "the section must run counter-clockwise")
        length = float(np.linalg.norm(b - a))

        def pred(p):
            rel = p - a
            s = rel @ d
            y, z = rel @ ey, rel @ ez
            ok = (s >= 0.0) & (s <= length)
            for i in range(5):
                x0, y0 = section[i]
                x1, y1 = section[(i + 1) % 5]
                ok &= (x1 - x0) * (z - y0) - (y1 - y0) * (y - x0) >= 0.0
            return ok

        self.check(prism(a, b, section, ey, ez), pred, 16)


def small_grid(n: int = 20, dx: float = 0.01) -> Grid:
    return Grid(n, n, n, dx, np.array([-n * dx / 2.0] * 3))


class ThinPlate(unittest.TestCase):
    """3 mm between two node planes 10 mm apart: no node centre falls inside it."""

    def setUp(self) -> None:
        self.grid = small_grid()
        self.plate = box((0.0, 0.0, 0.0), (0.003, 0.1, 0.1))
        self.vox = voxelize([self.plate], self.grid)
        self.i, self.j, self.k = self.grid.ijk(self.vox.node)

    def test_no_solid_nodes(self):
        self.assertEqual(int((self.vox.mask == 1).sum()), 0)
        self.assertGreater(self.vox.n_links, 0)

    def test_cut_from_both_sides(self):
        c = C[self.vox.dir]
        downstream = (self.i == 10) & (c[:, 0] > 0)
        upstream = (self.i == 9) & (c[:, 0] < 0)
        self.assertGreater(int(downstream.sum()), 0)
        self.assertEqual(int(downstream.sum()), int(upstream.sum()))
        # nothing else: only the two planes beside it, and only the directions coming through it
        self.assertEqual(int(downstream.sum() + upstream.sum()), self.vox.n_links)

    def test_every_line_through_the_span_is_stopped(self):
        have = set(zip(self.i.tolist(), self.j.tolist(), self.k.tolist(), self.vox.dir.tolist()))
        for j in range(5, 15):
            for k in range(5, 15):
                self.assertIn((10, j, k, 1), have)
                self.assertIn((9, j, k, 2), have)

    def test_a_middle_node_has_all_five_links(self):
        mid = (self.i == 10) & (self.j == 10) & (self.k == 10)
        self.assertEqual(sorted(self.vox.dir[mid].tolist()), [1, 7, 9, 11, 13])

    def _away_from_the_rim(self) -> np.ndarray:
        # a link near the rim can enter through the plate's edge face, at another distance
        return (self.j >= 6) & (self.j <= 13) & (self.k >= 6) & (self.k <= 13)

    def test_q_values(self):
        # the face at +1.5 mm, the node at +5 mm, the link 10 mm along x whatever its direction
        inner = self._away_from_the_rim()
        self.assertGreater(int(inner.sum()), 100)
        self.assertTrue(np.allclose(self.vox.q[inner], 0.35, atol=1e-12))

    def test_q_values_stay_on_the_link(self):
        self.assertTrue((self.vox.q > 0.0).all() and (self.vox.q < 1.0).all())

    def test_the_grid_sees_the_area(self):
        rep = geometry_report(self.vox, samples_per_cell=4)
        self.assertEqual(rep["shapes_without_cut_links"], [])
        self.assertAlmostEqual(rep["projected_area_x_m2"], 0.01, delta=1e-4)
        self.assertLess(abs(rep["grid_projected_area_x_error_pct"]), 5.0)

    def test_interpolation_stays_on_each_own_side(self):
        L = link_arrays(self.vox, np.zeros(3), 1.0)
        # near links (q < 1/2): a = 2q and the second value comes from the next node away from the plate
        inner = self._away_from_the_rim()
        self.assertTrue(np.allclose(L.a[inner], 0.7, atol=1e-6))
        self.assertTrue(np.allclose(L.b[inner], 0.3, atol=1e-6))
        n2 = self.vox.node[inner] + (C[self.vox.dir[inner]] @ np.array(self.grid.strides, dtype=np.int64))
        side_of_node = self.grid.position(self.vox.node[inner])[:, 0] > 0.0
        side_of_second = self.grid.position(n2)[:, 0] > 0.0
        self.assertTrue(np.array_equal(side_of_node, side_of_second))
        self.assertEqual(L.interpolated_share, 1.0)


class SphereLinks(unittest.TestCase):
    """Every cut link of a sphere and its distance, by the quadratic written out here."""

    def setUp(self) -> None:
        self.dx = 0.01
        self.grid = Grid(24, 22, 26, self.dx, np.array([-0.117, -0.104, -0.131]))
        self.center = np.array([0.0023, -0.0041, 0.0071])
        self.radius = 0.0637
        self.vox = voxelize([Sphere(self.center, self.radius)], self.grid)

    def brute_force(self) -> dict[tuple[int, int], float]:
        g = self.grid
        out: dict[tuple[int, int], float] = {}
        ii, jj, kk = np.meshgrid(np.arange(g.nx), np.arange(g.ny), np.arange(g.nz), indexing="ij")
        ii, jj, kk = ii.ravel(), jj.ravel(), kk.ravel()
        p = g.origin + (np.stack([ii, jj, kk], axis=1) + 0.5) * self.dx
        r = p - self.center
        fluid = (r * r).sum(axis=1) > self.radius ** 2
        nodes = g.node_index(ii, jj, kk)
        for q in range(1, 19):
            d = -C[q].astype(float) * self.dx
            a = d @ d
            b = r @ d
            c = (r * r).sum(axis=1) - self.radius ** 2
            disc = b * b - a * c
            real = disc >= 0.0
            t = np.where(real, (-b - np.sqrt(np.maximum(disc, 0.0))) / a, np.inf)
            cut = fluid & real & (t > 0.0) & (t < 1.0)
            for n, tt in zip(nodes[cut], t[cut]):
                out[(int(n), q)] = float(tt)
        return out

    def test_the_same_links(self):
        got = {(int(n), int(q)) for n, q in zip(self.vox.node, self.vox.dir)}
        self.assertEqual(got, set(self.brute_force()))

    def test_q_values(self):
        want = self.brute_force()
        for n, q, t in zip(self.vox.node, self.vox.dir, self.vox.q):
            self.assertAlmostEqual(float(t), want[(int(n), int(q))], places=11)

    def test_hit_points_lie_on_the_sphere(self):
        p = self.grid.position(self.vox.node)
        hit = p - (self.vox.q[:, None] * C[self.vox.dir]) * self.dx
        self.assertLess(np.abs(np.linalg.norm(hit - self.center, axis=1) - self.radius).max(), 1e-12)

    def test_no_solid_neighbour_without_a_link(self):
        g = self.grid
        have = set((self.vox.node * 19 + self.vox.dir).tolist())
        stride = C @ np.array(g.strides, dtype=np.int64)
        for s in np.flatnonzero(self.vox.mask == 1).tolist():
            for q in range(1, 19):
                target = s + int(stride[q])
                if self.vox.mask[target] == 0:
                    self.assertIn(target * 19 + q, have)

    def test_solid_nodes_are_the_inside_ones(self):
        g = self.grid
        solid = np.flatnonzero(self.vox.mask == 1)
        p = g.position(solid)
        self.assertTrue((np.linalg.norm(p - self.center, axis=1) <= self.radius).all())
        volume = len(solid) * self.dx ** 3
        exact = 4.0 / 3.0 * np.pi * self.radius ** 3
        self.assertLess(abs(volume - exact) / exact, 0.05)


class Ground(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = Grid(12, 12, 12, 1.0, np.array([-6.0, -6.0, 0.0]))

    def _down_links(self, vox):
        _, _, k = self.grid.ijk(vox.node)
        return (k == 0) & (C[vox.dir, 2] > 0)

    def test_a_shape_below_the_ground_plane_leaves_the_ground_to_answer(self):
        vox = voxelize([box((0.0, 0.0, -2.0), (8.0, 8.0, 4.0))], self.grid, ground=True)
        self.assertEqual(int(self._down_links(vox).sum()), 0)

    def test_a_shape_nearer_than_the_ground_keeps_its_links(self):
        # top face at z = 0.3: the first row at z = 0.5 meets it after 0.2 of a vertical link
        vox = voxelize([box((0.0, 0.0, 0.2), (8.0, 8.0, 0.2))], self.grid, ground=True)
        down = self._down_links(vox)
        self.assertGreater(int(down.sum()), 0)
        self.assertTrue(np.allclose(vox.q[down], 0.2, atol=1e-12))

    def test_the_grid_with_a_ground_starts_on_the_plane(self):
        self.assertEqual(float(self.grid.position(np.array([self.grid.node_index(0, 0, 0)]))[0, 2]), 0.5)


class LinkArrays(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = Grid(16, 16, 16, 1.0, np.array([-8.0, -8.0, -8.0]))
        self.vox = voxelize([Sphere((0.2, 0.1, -0.3), 3.3)], self.grid)
        self.N = self.grid.n_total
        self.stride = C @ np.array(self.grid.strides, dtype=np.int64)

    def test_the_slots_and_factors(self):
        L = link_arrays(self.vox, np.zeros(3), 1.0)
        order = np.argsort(self.vox.shape, kind="stable")
        node, q, t = self.vox.node[order], self.vox.dir[order], self.vox.q[order]
        opp = OPP[q]
        self.assertTrue(np.array_equal(L.slot_a.astype(np.int64), opp * self.N + node))
        self.assertTrue(np.array_equal(L.slot_d.astype(np.int64), q * self.N + node - self.stride[q]))
        self.assertTrue(np.allclose(L.a + L.b, 1.0, atol=1e-6))
        far = t >= 0.5
        self.assertTrue(np.allclose(L.a[far], 0.5 / t[far], atol=1e-6))
        self.assertTrue(np.array_equal(L.slot_b[far].astype(np.int64), (q * self.N + node)[far]))
        near = ~far
        self.assertTrue(np.allclose(L.a[near], 2.0 * t[near], atol=1e-6))
        self.assertTrue(np.array_equal(L.slot_b[near].astype(np.int64), (opp * self.N + node + self.stride[q])[near]))

    def test_without_interpolation_it_is_plain_bounce_back(self):
        L = link_arrays(self.vox, np.zeros(3), 1.0, interpolate=False)
        self.assertTrue(np.all(L.a == 1.0))
        self.assertTrue(np.all(L.b == 0.0))
        self.assertEqual(L.interpolated_share, 0.0)

    def test_a_wall_velocity_enters_with_its_factor(self):
        sphere = Sphere((0.2, 0.1, -0.3), 3.31)
        sphere.velocity = lambda p: np.tile(np.array([2.0, -1.0, 0.5]), (len(p), 1))
        vox = voxelize([sphere], self.grid)
        self.assertGreater(float(vox.q.min()), 0.0)
        scale = 0.03
        L = link_arrays(vox, np.zeros(3), scale)
        u = np.array([2.0, -1.0, 0.5]) * scale
        raw = 6.0 * W[vox.dir] * (C[vox.dir] @ u) * np.where(vox.q >= 0.5, 0.5 / vox.q, 1.0)
        # each node's links share out their sum, so a moving wall adds no mass at the node
        nodes, inv = np.unique(vox.node, return_inverse=True)
        net = np.bincount(inv, weights=raw) / np.bincount(inv)
        self.assertTrue(np.allclose(L.dh, raw - net[inv], atol=1e-6))
        self.assertLess(np.abs(np.bincount(inv, weights=L.dh.astype(float))).max(), 1e-6)
        self.assertEqual(len(nodes), len(net))

    def test_chunks_stay_inside_one_group(self):
        shapes = [Sphere((-4.0, 0.0, 0.0), 1.7, group="front"), Sphere((4.0, 0.0, 0.0), 1.7, group="rear")]
        vox = voxelize(shapes, self.grid)
        L = link_arrays(vox, np.zeros(3), 1.0)
        self.assertEqual(vox.groups, ["front", "rear"])
        node_x = self.grid.position(self.grid.node_index(*self.grid.ijk(vox.node)))[:, 0]
        self.assertGreater(len(L.chunk_group), 1)
        # links keep the order of the group, and the chunk table covers them all exactly once
        self.assertEqual(int(L.chunk_start[0]), 0)
        self.assertEqual(int(L.chunk_start[-1]), vox.n_links)
        self.assertTrue((np.diff(L.chunk_start.astype(np.int64)) > 0).all())
        stream = L.slot_a.astype(np.int64) % self.N
        pos_x = self.grid.position(stream)[:, 0]
        for c, grp in enumerate(L.chunk_group):
            part = pos_x[int(L.chunk_start[c]):int(L.chunk_start[c + 1])]
            self.assertTrue((part < 0).all() if grp == 0 else (part > 0).all())
        self.assertEqual(len(node_x), vox.n_links)


class Mapping(unittest.TestCase):
    def test_grid_index_round_trip(self):
        g = Grid(7, 5, 4, 0.5, np.array([1.0, 2.0, 3.0]))
        ii, jj, kk = np.meshgrid(np.arange(g.nx), np.arange(g.ny), np.arange(g.nz), indexing="ij")
        n = g.node_index(ii.ravel(), jj.ravel(), kk.ravel())
        i, j, k = g.ijk(n)
        self.assertTrue(np.array_equal(i, ii.ravel()) and np.array_equal(j, jj.ravel()) and np.array_equal(k, kk.ravel()))
        self.assertTrue(np.allclose(g.position(n)[0], np.array([1.25, 2.25, 3.25])))
        self.assertEqual(len(np.unique(n)), g.nx * g.ny * g.nz)

    def test_units_keep_the_reynolds_number(self):
        flow = Flow(speed_ms=30.0, rho=1.2, nu=1.5e-5, u_lattice=0.05)
        dx = 0.02
        u = Units.of(flow, dx)
        self.assertAlmostEqual(flow.u_lattice * dx / u.dt, flow.speed_ms, places=9)
        self.assertAlmostEqual(u.nu_lattice * dx * dx / u.dt, flow.nu, places=12)
        self.assertAlmostEqual(u.tau0, 0.5 + 3.0 * u.nu_lattice, places=14)
        # a lattice force of one is rho dx^4 / dt^2 in newtons
        self.assertAlmostEqual(u.force_N, 1.2 * dx ** 4 / u.dt ** 2, places=9)

    def test_ground_friction_is_the_log_law_at_the_first_row(self):
        # u / u_tau = ln(z / z0) / kappa at z = dx / 2, with cf = 2 (u_tau / u)^2
        for dx, z0 in ((0.02, 1e-3), (0.0125, 6e-7)):
            want = 2.0 * (0.41 / np.log(0.5 * dx / z0)) ** 2
            self.assertAlmostEqual(ground_cf(dx, z0), want, places=12)
        # the first row inside the roughness: the log is held at ln 1.5, not taken at or below zero
        self.assertAlmostEqual(ground_cf(0.01, 0.1), 2.0 * (0.41 / np.log(1.5)) ** 2, places=12)
        self.assertGreater(ground_cf(0.02, 1e-3), ground_cf(0.02, 1e-5), "rougher is more friction")

    def test_sponge_profile(self):
        s = sponge_profile(40, 6, 10, 0.1)
        self.assertAlmostEqual(float(s[0]), 0.1, places=6)
        self.assertAlmostEqual(float(s[-1]), 0.1, places=6)
        self.assertTrue(np.all(s[6:29] == 0.0))
        self.assertTrue(np.all(np.diff(s[:7]) < 0.0))
        self.assertTrue(np.all(np.diff(s[29:]) > 0.0))
        self.assertTrue(np.all(sponge_profile(10, 0, 0, 0.1) == 0.0))


if __name__ == "__main__":
    unittest.main()
