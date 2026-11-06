"""Each seat's back and head restraint (ladder_4w: seatback_<R|L>, head_restraint_<R|L>, sized from the large person)
and K4's measure of them against both body sizes' heads (k4_geometry.seats)."""

import json
import math
import unittest
from pathlib import Path

from hiraku import designgen
from hiraku.designgen import ladder_4w
from hiraku.designgen.ladder_4w import DesignGenError
from hiraku.solvers.k4_geometry import interferences, people, seats
from hiraku.solvers.k4_geometry.people import BIG, SIZES, SMALL

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
SEATS = ("seat_R", "seat_L")


def build(**changes):
    return designgen.regenerate(V1, {k: {"value": v, "unit": "mm"} for k, v in changes.items()})


def parts(d):
    return {p["id"]: p for p in d["parts"]}


def lo_hi(p):
    return people._box(p)


class DefaultTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = build()
        cls.p = parts(cls.d)

    def test_standard_measures_from_the_parts(self):
        """Q35-Q38's measures (k4_geometry.seats.standard_measures): from the parts' boxes and the large person's hip
        point, as the module's notes say; reported by the assembly tool once each, with its assumption."""
        got = seats.standard_measures(self.d)
        self.assertEqual(len(got), 2)
        for rid, m in got.items():
            r = self.p[rid]
            c, s = r["box"]["center_mm"], r["box"]["size_mm"]
            seat = next(p for p in self.d["parts"] if p["kind"] == "seat" and seats.restraint_of(self.d, p) is r)
            top_z = seat["box"]["center_mm"][2] + seat["box"]["size_mm"][2] / 2.0
            self.assertAlmostEqual(m["height_above_R"], c[2] + s[2] / 2.0 - (top_z + SIZES[BIG]["trochanter_height"]))
            self.assertAlmostEqual(m["width"], s[1])
            self.assertAlmostEqual(m["backset"], seats.restraint_results(self.d)[f"headrest_gap_{BIG}.{rid}"][0])
            self.assertGreaterEqual(m["seatback_gap"], 0.0)
        from hiraku.solvers.shelf.assembly import Assembly

        rec = Assembly()._analyse(self.d)
        qs = [r["quantity"] for r in rec["results"]]
        for q in seats.STANDARD:
            self.assertEqual(qs.count(q), 1, q)
        self.assertTrue(any("R17" in a for a in rec["assumptions"]))

    def test_parts_for_both_seats(self):
        for seat in SEATS:
            sb, hr = ladder_4w.seat_back_ids(seat)
            self.assertEqual(self.p[sb]["kind"], "seatback")
            self.assertEqual(self.p[hr]["kind"], "head_restraint")
            # carried by the seat's own nodes (the seat cross members'), so their mass reaches the frame there
            self.assertEqual(self.p[sb]["carried_by"], self.p[seat]["carried_by"])
            self.assertEqual(self.p[hr]["carried_by"], self.p[seat]["carried_by"])
            self.assertEqual({q["id"] for q in people.seat_back_parts(self.d, self.p[seat])}, {sb, hr})

    def test_sizes_from_the_large_person(self):
        big = SIZES[BIG]
        for seat in SEATS:
            sb, hr = (self.p[i] for i in ladder_4w.seat_back_ids(seat))
            slo, shi = lo_hi(self.p[seat])
            blo, bhi = lo_hi(sb)
            self.assertAlmostEqual(bhi[0], slo[0], places=9)  # its front face is the seat back
            # down to a gap above the seat box's bottom (clear of the floor), on its rear face
            self.assertAlmostEqual(blo[2], slo[2] + ladder_4w.SEATBACK_FLOOR_GAP_MM, places=9)
            self.assertAlmostEqual(bhi[2] - shi[2], big["shoulder_height"], places=9)
            hlo, hhi = lo_hi(hr)
            self.assertAlmostEqual(hhi[2] - shi[2], ladder_4w.VARIABLES["headrest_height"].default, places=9)
            self.assertAlmostEqual(slo[0] - hhi[0], ladder_4w.VARIABLES["headrest_gap"].default, places=9)
            self.assertAlmostEqual(hlo[2], bhi[2], places=9)
        # the default top: level with the large head's centre of gravity, rounded up
        self.assertEqual(ladder_4w.VARIABLES["headrest_height"].default,
                         math.ceil(big["sitting_height"] - big["head_height"] / 2.0))

    def test_masses_from_the_thicknesses_and_the_seat_unchanged_in_all(self):
        rho = float(self.d["materials"]["stkr400"]["density_kg_m3"])
        shell = ladder_4w.PLASTIC_SHELL_T_MM * 1e-3 * ladder_4w.PLASTIC_DENSITY_KG_M3
        for seat in SEATS:
            sb, hr = (self.p[i] for i in ladder_4w.seat_back_ids(seat))
            w, h = sb["box"]["size_mm"][1], ladder_4w.SEATBACK_HEIGHT_MM  # the face above the cushion
            self.assertAlmostEqual(sb["mass_kg"], w * h * 1e-6 * (ladder_4w.SEATBACK_STEEL_T_MM * 1e-3 * rho + shell), places=9)
            a, b, c = hr["box"]["size_mm"]
            self.assertAlmostEqual(hr["mass_kg"], 2.0 * (a * b + b * c + a * c) * 1e-6 * shell, places=9)
            # the three together: the seat's 11 kg before its back was split off (12 kg less its belt's 1 kg,
            # kit/safety.place_belts), the seat's mass in the stored v1 before the seatbacks
            self.assertAlmostEqual(self.p[seat]["mass_kg"] + sb["mass_kg"] + hr["mass_kg"], 11.0, places=9)

    def test_no_interference(self):
        self.assertEqual(interferences(self.d), [])

    def test_k4_values_from_the_parts_and_the_body(self):
        rows = seats.head_restraints(self.d)
        self.assertEqual(set(rows), {"occupant_R", "occupant_L"})
        for oid, seat in (("occupant_R", "seat_R"), ("occupant_L", "seat_L")):
            hr = self.p[ladder_4w.seat_back_ids(seat)[1]]
            self.assertEqual(rows[oid]["restraint"], hr["id"])
            slo, shi = lo_hi(self.p[seat])
            hlo, hhi = lo_hi(hr)
            for size in (BIG, SMALL):
                s = SIZES[size]
                m = rows[oid][size]
                self.assertAlmostEqual(m["top_margin"], hhi[2] - (shi[2] + s["sitting_height"]), places=9)
                self.assertAlmostEqual(m["cg_margin"], hhi[2] - (shi[2] + s["sitting_height"] - s["head_height"] / 2.0),
                                       places=9)
                self.assertAlmostEqual(m["gap"], slo[0] - hhi[0], places=9)
        # the drawn large head (kit/body.py) is where K4 measures it: its back at the seat back, its top at I1
        head = self.p["occupant_R.head"]
        a, b = lo_hi(head)
        self.assertAlmostEqual(a[0], lo_hi(self.p["seat_R"])[0][0], places=6)
        self.assertAlmostEqual(b[2] - lo_hi(self.p["seat_R"])[1][2], SIZES[BIG]["sitting_height"], places=6)
        worst = seats.restraint_results(self.d)
        self.assertAlmostEqual(worst["headrest_top_margin"][0], min(rows[o][z]["top_margin"] for o in rows for z in (BIG, SMALL)))
        self.assertAlmostEqual(worst["headrest_gap"][0], max(rows[o][z]["gap"] for o in rows for z in (BIG, SMALL)))

    def test_assembly_tool_reports_them(self):
        from hiraku.solvers.shelf.assembly import Assembly

        res = Assembly()._analyse(self.d)["results"]
        names = [r["quantity"] for r in res]
        self.assertEqual(len(names), len(set(names)))  # each quantity once in the analysis
        got = {r["quantity"]: r for r in res}
        want = seats.restraint_results(self.d)
        rows = seats.head_restraints(self.d)
        # the worst, per restraint and per body size there: 3 quantities x (1 + 2 restraints x (1 + 2 sizes)); and the
        # 4 measures of Q35-Q38 (k4_geometry.seats.STANDARD), the worst and per restraint: 4 x (1 + 2)
        self.assertEqual(len(want), 3 * (1 + 2 * 3) + 4 * (1 + 2))
        for q, (v, where) in want.items():
            self.assertAlmostEqual(got[q]["value"], v, places=9)
            self.assertEqual(got[q]["where"], where)
            self.assertTrue(where.startswith("head_restraint_"))
        for row in rows.values():
            rid = row["restraint"]
            for size in (BIG, SMALL):
                self.assertAlmostEqual(got[f"headrest_top_margin_{size}.{rid}"]["value"], row[size]["top_margin"], places=9)
                self.assertAlmostEqual(got[f"headrest_gap_{size}.{rid}"]["value"], row[size]["gap"], places=9)
            self.assertAlmostEqual(got[f"headrest_top_margin.{rid}"]["value"],
                                   min(row[z]["top_margin"] for z in (BIG, SMALL)), places=9)


class SlideTest(unittest.TestCase):
    def test_occupant_against_the_seatback(self):
        """For several slides the trunk's (and the head's) back is the seatback's front face: no offset between them."""
        for slide in (0.0, 100.0, 200.0, ladder_4w.SEAT_SLIDE_MAX_MM):
            d = build(seat_slide=slide) if slide else build()
            p = parts(d)
            for occ, seat in (("occupant_R", "seat_R"), ("occupant_L", "seat_L")):
                front = lo_hi(p[ladder_4w.seat_back_ids(seat)[0]])[1][0]
                for part in (occ, f"{occ}.head"):
                    self.assertAlmostEqual(lo_hi(p[part])[0][0], front, places=6, msg=(slide, part))
            self.assertEqual(interferences(d), [], slide)
            if slide:
                ends = people.slide_ends(d)
                for _, _, moved in ends:
                    self.assertEqual(moved, {"seat_R", "seatback_R", "head_restraint_R"})
                rows = seats.head_restraints(d)["occupant_R"]
                # the restraint goes with the seat: the gap is the same wherever each person sits on the slide
                for size in (BIG, SMALL):
                    self.assertAlmostEqual(rows[size]["gap"], ladder_4w.VARIABLES["headrest_gap"].default, places=9)
                self.assertEqual(rows[BIG]["offset"], people.poses(d)["occupant_R"]["offset"]["big"])


class VariablesTest(unittest.TestCase):
    def test_variables_move_the_parts(self):
        gap = ladder_4w.HEADREST_GAP_RANGE_MM[1]  # the rearmost: still standing on the seatback
        d = build(headrest_height=950.0, headrest_gap=gap)
        self.assertEqual(d["variables"]["headrest_height"], {"value": 950.0, "unit": "mm"})
        self.assertEqual(d["variables"]["headrest_gap"], {"value": gap, "unit": "mm"})
        p = parts(d)
        for seat in SEATS:
            slo, shi = lo_hi(p[seat])
            sb, hr = (p[i] for i in ladder_4w.seat_back_ids(seat))
            hlo, hhi = lo_hi(hr)
            self.assertAlmostEqual(hhi[2] - shi[2], 950.0, places=9)
            self.assertAlmostEqual(slo[0] - hhi[0], gap, places=9)
            self.assertGreaterEqual(hhi[0] - lo_hi(sb)[0][0], ladder_4w.HEADREST_ON_BACK_MM - 1e-9)  # over the seatback
            # held: the head restraint on the seatback, the seatback on the seat (kit/joints.py keeps them)
            self.assertEqual([c["to"] for c in hr["connects_to"]], [sb["id"]])
            self.assertIn(seat, [c["to"] for c in sb["connects_to"]])
        m = seats.head_restraints(d)["occupant_R"][BIG]
        self.assertAlmostEqual(m["top_margin"], 950.0 - SIZES[BIG]["sitting_height"], places=9)
        self.assertAlmostEqual(m["gap"], gap, places=9)
        self.assertEqual(interferences(d), [])

    def test_defaults_not_written(self):
        d = build()
        self.assertNotIn("headrest_height", d["variables"])
        self.assertNotIn("headrest_gap", d["variables"])

    def test_out_of_range(self):
        lo, hi = ladder_4w.HEADREST_HEIGHT_RANGE_MM
        for bad in ({"headrest_height": lo - 1.0}, {"headrest_height": hi + 1.0},
                    {"headrest_gap": ladder_4w.HEADREST_GAP_RANGE_MM[1] + 1.0}):
            with self.assertRaises(DesignGenError, msg=bad):
                build(**bad)

    def test_meanings_say_assumption(self):
        for k in ("headrest_height", "headrest_gap"):
            self.assertIn("assumption", ladder_4w.VARIABLES[k].meaning)


if __name__ == "__main__":
    unittest.main()
