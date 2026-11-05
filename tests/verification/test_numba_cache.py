"""numba's on-disk cache written by 8 processes at once (qa.numba_cache); skipped without numba."""

import importlib.util
import unittest

from qa import numba_cache


class Summary(unittest.TestCase):
    def test_a_wave_is_ok_only_without_failures_differences_or_cache_warnings(self):
        good = [{"exit": 0, "first_s": 1.0, "result": [1.0, 2.0], "warnings": []}] * 2
        self.assertTrue(numba_cache.summary("w", good)[1])
        self.assertFalse(numba_cache.summary("w", good + [{"exit": 1, "error": "x"}])[1])
        self.assertFalse(numba_cache.summary("w", good + [dict(good[0], result=[1.0, 3.0])])[1])
        self.assertFalse(numba_cache.summary("w", good + [dict(good[0], warnings=["cannot cache"])])[1])


@unittest.skipUnless(importlib.util.find_spec("numba"), "numba is not installed")
class ConcurrentCache(unittest.TestCase):
    def test_eight_processes_compile_and_then_load_the_cache(self):
        self.assertEqual(numba_cache.main(["--procs", "8"]), 0)


if __name__ == "__main__":
    unittest.main()
