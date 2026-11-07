"""The saved validation record, replayed without a GPU, and one small case re-run live behind HIRAKU_SLOW_TESTS=1.

The replay checks the record against itself and against the code's references: every error is what the numbers
give, every validated body is inside its tolerance against a reference that was read. A body outside stays recorded
as not validated. The tolerances are frozen here as well as in the code, so widening one fails this test. A body
without runs yet is listed as not run, and says nothing either way.
"""

from __future__ import annotations

import json
import math
import os
import re
import unittest

from hiraku.cfd import benchmarks as b

# A folder on a drive (not the "s:/" of a link), a home folder, or a mail address. The home folder's name is joined so
# the repository's own scan does not read this line.
PRIVATE_PLACES = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|[\\/]" + "Users" + r"[\\/]|/home/|[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)

# The tolerances fixed before the runs. Changing one to make a body pass is what this line is here to stop.
FROZEN_TOLERANCE_PCT = {"sphere": 5.0, "sphere_turbulent": 10.0, "plate": 10.0, "cube": 10.0, "ahmed25": 10.0, "ahmed35": 10.0}
FROZEN_SETTLED_SPREAD_PCT = 5.0

# The record's own promise on a re-run: the same case on the same code comes back this close.
LIVE_REPEAT_PCT = 0.5


def load_record() -> dict:
    return json.loads(b.RECORD.read_text(encoding="utf-8"))


def difference_key(entry: dict) -> str:
    return "error_pct" if entry["reference_usable"] else "diff_from_unverified_pct"


def error_keys(node: object) -> list[tuple[str, float]]:
    """Every number under a key ending in error_pct, the way uncertainty.py walks the file."""
    found: list[tuple[str, float]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).endswith("error_pct") and isinstance(v, (int, float)) and not isinstance(v, bool):
                found.append((str(k), float(v)))
            else:
                found += error_keys(v)
    elif isinstance(node, list):
        for v in node:
            found += error_keys(v)
    return found


class References(unittest.TestCase):
    """What can be checked with no run at all."""

    def test_tolerances_are_the_frozen_ones(self):
        self.assertEqual(b.TOLERANCE_PCT, FROZEN_TOLERANCE_PCT)
        self.assertEqual(b.SETTLED_SPREAD_PCT, FROZEN_SETTLED_SPREAD_PCT)

    def test_every_reference_says_where_it_comes_from_and_how_far_it_was_checked(self):
        for key in b.WANTED_BODIES:
            body, re = ("sphere", float(key.split("_re")[1])) if key.startswith("sphere") else (key, 0.0)
            refs = b.references_for(body, re)
            self.assertTrue(refs, key)
            for ref in refs:
                self.assertTrue(ref.source, key)
                self.assertTrue(ref.url.startswith("https://"), key)
                self.assertIn(ref.verified, ("read", "secondary", b.UNVERIFIED), key)

    def test_a_value_nobody_read_says_so(self):
        self.assertEqual(b.REFERENCES["ahmed35"][0].verified, b.UNVERIFIED)
        ahmed25 = {r.value: r for r in b.REFERENCES["ahmed25"]}
        self.assertEqual(ahmed25[0.285].verified, b.UNVERIFIED)
        self.assertEqual(ahmed25[0.2875].verified, "secondary")
        self.assertEqual(ahmed25[0.299].verified, "secondary")

    def test_correlations_give_the_quoted_values(self):
        # MFIX-Exa's tutorial quotes 1.0917 for Schiller-Naumann at Re 100
        self.assertAlmostEqual(b.schiller_naumann(100.0), 1.0917, places=3)
        # arXiv:2206.06875 Table 5 lists its Eq. (20) as 1.087 at Re 100 and 0.776 at Re 200
        self.assertAlmostEqual(b.clift_low_re(100.0), 1.087, places=3)
        self.assertAlmostEqual(b.clift_low_re(200.0), 0.776, places=3)
        # A library quoted in the search results gives Morrison 0.4841 at Re 1000: the formula's terms are right
        self.assertAlmostEqual(b.morrison(1000.0), 0.4841, places=3)


class Replay(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not b.RECORD.is_file():
            raise unittest.SkipTest("hiraku/cfd/data/validation.json がまだありません")
        cls.record = load_record()
        cls.bodies = cls.record["bodies"]

    def test_record_keeps_the_frozen_tolerances(self):
        self.assertEqual(self.record["tolerance_pct"], FROZEN_TOLERANCE_PCT)
        for key, entry in self.bodies.items():
            self.assertEqual(entry["tolerance_pct"], b.tolerance_for(entry["body"], entry["re"]), key)

    def test_every_wanted_body_is_there_or_listed_as_not_run(self):
        listed = set(self.bodies) | set(self.record["not_run_yet"])
        self.assertEqual(listed, set(b.WANTED_BODIES) | set(self.bodies))
        self.assertFalse(set(self.bodies) & set(self.record["not_run_yet"]))

    def test_references_match_the_code(self):
        for key, entry in self.bodies.items():
            want = b.references_for(entry["body"], entry["re"])
            got = [entry["reference"]] + entry["other_references"]
            self.assertEqual(len(want), len(got), key)
            for w, g in zip(want, got):
                self.assertAlmostEqual(w.value, g["value"], places=12, msg=key)
                self.assertEqual(w.url, g["url"], key)
                self.assertEqual(w.verified, g["verified"], key)

    def test_the_numbers_are_there_and_add_up(self):
        for key, entry in self.bodies.items():
            levels = entry["levels"]
            self.assertTrue(levels, key)
            cells = [row["cells"] for row in levels]
            self.assertEqual(cells, sorted(cells), key)
            diff = difference_key(entry)
            for row in levels:
                self.assertTrue(math.isfinite(row["cd"]), key)
                self.assertGreater(row["cd"], 0.0, key)
                self.assertAlmostEqual(row[diff], 100.0 * (row["cd"] / entry["reference"]["value"] - 1.0), places=9, msg=key)
                self.assertIsNone(row["faulted"], key)
                self.assertLess(row["max_mach"], 0.5, key)
            finest = entry["finest"]
            self.assertEqual(finest["cells"], levels[-1]["cells"], key)
            self.assertEqual(finest["cd"], levels[-1]["cd"], key)

    def test_the_verdict_follows_from_the_numbers(self):
        for key, entry in self.bodies.items():
            inside = abs(entry["finest"][difference_key(entry)]) <= entry["tolerance_pct"]
            self.assertEqual(entry["within_tolerance"], inside, key)
            usable = entry["reference"]["verified"] != b.UNVERIFIED
            self.assertEqual(entry["reference_usable"], usable, key)
            settled = entry["levels"][-1]["spread_pct"] <= FROZEN_SETTLED_SPREAD_PCT
            self.assertEqual(entry["settled"], settled, key)
            self.assertEqual(entry["validated"], inside and usable and settled, key)

    def test_outside_stays_not_validated_in_plain_words(self):
        for key, entry in self.bodies.items():
            self.assertTrue(entry["conclusion"], key)
            if not entry["validated"]:
                self.assertTrue("検証できていない" in entry["conclusion"] or "検証にはならない" in entry["conclusion"], key)
            else:
                self.assertNotIn("検証できていない", entry["conclusion"], key)

    def test_extrapolation_only_from_monotone_levels(self):
        for key, entry in self.bodies.items():
            rich = entry["richardson"]
            if "extrapolated" in rich:
                self.assertTrue(rich["monotone"], key)
                cd = [row["cd"] for row in entry["levels"]][-3:]
                self.assertGreaterEqual((cd[0] - cd[1]) * (cd[1] - cd[2]), 0.0, key)
                again = b.richardson([(row["cells"], row["cd"]) for row in entry["levels"]])
                self.assertAlmostEqual(rich["extrapolated"], again["extrapolated"], places=9, msg=key)

    def test_steady_bodies_are_nearly_symmetric(self):
        # Re 100 and 200 have an axisymmetric steady wake: side forces come from the grid only
        for re in (100.0, 200.0):
            entry = self.bodies.get(b.body_id("sphere", re))
            if entry is None:
                continue
            for row in entry["levels"]:
                self.assertLess(abs(row["cy"]) + abs(row["cz"]), 0.01 * row["cd"], re)
                self.assertLess(row["spread_pct"], 1.0, re)

    def test_the_error_band_reads_only_flow_errors(self):
        # uncertainty.py takes the largest of every key ending in error_pct: only Cd errors may carry that name
        allowed = {"error_pct"}
        names = {name for name, _ in error_keys(self.record)}
        self.assertLessEqual(names, allowed, "a key that is not a Cd error would widen the band")
        # and nothing measured against a value nobody read
        for key, entry in self.bodies.items():
            if not entry["reference_usable"]:
                self.assertEqual(error_keys(entry["levels"]) + error_keys(entry["finest"]) + error_keys(entry["richardson"]), [], key)

    def test_public_record_names_no_private_paths(self):
        text = b.RECORD.read_text(encoding="utf-8")
        # any absolute folder on this machine (a drive letter, a home folder) or a mail address, named by its shape only
        self.assertIsNone(PRIVATE_PLACES.search(text))

    def test_the_place_check_sees_paths_and_mail_but_not_links(self):
        # the samples are joined from pieces, so no line of this file holds a whole folder or address
        for seen in ("D:" + "/runs/a", "E:" + "\\runs\\a", "/" + "home" + "/a/b", "x\\" + "Users" + "\\a", "a.b" + "@" + "example.org"):
            self.assertIsNotNone(PRIVATE_PLACES.search(seen), seen)
        for unseen in ("https://example.org/a", "Re 1e5: 0.47", "ratio 1:2"):
            self.assertIsNone(PRIVATE_PLACES.search(unseen), unseen)


@unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "遅い試験 (GPU で走らせ直す): HIRAKU_SLOW_TESTS=1 のときだけ")
class LiveRepeat(unittest.TestCase):
    """The smallest body in the record again, on the GPU: the number in the record comes back."""

    def test_smallest_saved_level(self):
        from hiraku.cfd.cuda import gpu_or_reason, gpu_turn
        from hiraku.cfd.solver import MemoryBudgetError

        if not b.RECORD.is_file():
            self.skipTest("hiraku/cfd/data/validation.json がまだありません")
        entry = load_record()["bodies"].get("plate")
        if entry is None or not entry["levels"]:
            self.skipTest("記録に平板の結果がまだありません")
        saved = entry["levels"][0]
        with gpu_turn(wait_s=float(os.environ.get("HIRAKU_GPU_WAIT_S", "1800"))):
            gpu, reason = gpu_or_reason()
            if gpu is None:
                self.skipTest(reason)
            try:
                try:
                    rec = b.run_case(gpu, b.plate_case(saved["cells"], t_star=saved["t_star"], u_lattice=saved["u_lattice"]))
                except MemoryBudgetError as e:
                    self.skipTest(str(e))
            finally:
                gpu.close()
        self.assertIsNone(rec["faulted"])
        change = 100.0 * (rec["cd"] / saved["cd"] - 1.0)
        self.assertLess(abs(change), LIVE_REPEAT_PCT, f"recorded {saved['cd']}, now {rec['cd']} (code {rec['code']} against {saved['code']})")


if __name__ == "__main__":
    unittest.main()
