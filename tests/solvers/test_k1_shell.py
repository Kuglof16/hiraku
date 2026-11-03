import copy
import json
import time
import unittest
from pathlib import Path

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.sparse.csgraph import connected_components

from hiraku import contract
from hiraku.solvers.k0_handcalc import sections
from hiraku.solvers.k0_handcalc.vehicle import DesignError, mass_summary
from hiraku.solvers.k1_frame import build as k1_build
from hiraku.solvers.k1_frame import to_design
from hiraku.solvers.k1_shell import checks, element, stress
from hiraku.solvers.k1_shell.mesh import build_mesh
from hiraku.solvers.k1_shell.solve import ShellModel, StaticSolver, dofs_of, reduction_matrix, spread_load
from hiraku.solvers.shelf import fixtures

DEMO = Path(__file__).resolve().parents[2] / "data" / "designs" / "offroad-4w-v1.json"


def demo_design() -> dict:
    with DEMO.open(encoding="utf-8") as f:
        return json.load(f)


def report(rows) -> None:
    for r in rows:
        err = (r["got"] - r["expected"]) / (abs(r["expected"]) or 1.0)
        print(f"  {r['case']} | {r['quantity']} | expected {r['expected']:.6g} | got {r['got']:.6g} | "
              f"error {err:+.3%} | {r['source'][:80]}")


def zero_energy_modes(model: ShellModel, k: int = 9) -> np.ndarray:
    """Lowest eigenvalues of K x = lambda M x with the rigid links applied and no supports."""
    T, _ = reduction_matrix(model.nodes, model.links, np.array([], int))
    K = (T.T @ model.stiffness() @ T).tocsc()
    M = (T.T @ sp.diags(model.mass_diagonal()) @ T).tocsc()
    return np.sort(spla.eigsh(K, k=k, M=M, sigma=-10.0, which="LM", return_eigenvectors=False))


class AxesTest(unittest.TestCase):
    def test_section_axes_match_k1_on_every_demo_member(self):
        d = demo_design()
        ms = build_mesh(d, 20.0)
        fm = k1_build(d)
        for m in d["members"]:
            T = fm.model.members[m["id"]].T()[:3, :3]
            ours = ms.member_axes[m["id"]]
            for row in range(3):
                self.assertAlmostEqual(abs(float(to_design(T[row]) @ ours[row])), 1.0, places=9,
                                       msg=f"{m['id']} local axis {row}")

    def test_vertical_and_inclined_members_too(self):
        d = fixtures.beam_design(500.0, {"shape": "rect_tube", "dims_mm": {"b": 30.0, "h": 50.0, "t": 2.0}})
        for b in ([0.0, 0.0, 500.0], [0.0, 0.0, -500.0], [300.0, 200.0, 400.0], [-300.0, 100.0, -200.0]):
            dd = copy.deepcopy(d)
            dd["nodes"][1]["xyz_mm"] = b
            T = k1_build(dd).model.members["m"].T()[:3, :3]
            ours = build_mesh(dd, 20.0).member_axes["m"]
            for row in range(3):
                self.assertAlmostEqual(abs(float(to_design(T[row]) @ ours[row])), 1.0, places=9)


class ElementTest(unittest.TestCase):
    def test_patch_tests_exact(self):
        rows, _ = checks.patch_tests()
        report(rows)
        self.assertTrue(all(r["pass"] for r in rows))

    def test_inplane_bending_does_not_lock(self):
        got, exp = checks.inplane_cantilever(8, 2)
        self.assertAlmostEqual(got / exp, 1.0, places=6)
        got1, _ = checks.inplane_cantilever(8, 1)  # one element through the depth
        self.assertAlmostEqual(got1 / exp, 1.0, places=6)
        q4, _ = checks.inplane_cantilever(8, 2, incompatible=False)
        print(f"  in-plane end moment, 8x2: incompatible modes {got / exp:.6f}, plain bilinear {q4 / exp:.4f} of M L^2/(2EI)")
        self.assertLess(q4 / exp, 0.7)  # what the incompatible modes remove

    def test_drilling_stiffness_barely_matters(self):
        base = checks.tube_cantilever(size=20.0)
        out = []
        for f in (element.DRILL_FACTOR * 1e-3, element.DRILL_FACTOR * 10.0):
            other = checks.tube_cantilever(size=20.0, drill_factor=f)
            for case in (0, 1):
                a, b = base["U"][6 * base["tip"] + 2 - case, case], other["U"][6 * other["tip"] + 2 - case, case]
                out.append(abs(b / a - 1.0))
            out.append(abs(other["U_torsion"] @ other["U_torsion"] / (base["U_torsion"] @ base["U_torsion"]) - 1.0))
        print(f"  drilling factor {element.DRILL_FACTOR:g} vs x1e-3 and x10: largest relative change {max(out):.2e}")
        self.assertLess(max(out), 1e-3)

    def test_rigid_body_motion_has_no_energy_on_a_distorted_rotated_element(self):
        xyz = np.array([[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [11.0, 9.0, 0.0], [-1.0, 10.0, 0.0]]])
        R = np.linalg.qr(np.random.default_rng(1).normal(size=(3, 3)))[0]
        xyz = xyz @ R.T
        K = element.global_stiffness_blocks(xyz, np.array([1.0]), np.array([2e5]), np.array([0.3]))[0]
        for omega in np.eye(3):
            u = np.concatenate([np.concatenate([np.cross(omega, p), omega]) for p in xyz[0]])
            self.assertLess(np.abs(K @ u).max(), 1e-8 * np.abs(K).max())
        for tr in np.eye(3):
            u = np.concatenate([np.concatenate([tr, np.zeros(3)]) for _ in range(4)])
            self.assertLess(np.abs(K @ u).max(), 1e-8 * np.abs(K).max())

    def test_lumped_mass_totals(self):
        res = checks.tube_cantilever(size=20.0)
        m = res["torsion_model"]
        diag = m.mass_diagonal()
        self.assertAlmostEqual(diag[0::6].sum() / m.total_mass(), 1.0, places=12)


class CantileverTest(unittest.TestCase):
    def test_rect_tube_bending_and_bredt_torsion(self):
        rows = checks.tube_cantilever_records(checks.tube_cantilever(size=10.0))
        report(rows)
        self.assertTrue(all(r["pass"] for r in rows))

    def test_cantilever_converges(self):
        tips, errs = [], []
        for size in (40.0, 20.0, 10.0):
            rows = checks.tube_cantilever_records(checks.tube_cantilever(size=size, torsion=False))
            d = rows[0]
            tips.append(d["got"])
            errs.append(abs(d["got"] / d["expected"] - 1.0))
        steps = np.abs(np.diff(tips))
        print(f"  tip deflection, mesh 40/20/10 mm: {[f'{t:.5f}' for t in tips]} mm; error vs beam formula "
              f"{[f'{e:.3%}' for e in errs]}; change between meshes {steps}")
        self.assertLess(steps[1], steps[0])
        self.assertLess(errs[-1], 0.03)

    def test_stress_along_the_top_wall_follows_the_moment(self):
        res = checks.tube_cantilever(size=10.0)
        st = stress.element_stresses(res["model"], res["U"][:, 0])
        sxx = st.in_plane((1.0, 0.0, 0.0), "top")[:, 0]
        H = res["h"] - res["t"]
        t, idx, vals = stress.along_line(st.centres, sxx, (0.0, -5.0, H / 2), (1000.0, -5.0, H / 2), 1.0)
        self.assertGreater(len(t), 50)
        sec = res["section"]
        mid = (t > 200) & (t < 800)
        expect = 1000.0 * (1000.0 - t[mid]) * (res["h"] / 2) / sec["Iz"]
        self.assertLess(np.max(np.abs(vals[mid] / expect - 1.0)), 0.03)

    def test_round_tube_cantilever(self):
        d, t, L, P = 48.6, 2.3, 1000.0, 1000.0
        design = fixtures.beam_design(L, {"shape": "round_tube", "dims_mm": {"d": d, "t": t}})
        ms = build_mesh(design, 10.0)
        self.assertEqual(len(ms.end_rings[("m", "a")]) % 4, 0)
        model = ShellModel.from_mesh(ms, design)
        tip = model.add_node((L, 0.0, 0.0))
        model.links += [(int(n), tip) for n in ms.end_rings[("m", "b")]]
        F = np.zeros(model.n_dof)
        F[6 * tip + 2] = -P
        U = StaticSolver(model, dofs_of(ms.end_rings[("m", "a")])).solve(F)
        sec = sections.round_tube(d, t)
        expect = P * L**3 / (3 * checks.E_ST * sec["Iz"]) + P * L / (checks.G_ST * sec["A"] / 2.0)
        print(f"  round tube {d}x{t} cantilever: tip {-U[6 * tip + 2]:.4f} mm, beam {expect:.4f} mm "
              f"(thin tube shear area A/2)")
        self.assertLess(abs(-U[6 * tip + 2] / expect - 1.0), 0.03)


class PlateTest(unittest.TestCase):
    def test_square_plate_converges_to_navier(self):
        errs = []
        for n in (4, 8, 16, 32):
            got, exp = checks.square_plate(n)
            errs.append(abs(got / exp - 1.0))
        print(f"  SS square plate centre deflection error, 4/8/16/32 per side: {[f'{e:.3%}' for e in errs]}")
        self.assertTrue(all(b < a for a, b in zip(errs[:3], errs[1:3])))
        self.assertLess(errs[2], 0.01)

    def test_hole_kt_converges_and_matches_heywood(self):
        vals = []
        for nq, nr in ((8, 10), (12, 14), (16, 20), (24, 28)):
            k = checks.holed_plate_kt(n_quarter=nq, n_radial=nr)
            vals.append(k["kt"])
        ref = k["kt_heywood_gross"]
        steps = np.abs(np.diff(vals))
        print(f"  Kt gross d/W 0.25: FE {[f'{v:.4f}' for v in vals]}, Heywood {ref:.4f}, change between meshes {steps}")
        self.assertTrue(np.all(steps[1:] < steps[:-1]))
        self.assertLess(abs(vals[-1] / ref - 1.0), 0.01)

    def test_small_hole_tends_to_kirsch(self):
        k = checks.holed_plate_kt(W=400.0, d=20.0, length=1200.0, n_quarter=16, n_radial=30, grading=1.15)
        print(f"  Kt gross d/W 0.05: FE {k['kt']:.4f}, Heywood {k['kt_heywood_gross']:.4f}, Kirsch 3")
        self.assertLess(abs(k["kt"] / k["kt_heywood_gross"] - 1.0), 0.03)


class DemoMeshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = demo_design()
        t0 = time.perf_counter()
        cls.mesh = build_mesh(cls.design, 10.0)
        cls.t_mesh = time.perf_counter() - t0
        cls.model = ShellModel.from_mesh(cls.mesh, cls.design)

    def test_counts_and_edges(self):
        ms = self.mesh
        print(f"  demo at 10 mm: {ms.n_nodes} nodes, {ms.n_elements} elements, {6 * ms.n_nodes} DOF")
        self.assertLessEqual(ms.edge_lengths().max(), 10.0 + 1e-9)
        self.assertEqual(ms.links, [])
        self.assertEqual(len(ms.element_member), ms.n_elements)
        self.assertEqual(set(ms.element_member), {m["id"] for m in self.design["members"]})
        kinds = ms.joint_kinds
        self.assertEqual({k for k, v in kinds.items() if v == "L"}, {"LF", "LE", "RF", "RE"})
        self.assertEqual({k for k, v in kinds.items() if v == "T"}, {"LA", "LS1", "LS2", "LB", "RA", "RS1", "RS2", "RB"})

    def test_t_joint_rings_are_wall_nodes(self):
        ms = self.mesh
        rail_nodes = {mid: set() for mid in ("L", "R")}
        for e, mid in zip(ms.elements, ms.element_member):
            if mid.startswith("rail_"):
                rail_nodes[mid[5]].update(int(i) for i in e)
        for node, rings in ms.shared_rings.items():
            for member, ring, patch in rings:
                self.assertTrue(set(ring.tolist()) <= set(patch.tolist()), f"{node} {member}")
                self.assertTrue(set(ring.tolist()) <= rail_nodes[node[0]], f"{node} {member}")
                self.assertEqual(len(ring), len(ms.end_rings[(member, "a" if node[0] == "L" else "b")]))

    def test_normals_point_out_and_elements_are_flat(self):
        R, _, c, warp = element.local_frames(self.model.element_xyz())
        self.assertLess(warp.max(), 1e-9)
        nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in self.design["nodes"]}
        mem = {m["id"]: m for m in self.design["members"]}
        for k, mid in enumerate(self.mesh.element_member):
            ax = self.mesh.member_axes[mid]
            rel = c[k] - nodes[mem[mid]["node_a"]]
            rad = rel - (rel @ ax[0]) * ax[0]
            self.assertGreater(rad @ R[k, 2], 0.0)

    def test_connected(self):
        ms = self.mesh
        n = ms.n_nodes
        rows = np.repeat(np.arange(ms.n_elements), 4)
        A = sp.coo_matrix((np.ones(rows.size), (rows, ms.elements.ravel())), shape=(ms.n_elements, n)).tocsr()
        ncomp, _ = connected_components(A.T @ A, directed=False)
        self.assertEqual(ncomp, 1)

    def test_mass_against_the_beam_model(self):
        d = self.design
        shell = self.model.total_mass() * 1000.0
        beam = mass_summary(d)["frame_mass_kg"]
        # per unit length the shell (mid-line perimeter x t) and the beam (exact A) are the same for a
        # rect tube; the difference is length: rails extended at L joints, cross members cut back at the rail walls
        sec = {k: v["dims_mm"] for k, v in d["sections"].items()}
        rho = d["materials"]["stkr400"]["density_kg_m3"] * 1e-9
        rail, cross = sec["rt40x80x2.3"], sec["rt40x40x2.3"]
        A = {k: sections.rect_tube(v["b"], v["h"], v["t"])["A"] for k, v in sec.items()}
        perim = {k: 2 * (v["b"] - v["t"] + v["h"] - v["t"]) * v["t"] for k, v in sec.items()}
        for k in A:
            self.assertAlmostEqual(A[k], perim[k], places=9)
        n_L = sum(1 for v in self.mesh.joint_kinds.values() if v == "L")
        n_ends = 2 * sum(1 for m in d["members"] if m["section"] == "rt40x40x2.3")
        ext = (cross["b"] - cross["t"]) / 2.0
        cut = (rail["b"] - rail["t"]) / 2.0
        predicted = rho * (A["rt40x80x2.3"] * ext * n_L - A["rt40x40x2.3"] * cut * n_ends)
        print(f"  demo frame mass: shell {shell:.4f} kg, beam {beam:.4f} kg, difference {shell - beam:+.4f} kg "
              f"(predicted from the joint lengths {predicted:+.4f} kg)")
        self.assertAlmostEqual(shell - beam, predicted, places=6)

    def test_stations(self):
        ms = self.mesh
        props = {m["id"]: m for m in self.design["members"]}
        sec = self.design["sections"]
        for mid in ("rail_L_AS1", "cross_A"):
            sel = np.array([m == mid for m in ms.element_member])
            dims = sec[props[mid]["section"]]["dims_mm"]
            yz = ms.station_yz_mm[sel]
            H, B = dims["h"] - dims["t"], dims["b"] - dims["t"]
            on_wall = np.isclose(np.abs(yz[:, 0]), H / 2) | np.isclose(np.abs(yz[:, 1]), B / 2)
            self.assertTrue(np.all(on_wall))
            self.assertGreaterEqual(ms.station_s_mm[sel].min(), -1e-9)

    def test_record_fits_the_contract(self):
        rec = self.mesh.record("mesh-0001", self.design["design_version"])
        self.assertEqual(len(rec["elements"]), self.mesh.n_elements)
        json.dumps(rec)
        contract.validate("mesh", rec)
        contract.validate("field", stress.field_record("mesh-0001", np.zeros(self.mesh.n_elements), "max", "von Mises"))

    def test_frame_mesh_entry_point(self):
        from hiraku import solvers

        rec = solvers.frame_mesh(self.design)  # the caller (analyze) adds the mesh_id the store gives it
        self.assertNotIn("mesh_id", rec)
        contract.validate("mesh", dict(rec, mesh_id="mesh-0001"))
        self.assertEqual((len(rec["nodes_mm"]), len(rec["elements"])), (self.mesh.n_nodes, self.mesh.n_elements))

    def test_assemble_factor_solve_time(self):
        model = ShellModel.from_mesh(self.mesh, self.design)
        t0 = time.perf_counter()
        model.stiffness()
        t1 = time.perf_counter()
        ring = self.mesh.end_rings[("cross_A", "a")]
        solver = StaticSolver(model, dofs_of(ring))
        t2 = time.perf_counter()
        F = np.stack([spread_load(model.nodes, self.mesh.joints[n], force=(0.0, 0.0, -1000.0), n_dof=model.n_dof)
                      for n in ("LE", "RE", "LF")], axis=1)
        U = solver.solve(F)
        t3 = time.perf_counter()
        st = stress.element_stresses(model, U[:, 0])
        vm = st.von_mises("max")
        print(f"  demo at 10 mm: mesh {self.t_mesh:.2f} s, assemble {t1 - t0:.2f} s, reduce+factor {t2 - t1:.2f} s "
              f"({model.timings['n_dof_free']} DOF), solve 3 cases {t3 - t2:.3f} s; max von Mises case 0 {vm.max():.1f} MPa")
        self.assertTrue(np.all(np.isfinite(U)))
        self.assertLess(t3 - t0, 120.0)
        R = solver.reactions(U[:, 0], F[:, 0])
        fixed = dofs_of(ring, "xyz")
        self.assertAlmostEqual(R[fixed][2::3].sum(), 1000.0, places=4)


class DemoRigidModesTest(unittest.TestCase):
    def test_free_free_has_six_zero_energy_modes(self):
        d = demo_design()
        model = ShellModel.from_mesh(build_mesh(d, 20.0), d)
        vals = zero_energy_modes(model)
        print(f"  demo at 20 mm, free-free lowest eigenvalues (1/s^2): {np.array2string(vals, precision=3)}")
        self.assertTrue(np.all(np.abs(vals[:6]) < 1e-3))
        self.assertGreater(vals[6], 1e3)


def n_components(ms) -> int:
    rows = np.repeat(np.arange(ms.n_elements), ms.elements.shape[1])
    A = sp.coo_matrix((np.ones(rows.size), (rows, ms.elements.ravel())), shape=(ms.n_elements, ms.n_nodes)).tocsr()
    return connected_components(A.T @ A, directed=False)[0]


def nodes_of(ms, prefix: str) -> set[int]:
    return {int(i) for e, m in zip(ms.elements, ms.element_member) if m.startswith(prefix) for i in e}


T_NODES = {"LA", "LS1", "LS2", "LB", "RA", "RS1", "RS2", "RB"}


class MountTest(unittest.TestCase):
    """Wheel mounts: the rail's walls within +- h (80 mm) of each support's frame node."""

    def test_demo_mounts(self):
        d = demo_design()
        ms = build_mesh(d, 10.0)
        xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in d["nodes"]}
        self.assertEqual(set(ms.mounts), {"LA", "RA", "LB", "RB"})
        for fn, nset in ms.mounts.items():
            self.assertEqual(ms.mount_half_mm[fn], 80.0)
            rail = nodes_of(ms, f"rail_{fn[0]}_")
            dx = np.abs(ms.nodes[:, 0] - xyz[fn][0])
            want = {i for i in rail if dx[i] <= 80.0 + 1e-9}
            self.assertEqual(set(nset.tolist()), want)
            p = ms.nodes[nset]
            self.assertAlmostEqual(np.ptp(p[:, 0]), 160.0, places=9)  # grid lines at +- h
            self.assertAlmostEqual(np.ptp(p[:, 1]), 40.0 - 2.3, places=9)  # all four walls
            self.assertAlmostEqual(np.ptp(p[:, 2]), 80.0 - 2.3, places=9)
            el = ms.elements[ms.mount_elements[fn]]
            self.assertTrue(np.all(np.isin(el, nset)))
            self.assertTrue(all(ms.element_member[e].startswith("rail_") for e in ms.mount_elements[fn]))
            area = element.element_area(element.local_frames(ms.nodes[el])[1]).sum()
            self.assertAlmostEqual(area, 160.0 * 2 * (40.0 - 2.3 + 80.0 - 2.3), places=6)
        self.assertEqual(build_mesh(fixtures.ladder_design(), 20.0).mounts, {})  # supports at corners: joint nodes


class ThroughMeshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.butt = build_mesh(demo_design(), 10.0)
        cls.design = demo_design()
        cls.design["variables"]["cross_members_through"] = {"value": 1, "unit": "1"}
        cls.mesh = build_mesh(cls.design, 10.0)

    def test_joints_and_record(self):
        ms = self.mesh
        self.assertEqual(set(ms.through_joints), T_NODES)
        self.assertEqual({k for k, v in ms.joint_kinds.items() if v == "L"}, {"LF", "LE", "RF", "RE"})
        self.assertIn("Through joints", " ".join(ms.notes))
        self.assertIn("beam model (K1) is unchanged", " ".join(ms.notes))
        self.assertLessEqual(ms.edge_lengths().max(), 10.0 + 1e-9)
        self.assertEqual(ms.links, [])
        self.assertEqual(n_components(ms), 1)
        used = np.zeros(ms.n_nodes, bool)
        used[ms.elements.ravel()] = True
        self.assertTrue(used.all())  # the nodes inside the holes are gone
        contract.validate("mesh", ms.record("mesh-0001", 1))

    def test_cross_member_reaches_the_far_wall(self):
        ms = self.mesh
        for node in ms.through_joints:
            rings = ms.shared_rings[node]
            rail = nodes_of(ms, f"rail_{node[0]}_")
            self.assertEqual(len(rings), 2, node)  # near wall (the hole's edge) and far wall
            ys = []
            for member, ring, patch in rings:
                self.assertTrue(set(ring.tolist()) <= rail, f"{node} {member}")
                self.assertTrue(set(ring.tolist()) <= set(patch.tolist()))
                ys.append(float(np.mean(ms.nodes[ring, 1])))
            self.assertAlmostEqual(abs(ys[0] - ys[1]), 40.0 - 2.3, places=9)  # inner and outer rail wall
        # the near wall inside the footprint is open: no rail element is centred there
        R, _, c, warp = element.local_frames(ms.nodes[ms.elements])
        self.assertLess(warp.max(), 1e-9)
        rail = np.array([m.startswith("rail_L_") for m in ms.element_member])
        inner = rail & np.isclose(c[:, 1], 350.0 - 18.85)
        for x in (0.0, -450.0, -1000.0, -1700.0):
            hole = inner & (np.abs(c[:, 0] - x) < 18.85) & (np.abs(c[:, 2] - 380.0) < 18.85)
            self.assertFalse(hole.any(), x)

    def test_mass_change_is_the_extension_less_the_holes(self):
        d = self.design
        rho = d["materials"]["stkr400"]["density_kg_m3"] * 1e-9
        m = lambda ms: ShellModel.from_mesh(ms, d).total_mass() * 1000.0  # noqa: E731
        Bc, t = 40.0 - 2.3, 2.3
        per_joint = rho * (4 * Bc * t * (40.0 - 2.3) - Bc * Bc * t)  # walls through the rail, less the hole
        self.assertAlmostEqual(m(self.mesh) - m(self.butt), 8 * per_joint, places=9)

    def test_too_wide_a_branch_stays_a_t_joint(self):
        lad = fixtures.ladder_design()
        lad["variables"] = {"cross_members_through": {"value": 1, "unit": "1"}}
        ms = build_mesh(lad, 20.0)
        self.assertEqual(ms.through_joints, [])
        self.assertIn("not through", " ".join(ms.notes))
        with self.assertRaisesRegex(DesignError, "0 or 1"):
            build_mesh(dict(lad, variables={"cross_members_through": {"value": 2, "unit": "1"}}), 20.0)


class GussetMeshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hiraku import designgen
        cls.design = designgen.regenerate(demo_design(), {"joints": "gusset"})
        cls.gussets = {p["id"]: p for p in cls.design["parts"] if p["kind"] == "gusset"}
        cls.mesh = build_mesh(cls.design, 10.0)

    def test_plates_are_meshed_and_welded(self):
        ms = self.mesh
        self.assertEqual(set(ms.element_member) - {m["id"] for m in self.design["members"]}, set(self.gussets))
        self.assertEqual(set(ms.part_material), set(self.gussets))
        self.assertEqual(ms.part_material["gusset_LS1_rear_lower"], "cross_S1")
        self.assertLessEqual(ms.edge_lengths().max(), 10.0 + 1e-9)
        self.assertEqual(n_components(ms), 1)
        tubes = {int(i) for e, m in zip(ms.elements, ms.element_member) if m not in self.gussets for i in e}
        R, xl, c, warp = element.local_frames(ms.nodes[ms.elements])
        self.assertLess(warp.max(), 1e-9)
        area = element.element_area(xl)
        member = np.array(ms.element_member)
        for gid, p in self.gussets.items():
            sel = member == gid
            g, _, t = p["box"]["size_mm"]
            self.assertAlmostEqual(area[sel].sum(), g * g / 2, places=9)
            self.assertEqual(ms.member_thickness_mm[gid], t)
            np.testing.assert_allclose((area[sel][:, None] * c[sel]).sum(axis=0) / area[sel].sum(),
                                       p["box"]["center_mm"], atol=1e-9)  # the plate's centroid is the box centre
            up = 1.0 if gid.endswith("upper") else -1.0
            np.testing.assert_allclose(R[sel, 2] @ np.array([0, 0, 1.0]), up)  # top side away from the cross member
            nodes = set(ms.elements[sel].ravel().tolist())
            shared = nodes & tubes
            # both legs: 2 * (2n) + 1 nodes with n = ceil(half the hypotenuse / 10) = ceil(42.4 / 10) = 5
            self.assertEqual(len(shared), 21, gid)
            on_leg = ms.nodes[sorted(shared)]
            self.assertTrue(np.all(np.isclose(on_leg[:, 2], p["box"]["center_mm"][2])))

    def test_record_and_model(self):
        ms = self.mesh
        rec = ms.record("mesh-0001", 1)
        contract.validate("mesh", rec)
        self.assertEqual(rec["member_thickness_mm"]["gusset_LA_front_upper"], 2.3)
        model = ShellModel.from_mesh(ms, self.design)
        butt = ShellModel.from_mesh(build_mesh(demo_design(), 10.0), demo_design())
        self.assertAlmostEqual((model.total_mass() - butt.total_mass()) * 1000.0,
                               sum(p["mass_kg"] for p in self.gussets.values()), places=9)

    def test_misplaced_gusset_is_refused(self):
        d = copy.deepcopy(self.design)
        p = next(x for x in d["parts"] if x["id"] == "gusset_LA_front_upper")
        p["box"]["center_mm"][0] += 5.0
        with self.assertRaisesRegex(DesignError, "not the centroid"):
            build_mesh(d, 10.0)
        p["box"]["center_mm"][0] -= 5.0
        p["carried_by"] = ["LF"]  # an L joint
        with self.assertRaisesRegex(DesignError, "no T joint"):
            build_mesh(d, 10.0)


class OtherJointsTest(unittest.TestCase):
    def frame(self):
        """A rect rail with an oblique rect branch and a square round-tube branch at its middle node."""
        return {
            "design_version": 1, "name": "joints", "variables": {}, "supports": [], "parts": [],
            "nodes": [{"id": "A", "xyz_mm": [0.0, 0.0, 0.0]}, {"id": "M", "xyz_mm": [500.0, 0.0, 0.0]},
                      {"id": "B", "xyz_mm": [1000.0, 0.0, 0.0]}, {"id": "C", "xyz_mm": [800.0, 300.0, 0.0]},
                      {"id": "D", "xyz_mm": [500.0, -400.0, 0.0]}],
            "members": [{"id": "r1", "node_a": "A", "node_b": "M", "section": "rt", "material": "st"},
                        {"id": "r2", "node_a": "M", "node_b": "B", "section": "rt", "material": "st"},
                        {"id": "ob", "node_a": "M", "node_b": "C", "section": "rt2", "material": "st"},
                        {"id": "rd", "node_a": "M", "node_b": "D", "section": "pipe", "material": "st"}],
            "sections": {"rt": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.3}},
                         "rt2": {"shape": "rect_tube", "dims_mm": {"b": 30.0, "h": 30.0, "t": 2.0}},
                         "pipe": {"shape": "round_tube", "dims_mm": {"d": 27.2, "t": 2.0}}},
            "materials": {"st": fixtures.STEEL},
        }

    def test_tied_joints_are_noted_and_hold(self):
        d = self.frame()
        ms = build_mesh(d, 20.0)
        self.assertEqual(ms.joint_kinds["M"], "tied")
        self.assertIn("rigid link", ms.record("mesh-0002", 1)["notes"])
        slaves = {s for s, _ in ms.links}
        for member in ("ob", "rd"):
            self.assertTrue(set(ms.end_rings[(member, "a")].tolist()) <= slaves)
        model = ShellModel.from_mesh(ms, d)
        vals = zero_energy_modes(model)
        self.assertTrue(np.all(np.abs(vals[:6]) < 1e-3))
        self.assertGreater(vals[6], 1.0)

    def test_bad_section_is_refused(self):
        d = self.frame()
        d["sections"]["rt2"] = {"shape": "rect_solid", "dims_mm": {"b": 30.0, "h": 30.0}}
        with self.assertRaises(DesignError) as cm:
            build_mesh(d, 20.0)
        self.assertIn("rect_tube", str(cm.exception))

    def test_tubes_crossing_through_a_node_are_refused(self):
        d = self.frame()
        d["nodes"].append({"id": "E", "xyz_mm": [500.0, 400.0, 0.0]})
        d["members"][3]["section"] = "rt2"
        d["members"][2]["node_b"] = "E"
        with self.assertRaises(DesignError) as cm:
            build_mesh(d, 20.0)
        self.assertIn("pass through each other", str(cm.exception))


class KnownAnswersTest(unittest.TestCase):
    def test_shell_known_answers_pass_quickly(self):
        t0 = time.perf_counter()
        rows = checks.shell_known_answers()
        dt = time.perf_counter() - t0
        report(rows)
        print(f"  shell_known_answers: {len(rows)} checks in {dt:.2f} s")
        self.assertTrue(all(r["pass"] for r in rows))
        for r in rows:
            self.assertEqual(set(r), {"case", "quantity", "expected", "got", "unit", "tolerance_rel", "pass", "source"})
        self.assertLess(dt, 15.0)


class LoadSpreadTest(unittest.TestCase):
    def test_spread_load_keeps_the_resultant(self):
        rng = np.random.default_rng(0)
        x = rng.normal(size=(12, 3)) * 50.0
        ids = np.arange(12)
        F, M, about = np.array([10.0, -20.0, 30.0]), np.array([1000.0, 500.0, -700.0]), np.array([5.0, 6.0, 7.0])
        f = spread_load(x, ids, F, M, about).reshape(-1, 6)[:, :3]
        self.assertTrue(np.allclose(f.sum(axis=0), F))
        self.assertTrue(np.allclose(np.cross(x - about, f).sum(axis=0), M))


if __name__ == "__main__":
    unittest.main()
