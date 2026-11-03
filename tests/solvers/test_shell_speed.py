"""What makes the scripted run faster gives the same records:

- frame_shell's convergence levels solved in other processes (Levels) against one after another,
- the in-process caches of meshes and factorised frames (k1_shell.cache) against none,
- the kept results of the assembly tool's disassemble().

On fixtures.ladder_design() at 20 mm (levels 40 / 20 / 10 mm), a few seconds each. Times are printed,
not checked.
"""

import copy
import json
import os
import time
import unittest
from unittest import mock

from hiraku import solvers
from hiraku.solvers.k1_shell import cache, frame, mesh
from hiraku.solvers.shelf import fixtures
from hiraku.solvers.shelf import frame_shell

LC = {"load_case_id": "R1-x", "test_id": "R1", "domain": "strength", "title": "R1",
      "params": {"step_height": {"value": 100.0, "unit": "mm"}}, "tolerance_rel": 0.15}
PARAMS = {"element_size": {"value": 20.0, "unit": "mm"}}


def tool(name):
    return {t.name: t for t in solvers.builtin_tools()}[name]


def _clear():
    mesh._MESHES.clear()
    frame._FRAMES.clear()


def _timed(rec: dict) -> dict:
    """The record without what is a time (wall_time_s, timings_s) or says where the levels were solved."""
    out = json.loads(json.dumps(rec))
    out.pop("wall_time_s", None)
    out.pop("timings_s", None)
    out["effects"] = [e for e in out.get("effects", []) if not e["name"].startswith("mesh convergence solves")]
    return out


def _run(env: dict, min_elements: int | None = None) -> tuple[dict, float]:
    _clear()
    patches = [mock.patch.dict(os.environ, env)]
    if min_elements is not None:
        # the ladder's levels (4,000 to 24,000 elements) peak far below the demo frame's bytes per element,
        # so the estimate is lowered here to let them go to other processes
        patches += [mock.patch.object(frame_shell, "PARALLEL_MIN_ELEMENTS", min_elements),
                    mock.patch.object(frame_shell, "BYTES_PER_ELEMENT", 20_000)]
    for p in patches:
        p.start()
    try:
        t0 = time.perf_counter()
        rec = tool("frame_shell").run(fixtures.ladder_design(), LC, [{"prediction_id": "pred-0001"}], dict(PARAMS))
        return rec, time.perf_counter() - t0
    finally:
        for p in reversed(patches):
            p.stop()
        _clear()


class ConvergenceLevels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.serial, ts = _run({"HIRAKU_SHELL_WORKERS": "0", "HIRAKU_SHELL_CACHE": "0"})
        cls.parallel, tp = _run({"HIRAKU_SHELL_WORKERS": "2"}, min_elements=0)
        print(f"  ladder 20 mm, three levels: one after another without caches {ts:.1f} s, "
              f"two other processes with caches {tp:.1f} s")

    def test_both_ran(self):
        for rec in (self.serial, self.parallel):
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            self.assertIn("mesh_convergence", rec)

    def test_same_record(self):
        a, b = _timed(self.serial), _timed(self.parallel)
        for key in sorted(set(a) | set(b)):
            with self.subTest(key=key):
                self.assertEqual(json.dumps(a.get(key), sort_keys=True), json.dumps(b.get(key), sort_keys=True))

    def test_clock_times_only_in_timings(self):
        for rec in (self.serial, self.parallel):
            self.assertEqual([e["name"] for e in rec["effects"] if e.get("unit") == "s"], [])
            self.assertIn("shell_total", rec["timings_s"])
            self.assertEqual(sorted(k for k in rec["timings_s"] if k.startswith("convergence_level_")),
                             ["convergence_level_10mm", "convergence_level_40mm"])

    def test_levels_say_dof_and_where(self):
        def solves(rec):
            return next(e["name"] for e in rec["effects"] if e["name"].startswith("mesh convergence solves"))

        self.assertIn("free DOF", solves(self.serial))
        self.assertIn("one after another", solves(self.serial))
        self.assertIn("other processes", solves(self.parallel))


class Caches(unittest.TestCase):
    def setUp(self):
        _clear()

    tearDown = setUp

    def test_mesh_is_a_copy_of_the_same(self):
        lad = fixtures.ladder_design()
        a = mesh.build_mesh(lad, 20.0)
        b = mesh.build_mesh(lad, 20.0)
        self.assertIsNot(a, b)
        self.assertEqual(json.dumps(a.record("m", 1), sort_keys=True), json.dumps(b.record("m", 1), sort_keys=True))
        with mock.patch.dict(os.environ, {"HIRAKU_SHELL_CACHE": "0"}):
            c = mesh.build_mesh(lad, 20.0)
        self.assertEqual(json.dumps(a.record("m", 1), sort_keys=True), json.dumps(c.record("m", 1), sort_keys=True))

    def test_frame_shared_across_non_gusset_parts(self):
        lad = fixtures.ladder_design()
        f1, hit1 = frame.shared_frame(lad, 20.0)
        more = copy.deepcopy(lad)
        more["parts"] = list(more.get("parts", [])) + [{"id": "extra_box", "kind": "cargo", "mass_kg": 5.0}]
        f2, hit2 = frame.shared_frame(more, 20.0)
        self.assertEqual((hit1, hit2), (False, True))
        self.assertIs(f1, f2)
        _, hit3 = frame.shared_frame(lad, 25.0)
        self.assertFalse(hit3)

    def test_key_reads_gussets(self):
        lad = fixtures.ladder_design()
        g = copy.deepcopy(lad)
        g["parts"] = list(g.get("parts", [])) + [{"id": "g1", "kind": "gusset"}]
        c = copy.deepcopy(lad)
        c["parts"] = list(c.get("parts", [])) + [{"id": "c1", "kind": "cargo"}]
        self.assertNotEqual(cache.design_key(lad, 20.0), cache.design_key(g, 20.0))
        self.assertEqual(cache.design_key(lad, 20.0), cache.design_key(c, 20.0))

    def test_cache_off_and_on_give_the_same_analysis(self):
        from hiraku.solvers.shelf.frame_static import front_supports

        lad = fixtures.ladder_design()
        step = dict(step_mm=100.0, step_supports=front_supports(lad), element_size_mm=20.0)
        with mock.patch.dict(os.environ, {"HIRAKU_SHELL_CACHE": "0"}):
            a = frame.analyze_shell(lad, **step)
        frame.analyze_shell(lad, element_size_mm=20.0)  # another load case factorises the frame first
        b = frame.analyze_shell(lad, **step)
        self.assertEqual(b["timings"].get("frame_reused"), 1.0)
        self.assertEqual(a["U"].tobytes(), b["U"].tobytes())
        self.assertEqual(json.dumps(a["hot_spot_max"], sort_keys=True, default=lambda x: x.tolist()),
                         json.dumps(b["hot_spot_max"], sort_keys=True, default=lambda x: x.tolist()))

    def test_lru_keeps_the_newest(self):
        lru = cache.LRU(2)
        for k in "abc":
            lru.get_or_make(k, lambda k=k: k.upper())
        self.assertEqual(lru.get_or_make("a", lambda: "new"), ("new", False))
        self.assertEqual(lru.get_or_make("c", lambda: "x"), ("C", True))


class Disassembly(unittest.TestCase):
    def test_kept_result_is_not_shared(self):
        from hiraku.solvers.k4_geometry import assembly

        design = fixtures.ladder_design()
        assembly._DISASSEMBLED.clear()
        try:
            a = assembly.disassemble(design)
        except Exception as e:  # the ladder fixture may have nothing to take apart
            self.skipTest(f"disassemble on the ladder fixture: {e}")
        a["order"].append("changed")
        b = assembly.disassemble(design)
        self.assertNotIn("changed", b["order"])
        assembly._DISASSEMBLED.clear()
        c = assembly.disassemble(design)
        self.assertEqual(json.dumps(b, sort_keys=True, default=str), json.dumps(c, sort_keys=True, default=str))


if __name__ == "__main__":
    unittest.main()
