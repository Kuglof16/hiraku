"""K2 tests that run whole scenes: only with HIRAKU_SLOW_TESTS=1 (the tool checks they repeat also run
in tests/verification/test_tools_protocol.py on every run)."""

import os
import unittest

SLOW = unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "a long K2 scene: set HIRAKU_SLOW_TESTS=1")
