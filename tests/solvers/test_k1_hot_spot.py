"""Weld strips in the shell mesh and the structural hot-spot read-out (k1_shell.mesh, k1_shell.stress.hot_spots)."""

import copy
import json
import os
import time
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.solvers.k1_shell import element, stress
from hiraku.solvers.k1_shell.mesh import CORNER_LINES, build_mesh
from hiraku.solvers.k1_shell.solve import ShellModel, StaticSolver, dofs_of
from hiraku.solvers.shelf import fixtures

ROOT = Path(__file__).resolve().parents[2]
SLOW = os.environ.get("HIRAKU_SLOW_TESTS") == "1"  # the long demo runs (as in test_k1_shell)
TOL = 1e-6


def t_joint(branch_t: float = 2.3) -> dict:
    """A 40x80x2.3 tube from A to B (600 mm, node M in the middle) and a 40x40 tube from M to C ending square on
    its side wall (a T joint)."""
    return {
        "design_version": 1, "name": "t joint", "variables": {}, "supports": [], "parts": [],
        "nodes": [{"id": "A", "xyz_mm": [0.0, 0.0, 0.0]}, {"id": "M", "xyz_mm": [300.0, 0.0, 0.0]},
                  {"id": "B", "xyz_mm": [600.0, 0.0, 0.0]}, {"id": "C", "xyz_mm": [300.0, -250.0, 0.0]}],
        "members": [{"id": "r1", "node_a": "A", "node_b": "M", "section": "rail", "material": "st"},
                    {"id": "r2", "node_a": "M", "node_b": "B", "section": "rail", "material": "st"},
                    {"id": "br", "node_a": "M", "node_b": "C", "section": "cross", "material": "st"}],
        "sections": {"rail": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.3}},
                     "cross": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": branch_t}}},
        "materials": {"st": fixtures.STEEL},
    }


def small_ladder(through: bool = False) -> dict:
    """fixtures.ladder_design shortened, with 40x40x2.3 cross members on the 40x80x2.0 rails: L joints at the
    ends, T joints in the middle (through joints with `through`); rail and cross member walls differ."""
    d = fixtures.ladder_design(wheelbase=800.0, rail_y=250.0, wheel_y=350.0, payload_kg=100.0)
    d["sections"]["ct"] = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.3}}
    for m in d["members"]:
        if m["id"].startswith("cross"):
            m["section"] = "ct"
    if through:
        d["variables"] = {"cross_members_through": {"value": 1, "unit": "1"}}
    return d


def run_nodes(ms, members) -> np.ndarray:
    sel = np.isin(np.asarray(ms.element_member), list(members))
    return np.unique(ms.elements[sel].ravel())


def solve_t_joint(size: float):
    """The T joint held at both ends of the through tube, 100 N down at the branch's free end."""
    d = t_joint()
    ms = build_mesh(d, size)
    model = ShellModel.from_mesh(ms, d)
    tip = model.add_node((300.0, -250.0, 0.0))
    model.links += [(int(n), tip) for n in ms.end_rings[("br", "b")]]
    F = np.zeros(model.n_dof)
    F[6 * tip + 2] = -100.0
    fixed = dofs_of(np.concatenate([ms.end_rings[("r1", "a")], ms.end_rings[("r2", "b")]]))
    U = StaticSolver(model, fixed).solve(F)
    st = stress.element_stresses(model, U)
    return ms, stress.hot_spots(ms, st)


class ExtrapolationTest(unittest.TestCase):
    def test_factors_from_the_read_points(self):
        a, b = stress.hot_spot_factors()
        self.assertAlmostEqual(a, 1.5, places=12)
        self.assertAlmostEqual(b, -0.5, places=12)
        t = 2.3
        for A, B in ((100.0, -7.0), (-40.0, 3.5)):
            got = stress.extrapolate_hot_spot(A + B * 0.5 * t, A + B * 1.5 * t)
            self.assertAlmostEqual(float(got), A, places=9)  # a straight line gives back its value at the weld line

    def test_read_out_on_a_known_linear_field(self):
        """Per weld line: a surface stress whose component square to the line is A + B d (d from the line), with
        other components that must not leak in; every read point extrapolates to A (outer) and -A (inner)."""
        ms = build_mesh(small_ladder(through=True), 10.0)
        R, _, _, _ = element.local_frames(ms.nodes[ms.elements])
        centres = ms.nodes[ms.elements].mean(axis=1)
        A, B, C, D = 120.0, -9.0, 55.0, 17.0
        self.assertGreater(len(ms.weld_lines), 0)
        n_read = 0
        for wl in ms.weld_lines:
            u = (wl.p1 - wl.p0) / np.linalg.norm(wl.p1 - wl.p0)
            d = (centres - wl.p0) @ wl.away
            S = ((A + B * d)[:, None, None] * np.outer(wl.away, wl.away) + C * np.outer(u, u)
                 + D * (np.outer(wl.away, u) + np.outer(u, wl.away)))  # global tensor, in the strip's wall
            loc = np.einsum("eij,ejk,elk->eil", R, S, R)
            top = np.stack([loc[:, 0, 0], loc[:, 1, 1], loc[:, 0, 1]], axis=1)
            sig = np.stack([top, np.zeros_like(top), -top], axis=1)
            one = replace(ms, weld_lines=[wl])
            hs = stress.hot_spots(one, stress.ShellStress(sig, R, centres))
            for h in hs:
                self.assertAlmostEqual(h["value"], A if h["surface"] == "top" else -A, places=6)
                self.assertGreaterEqual(h["along_mm"], stress.CORNER_SKIP_T * wl.t - 1e-6)
                self.assertLess(np.linalg.norm(np.cross(h["position_mm"] - wl.p0, u)), 1e-6)  # on the weld line
            n_read += len(hs)
        self.assertGreater(n_read, 0)

    def test_mount_elements_are_not_read(self):
        ms = build_mesh(small_ladder(), 10.0)
        R, _, _, _ = element.local_frames(ms.nodes[ms.elements])
        centres = ms.nodes[ms.elements].mean(axis=1)
        st = stress.ShellStress(np.ones((ms.n_elements, 3, 3)), R, centres)
        every = stress.hot_spots(ms, st)
        ex = np.zeros(ms.n_elements, bool)
        ex[[h["elements"][0] for h in every[:10]]] = True
        left = stress.hot_spots(ms, st, ex)
        self.assertEqual(len(left), len(every) - len({h["elements"] for h in every[:10]}) * 2)
        self.assertFalse(any(ex[h["elements"][0]] or ex[h["elements"][1]] for h in left))


class StripMeshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = small_ladder()
        cls.mesh = build_mesh(cls.design, 10.0)
        cls.plain = build_mesh(cls.design, 10.0, weld_strips=False)
        cls.through = build_mesh(small_ladder(through=True), 10.0)

    def lines_beside(self, ms, wl) -> tuple[list[float], list[float]]:
        """Distances from the weld line of the grid lines on the strip's side (up to 3t) and along the line."""
        x = ms.nodes[run_nodes(ms, wl.members)]
        u = wl.p1 - wl.p0
        L = np.linalg.norm(u)
        u /= L
        rel = x - wl.p0
        on = (np.abs(rel @ wl.normal) < TOL) & (rel @ u > -TOL) & (rel @ u < L + TOL)
        d = rel[on] @ wl.away
        across = sorted({round(float(v), 6) for v in d if TOL < v < 3 * wl.t - TOL})
        along = sorted({round(float(v), 6) for v in rel[on & (np.abs(rel @ wl.away) < TOL)] @ u})
        return across, along

    def test_strip_lines_at_t_and_2t(self):
        for ms in (self.mesh, self.through):
            self.assertTrue(ms.weld_strips)
            for wl in ms.weld_lines:
                across, along = self.lines_beside(ms, wl)
                self.assertEqual(across, [round(wl.t, 6), round(2 * wl.t, 6)], (wl.joint, wl.side, wl.place))
                tc = 2.3  # the larger of the rail's (2.0) and the cross member's (2.3) walls
                L = round(float(np.linalg.norm(wl.p1 - wl.p0)), 6)
                for k in CORNER_LINES:
                    self.assertIn(round(k * tc, 6), along, (wl.joint, wl.side))
                    self.assertTrue(any(abs(v - (L - k * tc)) < 1e-5 for v in along), (wl.joint, wl.side))
        thick = {wl.side: wl.t for wl in self.mesh.weld_lines}
        self.assertEqual(thick, {"host": 2.0, "branch": 2.3})
        places = {wl.place for wl in self.through.weld_lines}
        self.assertTrue({"near wall", "far wall", "outside the near wall", "inside, at the near wall",
                         "inside, at the far wall"} <= places)

    def test_which_weld_lines(self):
        ms = self.mesh
        kinds = ms.joint_kinds
        by_joint = {}
        for wl in ms.weld_lines:
            by_joint.setdefault(wl.joint, []).append(wl)
        self.assertEqual(set(by_joint), {n for n, k in kinds.items() if k in ("T", "L")})
        for node, wls in by_joint.items():
            branch = [w for w in wls if w.side == "branch"]
            host = [w for w in wls if w.side == "host"]
            self.assertEqual(len(branch), 4, node)  # the branch's four walls
            # a T joint has a strip on all four sides of its footprint; at an L joint the rail ends at one of them
            self.assertEqual(len(host), 4 if kinds[node] == "T" else 3, node)

    def test_shapes_and_mass(self):
        for ms in (self.mesh, self.through):
            R, xl, c, warp = element.local_frames(ms.nodes[ms.elements])
            self.assertLess(warp.max(), 1e-9)
            self.assertGreater(element.element_area(xl).min(), 0.0)
            self.assertLessEqual(ms.edge_lengths().max(), 10.0 + 1e-9)
            ar = ms.aspect_ratios()
            print(f"  small ladder at 10 mm with weld strips: {ms.n_elements} elements, aspect ratio up to {ar.max():.2f}")
            self.assertLess(ar.max(), 10.0 / 2.0 + 1e-9)  # the thinnest strip (t = 2.0) by the element size
        m = lambda ms: ShellModel.from_mesh(ms, self.design).total_mass()  # noqa: E731
        self.assertAlmostEqual(m(self.mesh) / m(self.plain), 1.0, places=10)  # the strips only cut the walls
        self.assertEqual(self.plain.weld_lines, [])
        self.assertLess(self.plain.n_elements, self.mesh.n_elements)
        self.assertIn("Weld strips", " ".join(self.mesh.notes))
        contract.validate("mesh", self.mesh.record("mesh-0001", 1))


class MeshIndependenceTest(unittest.TestCase):
    def test_t_joint_hot_spot_10_and_5_mm(self):
        t0 = time.perf_counter()
        out = {}
        for size in (10.0, 5.0):
            ms, hs = solve_t_joint(size)
            out[size] = (hs, ms.n_elements)
        a = max(out[10.0][0], key=lambda h: abs(h["value"]))
        b = max(out[5.0][0], key=lambda h: abs(h["value"]))
        # the same read point at 5 mm (the joint is symmetric, so the largest may be its mirror image)
        same = next(h for h in out[5.0][0] if (h["side"], h["surface"]) == (a["side"], a["surface"])
                    and np.linalg.norm(h["position_mm"] - a["position_mm"]) < 1e-6)
        print(f"  T joint, 100 N at the branch end: hot spot {a['value']:+.2f} MPa at 10 mm ({out[10.0][1]} elements), "
              f"{same['value']:+.2f} MPa at 5 mm ({out[5.0][1]}) at the same point ({a['side']} side, {a['place']}); "
              f"largest at 5 mm {b['value']:+.2f} MPa; {time.perf_counter() - t0:.1f} s")
        self.assertLess(abs(a["value"] / same["value"] - 1.0), 0.03)
        self.assertLess(abs(abs(a["value"]) / abs(b["value"]) - 1.0), 0.03)

    @unittest.skipUnless(SLOW, "set HIRAKU_SLOW_TESTS=1 for the demo's R1 step at 10 and 5 mm (about 50 s)")
    def test_demo_r1_step_10_and_5_mm(self):
        from hiraku.contract import loading
        from hiraku.solvers.k1_shell.frame import analyze_shell
        d = loading.on_board(json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8")),
                             "two_cargo")
        top = {}
        for size in (10.0, 5.0):
            res = analyze_shell(d, step_mm=100.0, step_supports=["FL", "FR"], element_size_mm=size)
            top[size] = res["hot_spot_max"]
            print(f"  demo R1 step 100 mm, {size:g} mm: hot spot {top[size]['value']:+.1f} MPa at {top[size]['joint']} "
                  f"({top[size]['side']}, {top[size]['member']}), element peak "
                  f"{res['member_max'][res['worst_member']][0]:.1f} MPa, {res['mesh'].n_elements} elements, "
                  f"aspect ratio up to {res['mesh'].aspect_ratios().max():.2f}")
        a, b = top[10.0], top[5.0]
        self.assertEqual((a["joint"], a["side"], a["surface"]), (b["joint"], b["side"], b["surface"]))
        self.assertLess(abs(a["value"] / b["value"] - 1.0), 0.05)


class FrameShellHotSpotTest(unittest.TestCase):
    def test_result_in_the_contract(self):
        t = {x.name: x for x in solvers.builtin_tools()}["frame_shell"]
        self.assertEqual(t.describe()["predicts"][0]["quantity"], "max_hot_spot_stress")
        d = small_ladder()
        lc = {"load_case_id": "R1-rest", "test_id": "R1", "domain": "strength", "title": "R1", "params": {},
              "tolerance_rel": 0.15}
        t0 = time.perf_counter()
        rec = t.run(copy.deepcopy(d), lc, [{"prediction_id": "pred-0001"}], {})
        print(f"  frame_shell on the small ladder: {time.perf_counter() - t0:.1f} s")
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        hs = next(r for r in rec["results"] if r["quantity"] == "max_hot_spot_stress")
        self.assertEqual(hs["unit"], "MPa")
        self.assertIn(hs["where"], {m["id"] for m in d["members"]})
        self.assertGreater(hs["value"], 0.0)
        names = " ".join(e["name"] for e in rec["effects"])
        self.assertIn("structural hot spot max", names)
        self.assertIn("grows as the elements shrink", names)  # max_stress is said to be the corner peak
        top = next(e for e in rec["effects"] if e["name"].startswith("structural hot spot max"))
        self.assertAlmostEqual(top["value"], hs["value"], places=9)
        joined = " ".join(rec["assumptions"])
        self.assertIn("Hobbacher", joined)
        self.assertIn("確かめていない", joined)
        self.assertIn("max_stress", joined)
        field = next(f for f in rec["fields"] if f.get("label", "").startswith("構造的ホットスポット"))
        self.assertAlmostEqual(max(field["per_member"].values()), round(hs["value"], 2), places=6)


if __name__ == "__main__":
    unittest.main()
