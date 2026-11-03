"""The memory rules of the shell solves: the in-process caches stay small, the convergence levels go to
other processes only within frame_shell.MEMORY_BUDGET, and in this process each level is solved (and its
factors freed) before the base level.
"""

import os
import unittest
from unittest import mock

from hiraku.solvers.k1_shell import cache, frame, mesh
from hiraku.solvers.shelf import fixtures, frame_shell


def _clear():
    mesh._MESHES.clear()
    frame._FRAMES.clear()


class FrameCache(unittest.TestCase):
    setUp = tearDown = staticmethod(_clear)

    def test_one_frame_at_most(self):
        lad = fixtures.ladder_design()
        for size in (40.0, 30.0, 25.0):
            frame.shared_frame(lad, size)
            self.assertLessEqual(len(frame._FRAMES), cache.FRAMES)
        print(f"  frames kept after three sizes: {len(frame._FRAMES)}")

    def test_old_frame_goes_before_the_new_is_made(self):
        lru = cache.LRU(1)
        lru.get_or_make("a", lambda: "A")
        seen = []
        lru.get_or_make("b", lambda: seen.append(len(lru)) or "B")
        self.assertEqual(seen, [0])

    def test_large_frame_not_kept(self):
        lad = fixtures.ladder_design()
        with mock.patch.object(cache, "FRAME_MAX_BYTES", 1):
            _, hit1 = frame.shared_frame(lad, 40.0)
            _, hit2 = frame.shared_frame(lad, 40.0)
        self.assertEqual((hit1, hit2, len(frame._FRAMES)), (False, False, 0))

    def test_demo_frame_estimate(self):
        self.assertTrue(cache.keep_frame(49_612))  # the demo frame at 10 mm: about 1.8 GB
        self.assertFalse(cache.keep_frame(4 * 49_612))  # and at 5 mm


class ParallelBudget(unittest.TestCase):
    def test_demo_levels_fit_beside_the_base(self):
        with mock.patch.object(frame_shell, "_memory", return_value=(60e9, 0.3e9)):
            ok, why = frame_shell.parallel_memory(49_612, [20.0, 5.0], 10.0)  # the 5 mm level is left out
        self.assertTrue(ok, why)
        self.assertIn("about 3.3 GB", why)

    def test_large_base_is_over_the_budget(self):
        with mock.patch.object(frame_shell, "_memory", return_value=(60e9, 0.3e9)):
            ok, why = frame_shell.parallel_memory(90_000, [20.0], 10.0)
        self.assertFalse(ok)
        self.assertIn("over the 5.0 GB", why)

    def test_small_levels_fit(self):
        with mock.patch.object(frame_shell, "_memory", return_value=(60e9, 0.3e9)):
            ok, _ = frame_shell.parallel_memory(2_000, [40.0, 10.0], 20.0)  # 2,000 + 500 + 8,000 elements
        self.assertTrue(ok)

    def test_free_memory_also_caps(self):
        with mock.patch.object(frame_shell, "_memory", return_value=(1e9, 0.3e9)):
            ok, _ = frame_shell.parallel_memory(6_000, [40.0, 10.0], 20.0)
        self.assertFalse(ok)

    def test_memory_can_be_read(self):
        free, used = frame_shell._memory()
        print(f"  free {free / 1e9 if free else 0:.1f} GB, this process {used / 1e9 if used else 0:.2f} GB")
        self.assertTrue(used is None or used > 0)


class LevelsHere(unittest.TestCase):
    def test_levels_solved_before_the_base(self):
        order = []
        with mock.patch.dict(os.environ, {"HIRAKU_SHELL_WORKERS": "0"}), \
                mock.patch.object(frame_shell, "solve_level",
                                  side_effect=lambda d, s, solve, base: order.append(s) or {"size": s}):
            lv = frame_shell.Levels(fixtures.ladder_design(), 20.0, {}, 6_000)
            self.assertEqual(order, [40.0, 10.0])  # done in the constructor, before analyze_shell of the base
            self.assertEqual([r["size"] for r in lv.results()], [40.0, 10.0])
        self.assertIn("one after another", lv.where)

    def test_processes_get_one_blas_thread(self):
        seen = {}

        class FakePool:
            def __init__(self, n):
                seen.update({k: os.environ.get(k) for k in frame_shell.THREAD_ENV})

            def apply_async(self, f, args):
                return None

            def terminate(self):
                pass

            def join(self):
                pass

        ctx = mock.Mock()
        ctx.Pool = FakePool
        with mock.patch.dict(os.environ, {"HIRAKU_SHELL_WORKERS": "2", "OPENBLAS_NUM_THREADS": "8"}), \
                mock.patch.object(frame_shell.multiprocessing, "get_context", return_value=ctx), \
                mock.patch.object(frame_shell, "parallel_memory", return_value=(True, "")):
            lv = frame_shell.Levels(fixtures.ladder_design(), 20.0, {}, 20_000)
            lv.close()
            self.assertEqual(os.environ["OPENBLAS_NUM_THREADS"], "8")  # put back after the processes started
        self.assertEqual(seen, frame_shell.THREAD_ENV)


if __name__ == "__main__":
    unittest.main()
