"""fe.tubes: a design's frame as shells tube by tube with ties, against check 9, a beam model and K2's loads."""

import math
import os
import unittest

import numpy as np

from hiraku.designgen import space_frame_utv as sf
from hiraku.solvers.fe import checks, precheck, tubes
from hiraku.solvers.fe.beam import Beam
from hiraku.solvers.fe.connect import rigid_link
from hiraku.solvers.fe.model import FEModel, StaticSolver
from hiraku.solvers.fe.shell import shell_stresses

SLOW = unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "fine meshes: set HIRAKU_SLOW_TESTS=1")


def design(nodes, members, secs):
    return {"nodes": [{"id": k, "xyz_mm": [float(x) for x in v]} for k, v in nodes.items()],
            "members": [{"id": m, "node_a": a, "node_b": b, "section": s, "material": "st"} for m, a, b, s in members],
            "sections": secs, "materials": {"st": checks.STEEL}}


def round_sec(d, t):
    return {"shape": "round_tube", "dims_mm": {"d": d, "t": t}}


def rect_t(size, L=400.0, Lb=300.0, host=(40.0, 80.0, 2.3), branch=(30.0, 30.0, 2.0), P=(0.0, 0.0, -1000.0)):
    """checks.rect_t_joint(tied=True) built with tubes.frame_shells."""
    bh, hh, th = host
    bb, hb, tb = branch
    secs = {"h": {"shape": "rect_tube", "dims_mm": {"b": bh, "h": hh, "t": th}},
            "b": {"shape": "rect_tube", "dims_mm": {"b": bb, "h": hb, "t": tb}}}
    wall = (bh - th) / 2
    d = design({"A": (0, 0, 0), "M": (L / 2, 0, 0), "B": (L, 0, 0), "C": (L / 2, wall + Lb, 0)},
               [("h1", "A", "M", "h"), ("h2", "M", "B", "h"), ("br", "M", "C", "b")], secs)
    fs = tubes.frame_shells(d, size, facet=False, weld_strips=False)
    m = fs.model
    host, br = fs.tubes["h1+h2"], fs.tubes["br"]
    m.fix(host.ids[0], "all")
    m.fix(host.ids[-1], "all")
    tip = m.add_node([L / 2, wall + Lb, 0])
    m.add(rigid_link(tip, br.ids[-1], m.nodes))
    F = np.zeros((len(m.nodes), 6))
    F[tip, :3] = P
    sol = StaticSolver(m).solve(F)
    joint = np.array([L / 2, wall, 0])
    vm = []
    for t in fs.tubes.values():
        st = shell_stresses(sol, t.group)
        vm.append(st.von_mises("max")[np.linalg.norm(st.centres - joint, axis=1) <= 60.0])
    return sol.displacements[tip, :3].copy(), float(np.concatenate(vm).max()), len(m.nodes), fs


def oblique(ang, n_c=16, R=30.0, r=15.0, t=2.0, L=400.0, Lb=200.0, P=500.0):
    """checks.oblique_round_t built with tubes.frame_shells (corners on the circle, as check 9)."""
    size = 2 * np.pi * R / n_c
    a = np.radians(ang)
    dd = np.array([np.cos(a), np.sin(a), 0.0])
    d = design({"A": (-L / 2, 0, 0), "O": (0, 0, 0), "B": (L / 2, 0, 0), "C": Lb * dd},
               [("h1", "A", "O", "h"), ("h2", "O", "B", "h"), ("br", "O", "C", "b")],
               {"h": round_sec(2 * R + t, t), "b": round_sec(2 * r + t, t)})
    fs = tubes.frame_shells(d, size, facet=False, weld_strips=False)
    m = fs.model
    host, br = fs.tubes["h1+h2"], fs.tubes["br"]
    m.fix(np.r_[host.ids[0], host.ids[-1]], "all")
    tip = m.add_node(Lb * dd)
    m.add(rigid_link(tip, br.ids[-1], m.nodes))
    F = np.zeros((len(m.nodes), 6))
    F[tip, 2] = -P
    sol = StaticSolver(m).solve(F)
    return sol.displacements[tip, :3].copy(), fs, sol


def cantilever_shell(d, fixed_node, tip_node, size, P):
    """A frame of tubes clamped at the ring nearest one node, loaded at another through a rigid link to its
    ring; returns the tip displacement, the model and its solution."""
    fs = tubes.frame_shells(d, size)
    m = fs.model
    ring = lambda tb, end: tb.ids[0] if end == "a" else tb.ids[-1]  # noqa: E731
    clamp = [ring(fs.tubes[t], e) for t, e in fs.joints[fixed_node]["ends"]]
    m.fix(np.concatenate(clamp), "all")
    tb, e = fs.joints[tip_node]["ends"][0]
    tip = m.add_node(fs.xyz[tip_node])
    m.add(rigid_link(tip, ring(fs.tubes[tb], e), m.nodes))
    F = np.zeros((len(m.nodes), 6))
    F[tip, :3] = P
    sol = StaticSolver(m).solve(F)
    return sol.displacements[tip, :3].copy(), fs, sol


def cantilever_beam(d, fixed_node, tip_node, P, n=8):
    bm = tubes.beam_model(d, n)
    m, idx = bm["model"], bm["node"]
    m.fix(idx[fixed_node], "all")
    F = np.zeros((len(m.nodes), 6))
    F[idx[tip_node], :3] = P
    return StaticSolver(m).solve(F).displacements[idx[tip_node], :3].copy()


class Check9Test(unittest.TestCase):
    """Match 1: tubes.py's cut and tie give fe check 9's answers."""

    def test_square_rect_t(self):
        a = checks.rect_t_joint(10.0, tied=True)
        tip, vm, n_nodes, _ = rect_t(10.0)
        self.assertEqual(n_nodes, a["n_nodes"])  # the same grid
        np.testing.assert_allclose(tip, a["tip"], rtol=1e-8, atol=1e-9)
        self.assertAlmostEqual(vm, a["vm_max_near_joint"], delta=1e-6 * vm)

    def test_oblique_round_t(self):
        for ang in (60.0, 45.0):
            a = checks.oblique_round_t(ang, 16)
            tip, fs, sol = oblique(ang, 16)
            self.assertAlmostEqual(tip[2], a["tip"][2], delta=0.01 * abs(a["tip"][2]))  # rows round vs ceil
            gaps = [p["gap"] for c in fs.ties for p in c.projection]
            self.assertAlmostEqual(max(gaps), a["max_gap"], places=9)  # the same saddle on the same facets
            self.assertTrue(all(p["inside"] for c in fs.ties for p in c.projection))
            self.assertLess(precheck.equilibrium(sol)["residual"], 1e-8)
            self.assertLess(precheck.rigid_body_energy(fs.model)["constraint_residual"], 1e-12)


class JointKindsTest(unittest.TestCase):
    """The other joints: a mitre (kinked run), a corner whose host runs on past the node, a crossing X."""

    def test_mitre_against_beam(self):
        sec = {"c": round_sec(42.7, 2.3)}
        k = math.radians(20.0)
        d = design({"A": (0, 0, 0), "K": (400, 0, 0), "C": (400 + 400 * math.cos(k), 0, 400 * math.sin(k))},
                   [("m1", "A", "K", "c"), ("m2", "K", "C", "c")], sec)
        P = np.array([0.0, -300.0, -300.0])
        tip, fs, sol = cantilever_shell(d, "A", "C", 10.0, P)
        self.assertEqual({e["kind"] for t in fs.tubes.values() for e in t.ends.values()}, {"free", "mitre"})
        beam = cantilever_beam(d, "A", "C", P)
        np.testing.assert_allclose(tip, beam, rtol=0.05, atol=0.02 * np.linalg.norm(beam))
        self.assertLess(precheck.equilibrium(sol)["residual"], 1e-8)

    def test_corner_host_runs_on(self):
        sec = {"c": round_sec(42.7, 2.3), "b": round_sec(34.0, 2.3)}
        d = design({"A": (0, 0, 0), "K": (500, 0, 0), "C": (500, 400, 0)}, [("m1", "A", "K", "c"), ("m2", "K", "C", "b")],
                   sec)
        P = np.array([0.0, 0.0, -200.0])
        tip, fs, sol = cantilever_shell(d, "A", "C", 8.0, P)
        host = fs.tubes["m1"]
        self.assertEqual(host.ends["b"]["kind"], "host")
        # the stub reaches past the branch's footprint (radius 15.85 mm) by one element
        self.assertAlmostEqual(host.ends["b"]["ext"], 15.85 + 8.0, delta=0.6)
        self.assertGreater(fs.stub_mass_kg, 0.0)
        beam = cantilever_beam(d, "A", "C", P)
        # the shell's unreinforced corner gives (its wall ovalises; the beam joint is rigid): measured 1.34 x
        self.assertGreater(-tip[2], 1.1 * -beam[2])
        self.assertLess(-tip[2], 1.6 * -beam[2])
        self.assertLess(precheck.rigid_body_energy(fs.model)["constraint_residual"], 1e-12)

    def test_x_brace_halves_share_their_meeting_nodes(self):
        sec = {"c": round_sec(34.0, 2.3)}
        d = design({"A": (0, 0, 0), "B": (600, 600, 0), "C": (0, 600, 0), "D": (600, 0, 0), "X": (300, 300, 0)},
                   [("d1", "A", "X", "c"), ("d2", "X", "B", "c"), ("e1", "C", "X", "c"), ("e2", "X", "D", "c")], sec)
        fs = tubes.frame_shells(d, 10.0)
        self.assertIn("d1+d2", fs.tubes)  # the first diagonal runs through, the other is two branches
        self.assertEqual(fs.hosts["X"], "d1+d2")
        from hiraku.solvers.fe import quality

        q = quality.model_quality(fs.model)
        self.assertEqual(q["quality"]["topology"]["coincident_nodes"], 0)
        self.assertNotEqual(q["quality"]["verdict"], "fail")

    def test_refuses_a_branch_along_its_host(self):
        sec = {"c": round_sec(34.0, 2.3)}
        d = design({"A": (0, 0, 0), "B": (600, 0, 0), "X": (300, 0, 0), "C": (600, 40, 0)},
                   [("h1", "A", "X", "c"), ("h2", "X", "B", "c"), ("br", "X", "C", "c")], sec)
        with self.assertRaisesRegex(tubes.TubeMeshError, "under"):
            tubes.frame_shells(d, 10.0)


def t_joint_hot_spots(size):
    """k1_shell's T joint (test_k1_hot_spot.t_joint) on fe.tubes and on k1_shell: (tubes' hot spots, k1's)."""
    from tests.solvers.test_k1_hot_spot import solve_t_joint, t_joint

    d = t_joint()
    fs = tubes.frame_shells(d, size)
    m = fs.model
    host, br = fs.tubes["r1+r2"], fs.tubes["br"]
    m.fix(np.r_[host.ids[0], host.ids[-1]], "all")
    tip = m.add_node([300.0, -250.0, 0.0])
    m.add(rigid_link(tip, br.ids[-1], m.nodes))
    F = np.zeros((len(m.nodes), 6))
    F[tip, 2] = -100.0
    sol = StaticSolver(m).solve(F)
    return tubes.hot_spots(fs, sol), solve_t_joint(size)[1]


class HotSpotTest(unittest.TestCase):
    """The IIW hot spot on the tie lines (fe.tubes.hot_spots) by k1_shell's rule."""

    def test_rect_t_against_shared_nodes(self):
        # at 10 mm every ring node falls on a host node (the host's lines are evenly filled between the strip
        # and corner lines), so the joint is shared nodes and equals k1_shell's mesh: 105.74 and 56.14 MPa both
        from tests.solvers.test_k1_hot_spot import t_joint

        fs = tubes.frame_shells(t_joint(), 10.0)
        self.assertEqual(len(fs.ties), 0)
        hs, ref = t_joint_hot_spots(10.0)
        for side, tol in (("host", 1e-4), ("branch", 1e-4)):  # measured 2.8e-5 (node positions' rounding)
            a = tubes.hot_spot_max([h for h in hs if h["side"] == side])
            b = tubes.hot_spot_max([h for h in ref if h["side"] == side])
            self.assertLess(abs(abs(a["value"]) / abs(b["value"]) - 1.0), tol, side)
            near = min((h for h in ref if h["side"] == side and h["surface"] == a["surface"]),
                       key=lambda h: np.linalg.norm(h["position_mm"] - a["position_mm"]))
            self.assertLess(np.linalg.norm(near["position_mm"] - a["position_mm"]), 0.1)
        for h in hs:  # every pair read at 0.5 t and 1.5 t of its strip
            self.assertAlmostEqual(h["t_mm"], 2.3)

    def test_extrapolation_and_corner_squares(self):
        hs, _ = t_joint_hot_spots(10.0)
        for h in hs:
            self.assertAlmostEqual(1.5 * h["sigma_05t"] - 0.5 * h["sigma_15t"], h["value"], places=9)
        # along the branch's walls (40 mm mid-line minus t) nothing is read within 2 t of a corner: the
        # reads nearest a corner lie 2.5 t from it (the corner lines at t, 2t, 3t)
        br = [h for h in hs if h["side"] == "branch"]
        x = np.array([h["position_mm"][0] for h in br])
        corner = 300.0 + (40.0 - 2.3) / 2
        self.assertAlmostEqual(float(np.min(np.abs(x - corner)[x < corner])), 2.5 * 2.3, delta=1e-6)

    def test_oblique_round_t_crown_and_saddle(self):
        for ang in (90.0, 60.0, 45.0):
            a = np.radians(ang)
            dd = np.array([np.cos(a), np.sin(a), 0.0])
            d = design({"A": (-300, 0, 0), "O": (0, 0, 0), "B": (300, 0, 0), "C": 250 * dd},
                       [("h1", "A", "O", "h"), ("h2", "O", "B", "h"), ("br", "O", "C", "b")],
                       {"h": round_sec(42.7, 2.3), "b": round_sec(34.0, 2.3)})
            fs = tubes.frame_shells(d, 10.0)
            m = fs.model
            host, br = fs.tubes["h1+h2"], fs.tubes["br"]
            m.fix(np.r_[host.ids[0], host.ids[-1]], "all")
            tip = m.add_node(250 * dd)
            m.add(rigid_link(tip, br.ids[-1], m.nodes))
            F = np.zeros((len(m.nodes), 6))
            F[tip, 2] = -200.0
            sol = StaticSolver(m).solve(F)
            self.assertLess(precheck.equilibrium(sol)["residual"], 1e-8)
            hs = tubes.hot_spots(fs, sol)
            places = {h["place"].split(", ")[1].split(" of")[0] for h in hs if h["side"] == "host"}
            self.assertEqual(places, {"before", "after", "one side", "other side"}, ang)  # crown and saddle
            self.assertTrue(any(h["side"] == "branch" for h in hs))
            # the branch's strips are one wall thick square to the saddle cut: every pair along the ring is read
            br_hs = [h for h in hs if h["side"] == "branch"]
            self.assertEqual(len(br_hs), 2 * br.ids.shape[1], ang)
            top = tubes.hot_spot_max(hs)
            self.assertGreater(abs(top["value"]), 1.0)

    def test_rect_t_5_mm_partly_tied(self):
        # at 5 mm 10 of the ring's 48 nodes are tied: host 107.12 vs 106.33 MPa (+0.7 %), branch 55.58 vs
        # 56.24 MPa (-1.2 %); tubes' host 105.74 at 10 mm, 1.3 % between the two meshes
        hs10, _ = t_joint_hot_spots(10.0)
        hs, ref = t_joint_hot_spots(5.0)
        for side in ("host", "branch"):
            a = tubes.hot_spot_max([h for h in hs if h["side"] == side])
            b = tubes.hot_spot_max([h for h in ref if h["side"] == side])
            self.assertLess(abs(abs(a["value"]) / abs(b["value"]) - 1.0), 0.02, side)
        a10 = tubes.hot_spot_max([h for h in hs10 if h["side"] == "host"])
        a5 = tubes.hot_spot_max([h for h in hs if h["side"] == "host"])
        self.assertLess(abs(abs(a5["value"]) / abs(a10["value"]) - 1.0), 0.02)


def round_t_hot_spots(ang: float, size: float) -> tuple[float, float]:
    """Round T: 42.7 x 2.3 through tube (600 mm, held at both ends), 34 x 2.3 branch at `ang` deg to it (250 mm),
    200 N down at its end: the largest hot spot on the host side and on the branch side (MPa)."""
    a = np.radians(ang)
    dd = np.array([np.cos(a), np.sin(a), 0.0])
    d = design({"A": (-300, 0, 0), "O": (0, 0, 0), "B": (300, 0, 0), "C": 250 * dd},
               [("h1", "A", "O", "h"), ("h2", "O", "B", "h"), ("br", "O", "C", "b")],
               {"h": round_sec(42.7, 2.3), "b": round_sec(34.0, 2.3)})
    fs = tubes.frame_shells(d, size)
    m = fs.model
    host, br = fs.tubes["h1+h2"], fs.tubes["br"]
    m.fix(np.r_[host.ids[0], host.ids[-1]], "all")
    tip = m.add_node(250 * dd)
    m.add(rigid_link(tip, br.ids[-1], m.nodes))
    F = np.zeros((len(m.nodes), 6))
    F[tip, 2] = -200.0
    hs = tubes.hot_spots(fs, StaticSolver(m).solve(F))
    return (abs(tubes.hot_spot_max([h for h in hs if h["side"] == "host"])["value"]),
            abs(tubes.hot_spot_max([h for h in hs if h["side"] == "branch"])["value"]))


class RoundConvergenceTest(unittest.TestCase):
    """The round T's hot spots against the element size (the crown and saddle read on strip elements centred
    on the true points, at least HOT_SPOT_MIN_FACETS facets, rows a facet apart round the joint).

    Measured (MPa, 30 / 20 / 14 / 10 / 7 mm): 90 deg host 227.84 / 228.52 / 228.90 / 229.07 / 229.17, branch
    180.51 / 180.94 / 181.19 / 181.30 / 181.36; 60 deg host 170.46 / 171.06 / 171.29 / 171.41 / 171.48, branch
    138.27 / 138.73 / 138.91 / 139.00 / 139.05; 45 deg host 116.21 / 116.59 / 116.75 / 116.83 / 116.88, branch
    93.94 / 94.14 / 94.23 / 94.26 / 94.29."""

    def test_settles_with_the_element_size(self):
        for ang in (90.0, 60.0, 45.0):
            vals = [round_t_hot_spots(ang, s) for s in (30.0, 20.0, 14.0, 10.0)]
            for side in (0, 1):
                v = [x[side] for x in vals]
                steps = [abs(v[i + 1] - v[i]) / v[i + 1] for i in range(len(v) - 1)]
                self.assertLess(steps[-1], 0.005, (ang, side, v))  # the finest two within 0.5 %
                self.assertLessEqual(steps[-1], steps[0] + 1e-12, (ang, side, v))  # the steps shrink

    @SLOW
    def test_facet_count(self):
        # the facet count, not the element size, sets the read: 40 against 96 facets (14 mm), measured 90 deg
        # host 228.90 vs 236.43 (-3.2 %), branch 181.19 vs 194.06 (-6.6 %); 60 deg host -2.7 %, branch -2.4 %;
        # 45 deg host -0.4 %, branch +0.9 %. The chosen count reads up to about 7 % low (not on the safe side)
        old = tubes.HOT_SPOT_MIN_FACETS
        try:
            for ang in (90.0, 60.0):
                at40 = round_t_hot_spots(ang, 14.0)
                tubes.HOT_SPOT_MIN_FACETS = 96
                at96 = round_t_hot_spots(ang, 14.0)
                tubes.HOT_SPOT_MIN_FACETS = old
                for side in (0, 1):
                    self.assertLess(at40[side], at96[side])
                    self.assertLess(1.0 - at40[side] / at96[side], 0.08, (ang, side))
        finally:
            tubes.HOT_SPOT_MIN_FACETS = old


class SpaceFrameShellTest(unittest.TestCase):
    """Matches 2, 3 and 5 on the space-frame UTV (a coarse mesh; finer ones in the slow test)."""

    SIZE = 40.0

    @classmethod
    def setUpClass(cls):
        cls.d = sf.build()
        cls.st = tubes.k2_states(cls.d)
        cls.r = tubes.analyse(cls.d, cls.SIZE, states=cls.st)

    def test_the_k2_loads_balance(self):
        veh = self.st["veh"]
        items = tubes.sprung_items(self.d)
        self.assertAlmostEqual(sum(m for m, *_ in items), veh.m_s, places=9)  # K2's sprung body
        for case in ("static", "lift"):
            loads = tubes.body_loads(self.d, veh, self.st[case])
            res = tubes.resultant_of(loads, about=np.asarray(veh.cg_s) * 1000.0)
            total = sum(np.linalg.norm(l.force_N) for l in loads)
            self.assertLess(np.linalg.norm(res[:3]), 1e-9 * total)
            self.assertLess(np.linalg.norm(res[3:]), 1e-9 * total * 3000.0)
            mounts = [l for l in loads if l.kind == "mount"]
            self.assertEqual(len(mounts), 24)
            xyz = tubes.design_xyz(self.d)
            for l in mounts:  # K2's mount points come back to the hardpoints in the design's axes
                np.testing.assert_allclose(l.point_mm, xyz[l.carriers[0]], atol=1e-6)
        # standing still the mounts carry the sprung weight
        up = sum(l.force_N[2] for l in tubes.body_loads(self.d, veh, self.st["static"]) if l.kind == "mount")
        self.assertAlmostEqual(up, veh.m_s * 9.80665, delta=1e-3 * veh.m_s * 9.80665)

    def test_quality_and_solve(self):
        for case in ("static", "lift"):
            c = self.r["cases"][case]
            self.assertEqual(c["status"], "solved", c["reasons"])
            self.assertNotEqual(c["quality"]["verdict"], "fail")
            self.assertEqual(c["quality"]["topology"]["coincident_nodes"], 0)
            free = {x["quantity"]: x["value"] for x in c["results"]}["free_rigid_body_modes"]
            self.assertEqual(free, 0.0)
            self.assertLess(c["load_balance_residual"], 1e-8)  # the 3-2-1 holds take nothing
            self.assertLess(c["equilibrium_residual"], 1e-8)
            vm = c["von_mises_max"]
            self.assertGreater(vm["MPa"], 1.0)
            self.assertIn(vm["joint"], tubes.design_xyz(self.d))
        self.assertGreater(self.st["lift"]["t"], 0.0)
        self.assertEqual(self.r["n_ties"], sum(1 for _ in self.r["shells"].ties))

    def test_judged_values(self):
        # judged on the IIW hot spot at the tie lines (fe.tubes.hot_spots). Measured at 40 mm: standing still
        # 132.2 MPa at RQ1 (the front lower link's end; yield / hot spot 1.63), D0 129.4; at the lift-off
        # 231.2 MPa at RQ3 (the rear lower link's end), above the yield (0.93), D0 97.6. Recorded as found: the
        # rear lower link is to be reinforced
        yield_mpa = self.d["materials"][sf.MATERIAL]["yield_MPa"]
        st, lift = self.r["cases"]["static"], self.r["cases"]["lift"]
        for c in (st, lift):
            self.assertGreater(len(c["hot_spots"]), 1000)
        self.assertGreaterEqual(yield_mpa / abs(st["hot_spot_max"]["value"]), 1.5)
        self.assertIn(lift["hot_spot_max"]["joint"], ("RQ3", "LQ3"))
        self.assertTrue(lift["hot_spot_max"]["member"].startswith("rear_lower_link"))
        self.assertAlmostEqual(abs(lift["hot_spot_max"]["value"]), 231.2, delta=5.0)
        d0 = max(abs(h["value"]) for h in st["hot_spots"] if h["joint"] == "D0")
        self.assertLess(d0, 0.65 * yield_mpa)

    def test_shell_mass(self):
        from hiraku.solvers.k2_vehicle.mass import member_items

        rods = sum(m for m, *_ in member_items(self.d))
        shell = self.r["shell_mass_kg"]
        # the shell has the stubs and not the tube inside the joints: within 5 % of K2's rods
        self.assertAlmostEqual(shell, rods, delta=0.05 * rods)

    def test_against_the_beam_model(self):
        for case in ("static", "lift"):
            cb = tubes.compare_beam(self.d, self.r, case, self.st)
            self.assertLess(np.linalg.norm(cb["reaction_N"]), 1e-6 * self.r["cases"][case]["applied_force_sum_N"])
            self.assertGreater(cb["ratio"], 0.9)  # the shell's joints are softer, its tubes the same
            self.assertLess(cb["ratio"], 1.3)


@SLOW
class RearLinkRevisionTest(unittest.TestCase):
    """RQ3 (the rear lower link's end at the lift-off, above the yield with 34 x 2.3) can be brought under the
    yield by the revisable variable rear_link_tube_d. Measured at 40 mm, lift-off: Q3 231.2 (34 x 2.3), 156.5
    (42.7 x 2.3; the frame's largest then 182.7 MPa at LP3), 175.5 (48.6 x 3.2), 204.3 MPa (60.5 x 3.2): a
    stiffer link draws more load, so the hot spot rises again past 42.7. At the default 20 mm, lift-off (step 100
    mm): RQ3 233.2 (34 x 2.3); with 42.7 x 2.3 LQ3 158.1, RQ3 147.2 and the frame's largest 182.7 MPa at LP3."""

    def test_42_7_brings_every_hot_spot_under_the_yield(self):
        d = sf.build({"rear_link_tube_d": 42.7})
        yield_mpa = d["materials"][sf.MATERIAL]["yield_MPa"]
        r = tubes.analyse(d, 40.0)
        lift = r["cases"]["lift"]
        q3 = max(abs(h["value"]) for h in lift["hot_spots"] if h["joint"] in ("RQ3", "LQ3"))
        self.assertAlmostEqual(q3, 156.5, delta=5.0)
        for case in ("static", "lift"):
            self.assertLess(abs(r["cases"][case]["hot_spot_max"]["value"]), yield_mpa, case)


@SLOW
class SpaceFrameConvergenceTest(unittest.TestCase):
    """Match 4: the hot spot at D0 (the dash bar's middle, where the front upper links land) and the largest
    on finer meshes."""

    def test_hot_spot_settles(self):
        # 40 / 30 / 20 / 14 mm (10 mm, about 250,000 elements, is beyond the development PC's memory). Measured:
        # standing still D0 129.40 / 129.80 / 130.18 / 130.34, largest (RQ1) 132.17 / 132.46 / 132.64 / 132.74;
        # lift-off D0 97.62 / 97.87 / 98.08 / 98.11, largest (RQ3) 231.24 / 232.27 / 233.17 / 233.65 MPa
        d = sf.build()
        st = tubes.k2_states(d)
        series = {k: [] for k in ("D0 static", "max static", "D0 lift", "max lift")}
        for size in (40.0, 30.0, 20.0, 14.0):
            r = tubes.analyse(d, size, states=st)
            for case in ("static", "lift"):
                c = r["cases"][case]
                series[f"D0 {case}"].append(max(abs(h["value"]) for h in c["hot_spots"] if h["joint"] == "D0"))
                series[f"max {case}"].append(abs(c["hot_spot_max"]["value"]))
        print("  " + "; ".join(f"{k} {[round(v, 2) for v in vals]}" for k, vals in series.items()))
        for k, v in series.items():
            steps = [abs(v[i + 1] - v[i]) / v[i + 1] for i in range(len(v) - 1)]
            self.assertLess(steps[-1], 0.005, (k, v))  # the finest two within 0.5 %
            self.assertLessEqual(steps[-1], steps[0] + 1e-12, (k, v))  # the steps shrink


if __name__ == "__main__":
    unittest.main()
