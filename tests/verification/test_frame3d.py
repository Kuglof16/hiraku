"""qa.frame3d against closed forms, then against K1 (PyNite) on the demo frame.

K1 builds a PyNite model from the design. Here the same nodes, members, supports, springs and
loads are read from that model and solved by qa.frame3d. The section properties come from the
design's own section sizes through qa.known_answers, the materials from the design, and each
member's local axes from K1's rule (a horizontal member's local y is up). Both are
Euler-Bernoulli without shear deformation, so the displacements, reactions and member stresses
should agree to round-off. The stress is the measure K1 reports: the worst section corner, |N|/A
plus both bending stresses, with the torsional shear of the thin-walled cell (Bredt), as
sqrt(sigma^2 + 3 tau^2).
"""

import math
import unittest
from pathlib import Path

import numpy as np

from qa import known_answers as ka
from qa.frame3d import Frame3D

ROOT = Path(__file__).resolve().parents[2]
E, G = 205000.0, 205000.0 / 2.6
SEC = {"A": 400.0, "Iy": 2.0e5, "Iz": 1.0e5, "J": 1.5e5}


def cantilever(load, L=1000.0, w=(0.0, 0.0, 0.0), mid=False):
    f = Frame3D()
    f.node("a", (0, 0, 0))
    if mid:
        f.node("m", (L / 2, 0, 0))
    f.node("b", (L, 0, 0))
    f.member("ab", "a", "b", E, G, y_ref=(0, 1, 0), w=w, **SEC)
    f.support("a", [True] * 6)
    f.load("b", load)
    return f.solve()


class ClosedForms(unittest.TestCase):
    def test_tip_loads_on_a_cantilever(self):
        L, P = 1000.0, 100.0
        r = cantilever([0, P, 0, 0, 0, 0])
        self.assertAlmostEqual(r["U"]["b"][1] / ka.cantilever_tip_load(P, L, E, SEC["Iz"])["deflection"], 1.0, places=10)
        r = cantilever([0, 0, P, 0, 0, 0])
        self.assertAlmostEqual(r["U"]["b"][2] / ka.cantilever_tip_load(P, L, E, SEC["Iy"])["deflection"], 1.0, places=10)
        r = cantilever([P, 0, 0, 0, 0, 0])
        self.assertAlmostEqual(r["U"]["b"][0], P * L / (E * SEC["A"]), places=12)
        r = cantilever([0, 0, 0, 1e5, 0, 0])
        self.assertAlmostEqual(r["U"]["b"][3], 1e5 * L / (G * SEC["J"]), places=12)
        np.testing.assert_allclose(r["reactions"]["a"], [0, 0, 0, -1e5, 0, 0], atol=1e-6)

    def test_internal_forces_along_the_cantilever(self):
        L, P = 1000.0, 100.0
        r = cantilever([0, 0, P, 0, 0, 0], mid=True)
        for s in (0.0, 250.0, 500.0, 900.0):
            N, Vy, Vz, T, My, Mz = Frame3D.internal(r, "ab", s)
            self.assertAlmostEqual(abs(My), P * (L - s), places=6)
            self.assertAlmostEqual(abs(Vz), P, places=9)
            self.assertAlmostEqual(abs(Mz) + abs(N) + abs(T), 0.0, places=6)

    def test_uniform_load(self):
        L, w = 1000.0, 0.5
        # simply supported: pin at a (and torsion held), roller at b
        f = Frame3D()
        for n, x in (("a", 0.0), ("m", L / 2), ("b", L)):
            f.node(n, (x, 0, 0))
        f.member("ab", "a", "b", E, G, y_ref=(0, 1, 0), w=(0, -w, 0), **SEC)
        f.support("a", [True, True, True, True, False, False])
        f.support("b", [False, True, True, False, False, False])
        r = f.solve()
        exp = ka.simply_supported_uniform_load(w, L, E, SEC["Iz"])
        self.assertAlmostEqual(-r["U"]["m"][1] / exp["deflection"], 1.0, places=10)
        self.assertAlmostEqual(abs(Frame3D.internal(r, "ab", L / 2)[5]) / exp["moment"], 1.0, places=10)
        self.assertAlmostEqual(r["reactions"]["a"][1] / exp["reaction"], 1.0, places=10)

    def test_portal_sway_and_an_inclined_member(self):
        H, h, I = 1000.0, 2000.0, 1.0e6
        f = Frame3D()
        for n, xyz in (("a", (0, 0, 0)), ("b", (0, 0, h)), ("c", (3000, 0, h)), ("d", (3000, 0, 0))):
            f.node(n, xyz)
        col = dict(E=E, G=G, A=1e9, Iy=I, Iz=I, J=1e9)
        f.member("ab", "a", "b", y_ref=(1, 0, 0), **col)
        f.member("bc", "b", "c", y_ref=(0, 0, 1), **dict(col, Iy=I * 1e6, Iz=I * 1e6))
        f.member("dc", "d", "c", y_ref=(1, 0, 0), **col)
        for n in ("a", "d"):
            f.support(n, [True] * 6)
        f.support("b", [False, True, False, True, False, True])  # keep it in the x-z plane
        f.support("c", [False, True, False, True, False, True])
        f.load("b", [H, 0, 0, 0, 0, 0])
        r = f.solve()
        self.assertAlmostEqual(r["U"]["b"][0] / ka.portal_sway_fixed_bases(H, h, E, I), 1.0, places=5)
        # a member along a skew line deflects as the same cantilever
        d = np.array([1.0, 2.0, 2.0]) / 3.0
        f = Frame3D()
        f.node("a", (0, 0, 0))
        f.node("b", 1000.0 * d)
        f.member("ab", "a", "b", E, G, y_ref=(0, 0, 1), **SEC)
        f.support("a", [True] * 6)
        side = np.cross(d, [0, 0, 1])
        side /= np.linalg.norm(side)  # the local z axis: bending about local y, Iy
        f.load("b", np.r_[100.0 * side, 0, 0, 0])
        u = f.solve()["U"]["b"][:3]
        self.assertAlmostEqual(u @ side / ka.cantilever_tip_load(100.0, 1000.0, E, SEC["Iy"])["deflection"], 1.0, places=10)


# --- K1 ---------------------------------------------------------------------------------
DIRS = {"FX": 0, "FY": 1, "FZ": 2, "MX": 3, "MY": 4, "MZ": 5}


def k1_or_skip():
    import importlib
    try:
        return importlib.import_module("hiraku.solvers.k1_frame.analyze")
    except ImportError as e:
        raise unittest.SkipTest(f"K1 is not in this checkout: {e}")


def pynite_y(xi, xj):
    """K1's local y for a member from xi to xj in PyNite axes (Y up): up for a horizontal member,
    with an upward part for an inclined one, -X (+X going down) for a vertical one."""
    d = xj - xi
    if math.isclose(xi[0], xj[0]) and math.isclose(xi[2], xj[2]):
        return np.array([-1.0, 0, 0]) if xj[1] > xi[1] else np.array([1.0, 0, 0])
    if math.isclose(xi[1], xj[1]):
        return np.array([0.0, 1.0, 0.0])
    x = d / np.linalg.norm(d)
    proj = np.array([d[0], 0.0, d[2]])
    z = np.cross(proj, x) if xj[1] > xi[1] else np.cross(x, proj)
    return np.cross(z / np.linalg.norm(z), x)


def section(sec: dict) -> dict:
    """A, Iy, Iz, J in K1's local axes (y along h, vertical for a horizontal member), the corner
    distances and the torsional shear per torque, from the design's section sizes."""
    d = sec["dims_mm"]
    if sec["shape"] == "rect_tube":
        b, h, t = d["b"], d["h"], d["t"]
        s = ka.rect_tube(b, h, t)  # its Iy: h along z, i.e. bending about the axis along b
        bt = ka.bredt_torsion(1.0, 1.0, 1.0, b, h, t)
        return {"A": s["A"], "Iy": s["Iz"], "Iz": s["Iy"], "J": bt["J"], "cy": h / 2, "cz": b / 2,
                "tau_per_T": bt["shear_stress"], "round": False}
    if sec["shape"] == "round_tube":
        s = ka.round_tube(d["d"], d["t"])
        return {"A": s["A"], "Iy": s["Iy"], "Iz": s["Iz"], "J": s["J"], "cy": s["c"], "cz": s["c"],
                "tau_per_T": s["c"] / s["J"], "round": True}
    raise unittest.SkipTest(f"no independent section for {sec['shape']}")


def corner_stress(p: dict, N, My, Mz, T) -> float:
    if p["round"]:
        sigma = abs(N) / p["A"] + math.hypot(My, Mz) * p["cy"] / p["Iz"]
    else:
        sigma = abs(N) / p["A"] + abs(My) * p["cz"] / p["Iy"] + abs(Mz) * p["cy"] / p["Iz"]
    return math.sqrt(sigma**2 + 3 * (abs(T) * p["tau_per_T"]) ** 2)


def mirror(fm, design) -> tuple[Frame3D, dict]:
    """The K1 model's nodes, supports, springs and loads, solved with independent sections."""
    model = fm.model
    f = Frame3D()
    for n in model.nodes.values():
        f.node(n.name, (n.X, n.Y, n.Z))
        f.support(n.name, [n.support_DX, n.support_DY, n.support_DZ, n.support_RX, n.support_RY, n.support_RZ])
        for direction, P, case in n.NodeLoads:
            if case == "load":
                vec = np.zeros(6)
                vec[DIRS[direction]] = P
                f.load(n.name, vec)
    secs = {}
    for mid, m in model.members.items():
        assert not any(m.Releases) and not m.rotation, f"{mid}: releases or rotation are not mirrored"
        xi = np.array([m.i_node.X, m.i_node.Y, m.i_node.Z])
        xj = np.array([m.j_node.X, m.j_node.Y, m.j_node.Z])
        w = np.zeros(3)
        for direction, w1, w2, x1, x2, case, _sw in m.DistLoads:
            assert direction in ("FX", "FY", "FZ") and w1 == w2 and x1 == 0 and math.isclose(x2, m.L()), m.DistLoads
            w[DIRS[direction]] += w1
        dm = next((x for x in design["members"] if x["id"] == mid), None)
        if dm is None:  # K1's stiff link to a wheel centre: its own modelling, taken as it is
            p = {"A": m.section.A, "Iy": m.section.Iy, "Iz": m.section.Iz, "J": m.section.J}
            Em, Gm = m.material.E, m.material.G
        else:
            p = section(design["sections"][dm["section"]])
            mat = design["materials"][dm["material"]]
            Em = float(mat["E_MPa"])
            Gm = float(mat.get("G_MPa") or Em / (2 * (1 + float(mat["nu"]))))
            secs[mid] = (p, m)
        f.member(mid, m.i_node.name, m.j_node.name, Em, Gm, p["A"], p["Iy"], p["Iz"], p["J"], pynite_y(xi, xj), w)
    for s in model.springs.values():
        f.spring(s.i_node.name, s.j_node.name, s.ks)
    return f, secs


def designs():
    import yaml
    from hiraku import designgen
    import json
    v1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
    plan = yaml.safe_load((ROOT / "data" / "catalog" / "scripted.yaml").read_text(encoding="utf-8"))

    def revisions(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "revise" and isinstance(v, list):
                    yield from v
                else:
                    yield from revisions(v)
        elif isinstance(x, list):
            for v in x:
                yield from revisions(v)

    first = next(revisions(plan), None)
    out = {"v1": v1}
    if first and first.get("changes"):
        out["v2"] = designgen.regenerate(v1, first["changes"])
    return out


class AgainstK1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k1 = k1_or_skip()
        from hiraku.solvers.shelf.frame_static import front_supports
        cls.cases = []
        for name, design in designs().items():
            step = {"step_mm": 100.0, "step_supports": front_supports(design)}
            for load, kw in (("at rest", {}), ("step 100 mm", step)):
                cls.cases.append((f"{name} {load}", design, cls.k1.analyze(design, **kw)))

    def test_section_properties_match(self):
        for label, design, res in self.cases[:2]:
            fm = res["model"]
            for mid, (p, m) in mirror(fm, design)[1].items():
                with self.subTest(member=mid):
                    for key in ("A", "Iy", "Iz", "J"):
                        self.assertAlmostEqual(getattr(m.section, key) / p[key], 1.0, places=12, msg=key)

    def test_displacements_reactions_and_stresses(self):
        for label, design, res in self.cases:
            with self.subTest(case=label):
                fm = res["model"]
                f, secs = mirror(fm, design)
                r = f.solve()
                combo = "combo"
                U_k1 = {n.name: np.array([n.DX[combo], n.DY[combo], n.DZ[combo], n.RX[combo], n.RY[combo], n.RZ[combo]])
                        for n in fm.model.nodes.values()}
                scale_u = max(np.linalg.norm(u[:3]) for u in U_k1.values())
                scale_r = max(np.linalg.norm(u[3:]) for u in U_k1.values())
                du = max(np.linalg.norm(U_k1[n][:3] - r["U"][n][:3]) for n in U_k1)
                dr = max(np.linalg.norm(U_k1[n][3:] - r["U"][n][3:]) for n in U_k1)
                self.assertLess(du / scale_u, 1e-6, f"translations off by {du:.3g} mm of {scale_u:.3g}")
                self.assertLess(dr / scale_r, 1e-6, f"rotations off by {dr:.3g} rad of {scale_r:.3g}")
                for n, rx in r["reactions"].items():
                    node = fm.model.nodes[n]
                    theirs = np.array([node.RxnFX[combo], node.RxnFY[combo], node.RxnFZ[combo]])
                    self.assertLess(np.linalg.norm(theirs - rx[:3]), 1e-6 * max(1.0, res["weight_N"]), n)
                for mid, (p, m) in secs.items():
                    L = m.L()
                    mine = max(corner_stress(p, *(lambda q: (q[0], q[4], q[5], q[3]))(Frame3D.internal(r, mid, s)))
                               for s in np.linspace(0.0, L, 41))
                    theirs = res["stresses"][mid]["max"]
                    self.assertAlmostEqual(mine / theirs, 1.0, places=6, msg=f"{mid}: {mine:.6g} vs K1 {theirs:.6g} MPa")


if __name__ == "__main__":
    unittest.main()
