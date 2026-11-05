"""qa.browser_pick: the pure core (no git, no browser)."""
import unittest

from qa.browser_pick import byte_ranges, only_args, parse_hunks, pick

A = "tests.web.test_a.ATest"
B = "tests.web.test_b.BTest"
C = "tests.web.test_c.CTest"
NOW = [A, B, C]

# web/js/app.js at the map's commit: two functions with a blank line between
APP = b"function f() {\n  return 1;\n}\n\nfunction g() {\n  return 2;\n}\n"
F = (0, APP.index(b"}\n") + 1)                       # function f
G = (APP.index(b"function g"), len(APP) - 1)          # function g
LINE = {i + 1: s for i, s in enumerate([0] + [k + 1 for k, c in enumerate(APP) if c == 10])}


def py_map(classes, dirty=False):
    return {"made": "2026-11-05T10:00:00", "commit": "abc", "tree": "def", "dirty": dirty, "classes": classes}


class TestBytes(unittest.TestCase):
    def test_parse_hunks(self):
        diff = "diff --git a/x b/x\n@@ -3 +3 @@\n-a\n+b\n@@ -7,0 +8,2 @@\n+c\n+d\n@@ -10,2 +12 @@\n"
        self.assertEqual(parse_hunks(diff), [(3, 1), (7, 0), (10, 2)])

    def test_ascii_lines(self):
        text = b"a\nbb\nccc\n"
        self.assertEqual(byte_ranges(text, [(2, 1)]), [(2, 5)])
        self.assertEqual(byte_ranges(text, [(1, 3)]), [(0, 9)])

    def test_multibyte_lines(self):
        # positions as the browser's coverage counts them: UTF-16 code units, not UTF-8 bytes
        s1, s2, s3 = "// 日本語\n", "const 名前 = 1;\n", "x\n"
        text = (s1 + s2 + s3).encode("utf-8")
        self.assertEqual((len(s1.encode("utf-8")), len(s1)), (13, 7))  # 3 bytes but 1 unit per character here
        self.assertEqual(byte_ranges(text, [(2, 1)]), [(len(s1), len(s1) + len(s2))])
        self.assertEqual(byte_ranges(text, [(3, 1)]), [(len(s1) + len(s2), len(s1 + s2 + s3))])

    def test_a_character_outside_the_bmp_counts_two_units(self):
        s1, s2 = "// 𠮷\n", "b\n"  # U+20BB7: a surrogate pair in UTF-16
        self.assertEqual(byte_ranges((s1 + s2).encode("utf-8"), [(2, 1)]), [(len(s1) + 1, len(s1) + 1 + len(s2))])

    def test_crlf_lines(self):
        # a Windows checkout serves CRLF: the \r is in the positions (qa.browser_pick.js_changes converts the blob)
        self.assertEqual(byte_ranges(b"a\r\nbb\r\nccc\r\n", [(2, 1)]), [(3, 7)])

    def test_insertion(self):
        text = "あ\nい\n".encode("utf-8")
        self.assertEqual(byte_ranges(text, [(1, 0)]), [(2, 2)])   # after line 1
        self.assertEqual(byte_ranges(text, [(0, 0)]), [(0, 0)])   # at the top
        self.assertEqual(byte_ranges(text, [(2, 0)]), [(4, 4)])   # at the end


class TestPick(unittest.TestCase):
    def setUp(self):
        self.map = py_map({A: ["web/js/app.js", "hiraku/a.py"], B: ["web/js/app.js"], C: ["hiraku/c.py"]})
        self.js = {A: {"web/js/app.js": [F]}, B: {"web/js/app.js": [G]}}

    def picked(self, files, ranges=None, js=None, sizes=None, m=None, now=NOW):
        return pick(now, self.map if m is None else m, self.js if js is None else js, files,
                    ranges or {}, sizes if sizes is not None else {"web/js/app.js": len(APP)})[0]

    def test_function_hit_and_miss(self):
        in_g = byte_ranges(APP, [(6, 1)])                      # "  return 2;"
        self.assertEqual(self.picked(["web/js/app.js"], {"web/js/app.js": in_g}), [B])
        in_f = byte_ranges(APP, [(2, 1)])
        self.assertEqual(self.picked(["web/js/app.js"], {"web/js/app.js": in_f}), [A])

    def test_insertion_between_functions(self):
        between = byte_ranges(APP, [(4, 0)])                    # after the blank line
        self.assertEqual(between, [(LINE[5], LINE[5])])
        self.assertEqual(self.picked(["web/js/app.js"], {"web/js/app.js": between}), [])
        inside = byte_ranges(APP, [(5, 0)])                     # after "function g() {"
        self.assertEqual(self.picked(["web/js/app.js"], {"web/js/app.js": inside}), [B])

    def test_whole_file_range(self):
        js = {A: {"web/js/app.js": [(0, len(APP), "")]}, B: {"web/js/app.js": [G]}}
        between = byte_ranges(APP, [(4, 0)])
        self.assertEqual(self.picked(["web/js/app.js"], {"web/js/app.js": between}, js=js), [A])
        # without the size: the largest recorded end stands for the file's end
        self.assertEqual(self.picked(["web/js/app.js"], {"web/js/app.js": between}, js=js, sizes={}), [A])

    def test_js_file_level_without_ranges(self):
        # no changed ranges (added after the map's commit, or not in the JS map): every test that served it
        self.assertEqual(self.picked(["web/js/app.js"]), [A, B])
        self.assertEqual(self.picked(["web/js/app.js"], {"web/js/app.js": [(0, 1)]}, js={}), [A, B])

    def test_method_names(self):
        m = py_map({A + ".test_one": ["web/js/app.js"], A + ".test_two": ["hiraku/a.py"], B: [], C: []})
        js = {A + ".test_one": {"web/js/app.js": [F]}}
        self.assertEqual(self.picked(["hiraku/a.py"], m=m, js=js), [A + ".test_two"])
        in_f = byte_ranges(APP, [(2, 1)])
        self.assertEqual(self.picked(["web/js/app.js"], {"web/js/app.js": in_f}, m=m, js=js), [A + ".test_one"])
        self.assertEqual(self.picked(["tests/web/test_a.py"], m=m, js=js), [A + ".test_one", A + ".test_two"])

    def test_always_patterns(self):
        for f in ["web/index.html", "web/style.css", "web/css/x/y.css", "web/cut.html", "web/vendor/three.module.js",
                  "web/js/three_loader.js", "tests/web/__init__.py", "tests/web/browser.py", "tests/web/_page.py",
                  "tests/web/helpers_cov.py"]:
            with self.subTest(f=f):
                picked, reasons = pick(NOW, self.map, self.js, [f], {})
                self.assertEqual(picked, sorted(NOW))
                self.assertIn(f, reasons[0])

    def test_own_file(self):
        self.assertEqual(self.picked(["tests/web/test_c.py"]), [C])

    def test_py_file(self):
        self.assertEqual(self.picked(["hiraku/a.py"]), [A])
        self.assertEqual(self.picked(["hiraku/c.py", "README.md"]), [C])
        self.assertEqual(self.picked(["README.md"]), [])

    def test_new_test(self):
        new = "tests.web.test_d.DTest"
        self.assertEqual(self.picked([], now=NOW + [new]), [new])
        picked, reasons = pick(NOW + [new], self.map, self.js, ["hiraku/c.py"], {})
        self.assertEqual(picked, [C, new])
        self.assertIn("1 new", reasons[0])

    def test_gone_test_left_out(self):
        picked, reasons = pick([A, B], self.map, self.js, ["hiraku/c.py"], {})
        self.assertEqual(picked, [])
        self.assertTrue(any("no longer found" in r for r in reasons))

    def test_missing_map(self):
        picked, reasons = pick(NOW, None, {}, [], {})
        self.assertEqual(picked, sorted(NOW))
        self.assertIn("no browser map", reasons[0])

    def test_dirty_map(self):
        picked, reasons = pick(NOW, py_map(self.map["classes"], dirty=True), self.js, ["README.md"], {})
        self.assertEqual(picked, sorted(NOW))
        self.assertIn("uncommitted", reasons[0])


class OnlyArgs(unittest.TestCase):
    NOW = ["tests.web.test_a.A", "tests.web.test_b.B", "tests.web.test_c.C"]

    def test_classes_of_the_picked_tests_as_only_arguments(self):
        # a method's name is longer than the class names qa.run_tests filters on: its class goes in
        self.assertEqual(only_args(["tests.web.test_b.B.test_x", "tests.web.test_a.A"], self.NOW),
                         ["--only", "tests.web.test_a.A", "--only", "tests.web.test_b.B"])

    def test_every_class_picked_is_the_whole_browser_suite(self):
        self.assertEqual(only_args(self.NOW, self.NOW), ["--only", "tests.web"])

    def test_nothing_picked_gives_no_arguments(self):
        self.assertEqual(only_args([], self.NOW), [])


if __name__ == "__main__":
    unittest.main()
