"""K2 tools keep their own checks once per tool in the process: asking two tools in turn (as the hooks' stage
lookup does for every call) does not compute either tool's checks again."""

import os
import unittest
from unittest import mock

from hiraku.solvers.k2_vehicle.shelf.base import K2Tool


class _Counting(K2Tool):
    def __init__(self, name):
        self.name = name
        self.calls = 0

    def own_checks(self):
        self.calls += 1
        return [{"case": self.name, "pass": True}]

    def _run(self, *a, **k):  # not run here
        raise NotImplementedError


class OwnChecksCacheTest(unittest.TestCase):
    def setUp(self):
        # the process's cache alone: the tests' disk cache (HIRAKU_CHECK_CACHE=1) would answer from an earlier run
        from hiraku.solvers.k2_vehicle.checks import CACHE_ENV

        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(CACHE_ENV, None)

    def test_two_tools_in_turn_compute_once_each(self):
        a, b = _Counting("cache_probe_a"), _Counting("cache_probe_b")
        for _ in range(3):
            self.assertEqual(a._own_checks(), ({"case": "cache_probe_a", "pass": True},))
            self.assertEqual(b._own_checks(), ({"case": "cache_probe_b", "pass": True},))
        self.assertEqual((a.calls, b.calls), (1, 1))


if __name__ == "__main__":
    unittest.main()
