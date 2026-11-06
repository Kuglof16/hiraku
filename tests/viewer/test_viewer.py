"""The viewer's data scripts (viewer/src/<field>.py) on small fixtures trimmed from real records
(tests/viewer/fixtures/README.md), the data shape the page reads, and the page itself in headless
Chromium when the system Python has Playwright (skipped otherwise)."""

import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.viewer import no_paths

from viewer.src import assembly, common, crash, names, nvh, nvh_shell

ROOT = Path(__file__).resolve().parents[2]
FX = Path(__file__).resolve().parent / "fixtures"
VIEWER = ROOT / "viewer"
SCRIPTS = {"nvh": nvh, "crash": crash, "assembly": assembly}
COMMON_KEYS = {"field", "title", "source", "frame", "parts", "scene", "vectors", "results", "assumptions",
               "inputs", "notes", "ledger", "tyre"}
LEDGER_CATS = set(common.CATEGORIES)


def make(field, run=None, tool=None):
    args = SimpleNamespace(run=FX / (run or field), version=None, analysis=None, load_case=None, tool=tool)
    return SCRIPTS[field].build(args)


def make_shell():
    return nvh_shell.build(SimpleNamespace(run=FX / "shell", version=None, analysis=None, design=None))


class PackTest(unittest.TestCase):
    def test_round_trip_and_same_bytes(self):
        vals = [0.0, 1.5, -2.25, 1e6, 3.14159]
        p = common.pack_f32(vals, [5])
        got = common.unpack_f32(p)
        for a, b in zip(vals, got):
            self.assertAlmostEqual(a, b, delta=abs(a) * 1e-6 + 1e-6)
        self.assertEqual(p, common.pack_f32(vals, [5]))   # gzip without a time stamp: same bytes each run

    def test_data_js_reads_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = common.write_data("x", {"a": [1, 2], "s": "壁"}, Path(d))
            self.assertEqual(common.read_data_js(path), ("x", {"a": [1, 2], "s": "壁"}))
            path.read_text(encoding="ascii")


class RunFolderTest(unittest.TestCase):
    def test_version_folder_and_run_folder(self):
        self.assertEqual(common.version_dirs(FX / "nvh"), [FX / "nvh" / "v001"])
        self.assertEqual(common.version_dirs(FX / "nvh" / "v001"), [FX / "nvh" / "v001"])
        with self.assertRaises(LookupError):
            common.find_analysis(FX / "nvh", "crash")

    def test_load_case_from_project_or_file(self):
        self.assertEqual(common.load_case_of(FX / "nvh" / "v001", "N1-40kmh")["test_id"], "N1")
        self.assertEqual(common.load_case_of(FX / "crash" / "v001", "C1-40kmh")["test_id"], "C1")
        self.assertIsNone(common.load_case_of(FX / "assembly" / "v001", "H1-interference"))


class FieldDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obj = {f: make(f) for f in SCRIPTS}

    def test_common_shape(self):
        for f, o in self.obj.items():
            with self.subTest(field=f):
                self.assertTrue(COMMON_KEYS <= set(o), COMMON_KEYS - set(o))
                self.assertEqual(o["field"], f)
                fr = o["frame"]
                n = len(fr["node_ids"])
                self.assertEqual(len(common.unpack_f32(fr["nodes"])), 3 * n)
                self.assertEqual(len(fr["members"]), len(fr["member_ids"]))
                self.assertEqual(len(fr["members"]), len(fr["radius_mm"]))
                for a, b in fr["members"]:
                    self.assertTrue(0 <= a < n and 0 <= b < n)
                for p in o["parts"]:
                    self.assertEqual((len(p["c"]), len(p["s"]), len(p["r"])), (3, 3, 3))
                self.assertTrue(o["inputs"])
                self.assertNotIn("not_computed", o)
                # no file path anywhere in the data: the run by its folder name only
                text = json.dumps(o, ensure_ascii=False)
                self.assertNotRegex(text, no_paths.IN_JSON)
                self.assertEqual(o["source"]["run"], f if f != "crash" else "crash")
                # the parts ledger: one entry per part box, in the same order, with a known category
                L = o["ledger"]
                self.assertEqual([e["id"] for e in L["parts"]], [p["id"] for p in o["parts"]])
                for e in L["parts"]:
                    self.assertIn(e["cat"], LEDGER_CATS)
                    self.assertTrue(e["fn"].startswith("hiraku."), e["fn"])
                    if e["cat"] in ("frame_nodes", "unsprung", "mass_only"):
                        self.assertAlmostEqual(sum(kg for _, kg in e["nodes"]), e["mass_kg"], places=9)
                self.assertEqual(sum(r["count"] for r in L["table"]), len(L["parts"]))
                json.dumps(o)

    def test_ledger_per_field(self):
        cats = {f: {e["id"]: e["cat"] for e in o["ledger"]["parts"]} for f, o in self.obj.items()}
        # NVH (modal.add_masses): the battery on its carried_by nodes, the wheel at the wheel centre node
        self.assertEqual(cats["nvh"], {"battery": "frame_nodes", "wheel_FL": "unsprung"})
        w = next(e for e in self.obj["nvh"]["ledger"]["parts"] if e["id"] == "wheel_FL")
        self.assertEqual(w["nodes"], [["wc:FL", 14.0]])   # support FL is 60 mm outboard of LA: a link node
        self.assertEqual(self.obj["nvh"]["ledger"]["stated"], [])
        # crash A: every part in the one rigid mass; the record's vehicle_mass is compared (fixture: fewer parts)
        L = self.obj["crash"]["ledger"]
        self.assertEqual(set(cats["crash"].values()), {"mass_only"})
        st = {s["label"]: s for s in L["stated"]}
        self.assertTrue(st["frame_mass"]["agrees"], st["frame_mass"])
        self.assertFalse(st["vehicle_mass"]["agrees"])
        self.assertAlmostEqual(st["vehicle_mass"]["diff_kg"], L["total_kg"] - st["vehicle_mass"]["stated_kg"])
        self.assertTrue(L["members"]["crush_members"])
        # assembly: bodies, no masses
        self.assertEqual(set(cats["assembly"].values()), {"body"})
        self.assertIsNone(self.obj["assembly"]["ledger"]["total_kg"])
        # crash B: parts on their carried_by nodes, the record's frame mass agrees, parts moved with those nodes
        b = make("crash", "crash_b")
        self.assertEqual(set(e["cat"] for e in b["ledger"]["parts"]), {"frame_nodes"})
        sb = {s["label"]: s for s in b["ledger"]["stated"]}
        self.assertTrue(sb["frame"]["agrees"], sb["frame"])
        self.assertEqual(len(b["parts"]), len(b["ledger"]["parts"]))

    def test_ledger_rules_by_hand(self):
        # a two-member frame, one support at a frame node, an occupant left off by the loading 'empty'
        d = {"nodes": [{"id": "a", "xyz_mm": [0, 0, 0]}, {"id": "b", "xyz_mm": [1000, 0, 0]}, {"id": "c", "xyz_mm": [2000, 0, 0]},
                       {"id": "lone", "xyz_mm": [2000, 10, 0]}],
             "members": [{"id": "m1", "node_a": "a", "node_b": "b", "section": "s", "material": "st"},
                         {"id": "m2", "node_a": "b", "node_b": "c", "section": "s", "material": "st"}],
             "sections": {"s": {"shape": "rect_tube", "dims_mm": {"b": 40, "h": 80, "t": 2}}},
             "materials": {"st": {"E_MPa": 200000.0, "nu": 0.3, "density_kg_m3": 7850.0, "yield_MPa": 300.0}},
             "supports": [{"id": "S", "kind": "wheel", "position_mm": [0, 0, 0], "radius_mm": 300, "frame_node": "a"}],
             "parts": [{"id": "box", "kind": "battery", "box": {"center_mm": [1000, 0, 100], "size_mm": [100, 100, 100]}, "mass_kg": 30.0,
                        "carried_by": ["b", "lone"]},
                       {"id": "wheel", "kind": "wheel", "box": {"center_mm": [0, 0, 0], "size_mm": [100, 100, 100]}, "mass_kg": 10.0},
                       {"id": "driver", "kind": "occupant", "box": {"center_mm": [1500, 0, 300], "size_mm": [100, 100, 100]}, "mass_kg": 75.0}]}
        parts = common.parts_of(d)
        frame = 7850.0 * (40 * 80 - 36 * 76) * 2000.0 * 1e-9
        for tool in ("modal", "crash_beam"):
            L = common.parts_ledger(tool, d, {"params": {"loading": "empty"}}, {}, parts)
            c = {e["id"]: e for e in L["parts"]}
            self.assertEqual(c["driver"]["cat"], "not_in")
            self.assertEqual([x["id"] for x in L["not_in"]], ["driver"])
            self.assertEqual(c["wheel"]["cat"], "unsprung")
            self.assertEqual(c["wheel"]["nodes"], [["a", 10.0]])   # the support sits at frame node a
            self.assertAlmostEqual(L["members"]["mass_kg"], frame)
            self.assertAlmostEqual(L["total_kg"], frame + 40.0)
            self.assertEqual(sorted(L["no_stiffness"]), ["box", "wheel"])
        # modal: equal shares on the carried_by nodes as given; crash_beam: a node no member uses goes to the nearest
        m = {e["id"]: e for e in common.parts_ledger("modal", d, None, {}, parts)["parts"]}
        self.assertEqual(m["box"]["nodes"], [["b", 15.0], ["lone", 15.0]])
        b = {e["id"]: e for e in common.parts_ledger("crash_beam", d, {"params": {"loading": "empty"}}, {}, parts)["parts"]}
        self.assertEqual(b["box"]["nodes"], [["b", 15.0], ["c", 15.0]])
        # crash_beam's stated mass is read from its assumption and compared per row
        rec = {"assumptions": [f"mass {frame + 40.0:.1f} kg: frame {frame:.1f} kg on its element nodes, parts 40.0 kg; "
                               "the parts without carried_by (wheels, suspension) as point masses at their nearest support's "
                               "frame node: S 10.0 kg at a; each part's mass ..."]}
        L = common.parts_ledger("crash_beam", d, {"params": {"loading": "empty"}}, rec, parts)
        self.assertTrue(all(s["agrees"] for s in L["stated"]), L["stated"])
        self.assertEqual(len(L["stated"]), 4)

    def test_run_name_not_path(self):
        self.assertEqual(common.run_name(FX / "nvh", FX / "nvh" / "v001"), "nvh")
        self.assertEqual(common.run_name(FX / "nvh" / "v001", FX / "nvh" / "v001"), "nvh")

    def test_nvh(self):
        o = self.obj["nvh"]
        rec = common.find_analysis(FX / "nvh", "modal")[1]
        n = len(o["frame"]["node_ids"])
        self.assertEqual(len(o["modes"]), 3)
        for m, f in zip(o["modes"], rec["fields"]):
            shape = common.unpack_f32(m["shape"])
            self.assertEqual(len(shape), 3 * n)
            self.assertAlmostEqual(m["f_Hz"], f["frequency_Hz"])
            amp = max(math.sqrt(sum(c * c for c in v)) for v in f["per_node"].values())
            self.assertAlmostEqual(m["amp_max"], amp, places=9)
            # the node order follows frame.node_ids
            i = o["frame"]["node_ids"].index("LHT")
            for a in range(3):
                self.assertAlmostEqual(shape[3 * i + a], f["per_node"]["LHT"][a], places=5)
        self.assertEqual(len(common.unpack_f32(o["frf"]["x"])), len(common.unpack_f32(o["frf"]["y"])))
        self.assertEqual({l["label"] for l in o["lines"]}, {"wheel rotation", "motor rotation"})

    def test_crash(self):
        o = self.obj["crash"]
        rec = common.find_analysis(FX / "crash", "crash")[1]
        self.assertEqual(len(o["stress"]["per_member"]), len(o["frame"]["member_ids"]))
        st = rec["fields"][0]["per_member"]
        self.assertAlmostEqual(o["stress"]["max"], max(st[m] for m in o["frame"]["member_ids"] if m in st))
        t = common.unpack_f32(o["t"])
        for k in ("decel", "crush", "wall_force"):
            self.assertEqual(len(common.unpack_f32(o[k])), len(t))
        self.assertAlmostEqual(o["a_max"], max(common.series(rec, "deceleration")["values"]))
        self.assertEqual([s["kind"] for s in o["scene"]], ["wall"])
        self.assertEqual(len(o["vectors"]), len(rec["vectors"]))
        self.assertEqual(o["model"], "A")
        self.assertTrue(any("1 次元モデル" in t for t in o["notes"]))
        self.assertTrue(any("a(t)/a_max" in t for t in o["notes"]))
        self.assertEqual(o["wall_x_mm"], o["scene"][0]["c"][0] - o["scene"][0]["s"][0] / 2)
        labels = {r["label"] for r in o["inputs"]}
        self.assertTrue({"速さ", "壁", "質量"} <= labels, labels)
        # the page never moves the A vehicle: no crush(t) translation in its code path
        js = (VIEWER / "js" / "crash.js").read_text(encoding="utf-8")
        a_part = js[js.index("HV.crashA"):js.index("HV.crashB = ")]
        self.assertNotIn("boxes.off", a_part)
        self.assertNotIn("ghost.position", a_part)

    def test_crash_b(self):
        o = make("crash", "crash_b")
        rec = common.find_analysis(FX / "crash_b", "crash_beam")[1]
        nm = rec["fields"][0]
        self.assertEqual(o["model"], "B")
        fr, mo = o["frame"], o["motion"]
        n, ne, nF = len(nm["nodes"]), len(nm["members"]), len(nm["frames"]["t_s"])
        self.assertEqual((len(fr["node_ids"]), len(fr["members"]), len(fr["member_ids"])), (n, ne, ne))
        pos = common.unpack_f32(mo["pos"])
        self.assertEqual(len(pos), nF * n * 3)
        self.assertEqual(len(common.unpack_f32(mo["t"])), nF)
        self.assertEqual(len(mo["broken"]), nF)
        # frame 0 is the mesh at rest; the last frame is the record's last, node by node
        self.assertEqual(common.unpack_f32(fr["nodes"]), pos[:n * 3])
        last = nm["frames"]["positions_mm"][-1]
        for i in (0, n // 2, n - 1):
            for a in range(3):
                self.assertAlmostEqual(pos[((nF - 1) * n + i) * 3 + a], last[i][a], places=2)
        for k in ("strain", "fold"):
            v = common.unpack_f32(o["colour"][k])
            self.assertEqual(len(v), nF * ne)
            self.assertTrue(all(abs(x) < 1e-3 for x in v[:ne]), k)   # nothing at t = 0
        # an element's strain recomputed by hand at the last frame
        ia, ib = fr["members"][0]
        P0, PL = nm["frames"]["positions_mm"][0], last
        want = 100 * (math.dist(PL[ia], PL[ib]) - math.dist(P0[ia], P0[ib])) / math.dist(P0[ia], P0[ib])
        self.assertAlmostEqual(common.unpack_f32(o["colour"]["strain"])[(nF - 1) * ne], want, places=3)
        self.assertEqual(len(common.unpack_f32(o["part_off"])), nF * len(o["parts"]) * 3)
        b = o["beyond"]
        wx = o["wall_x_mm"]
        self.assertEqual(wx, o["scene"][0]["c"][0] - o["scene"][0]["s"][0] / 2)
        want_n = [sum(1 for p in F if p[0] > wx + b["tol_mm"]) for F in nm["frames"]["positions_mm"]]
        self.assertEqual(b["nodes"], want_n)
        self.assertEqual(len(b["parts"]), nF)
        self.assertTrue(any("壁面" in t for t in o["notes"]))
        self.assertTrue(any("変形 ×1" in t for t in o["notes"]))
        self.assertTrue({"速さ", "壁", "壁の摩擦", "梁が折れる角度", "質量", "梁の分割"} <= {r["label"] for r in o["inputs"]})
        for k in ("t", "decel", "crush"):
            self.assertEqual(len(common.unpack_f32(o["series"][k])), len(common.series(rec, "deceleration")["t_s"]))
        # --tool picks the kind; a run with only A falls back to A
        with self.assertRaises(LookupError):
            make("crash", "crash_b", tool="crash")
        self.assertEqual(make("crash", "crash", tool=None)["model"], "A")

    def test_crash_b_record_results(self):
        # K2 182f148f: the stop (a piece broke off the seats' side), member axial force and end hinges at the
        # node_motion frames, hot spots
        o = make("crash", "crash_b")
        rec = common.find_analysis(FX / "crash_b", "crash_beam")[1]
        nm = rec["fields"][0]
        nF, ne = len(nm["frames"]["t_s"]), len(nm["members"])
        st = o["stop"]
        split = common.result(rec, "frame_split_time")
        self.assertAlmostEqual(st["t_s"], split)
        self.assertEqual(st["member"], next(r["where"] for r in rec["results"] if r["quantity"] == "frame_split_time"))
        self.assertEqual((st["piece_pct"], st["limit_pct"]), (4.6, 3.0))
        self.assertTrue(any("計算の停止" in t and st["member"] in t for t in o["notes"]))
        C = o["colour"]
        stress, hinge = common.unpack_f32(C["stress"]), common.unpack_f32(C["hinge"])
        self.assertEqual((len(stress), len(hinge)), (nF * ne, nF * ne))
        # one member by hand at the last frame: stress = its axial force / the section's area; hinge = larger end
        e = o["frame"]["member_ids"].index("rail_L_AS1")
        d = common.read_json(FX / "crash_b" / "v001" / "design.json")
        sec = d["sections"][next(m for m in d["members"] if m["id"] == "rail_L_AS1")["section"]]
        A = nvh.section_props(sec)["A"]
        self.assertAlmostEqual(C["area_mm2"]["rail_L_AS1"], A)
        F = common.series(rec, "member_axial_force.rail_L_AS1")["values"][-1]
        self.assertAlmostEqual(stress[(nF - 1) * ne + e], F / A, delta=abs(F / A) * 1e-5 + 1e-4)
        ends = [common.series(rec, f"member_hinge.rail_L_AS1.{n}")["values"][-1] for n in ("LAp2", "LS1")]
        self.assertAlmostEqual(hinge[(nF - 1) * ne + e], max(ends), places=4)
        self.assertEqual(sorted(C["hinge_ends"]["rail_L_AS1"]), ["LAp2", "LS1"])
        self.assertEqual(len(common.unpack_f32(C["force"]["rail_L_AS1"])), nF)
        # hot spots: least room first, OK / NG against the limit
        hs = o["hot_spots"]
        self.assertEqual(len(hs), len(rec["hot_spots"]))
        self.assertEqual([h["value"] for h in hs], sorted(h["value"] for h in hs))
        self.assertEqual(o["summary"]["least_room"], hs[0])
        self.assertEqual(hs[0]["ok"], hs[0]["value"] >= hs[0]["limit"])
        dv = common.series(rec, "deceleration")["values"]
        self.assertAlmostEqual(o["summary"]["peak_decel"], common.result(rec, "peak_decel"))
        # the record's mean_decel when it has one (speed lost / time to the stop), else the series' mean
        md = common.result(rec, "mean_decel")
        self.assertAlmostEqual(o["summary"]["mean_decel"], md if md is not None else sum(dv) / len(dv))
        self.assertAlmostEqual(o["summary"]["crush_end"], common.result(rec, "crush_at_split"))
        # the wall at the front-most part box, as the record's scene gives it
        self.assertEqual(o["wall_x_mm"], 415.0)

    def test_crash_contact_record(self):
        # K2 9bffe19c (the run goes on to the vehicle's full stop): element contact, wheel nodes on no element touching the wall through the tyre spring, C1
        # judged by the least room over the run (occupant_space_margin) and frame_split_fraction (C1-split)
        o = make("crash", "crash_contact")
        rec = common.find_analysis(FX / "crash_contact", "crash_beam")[1]
        nm = rec["fields"][0]
        self.assertIsNone(o["stop"])  # no frame_split_time: no piece that can reach the occupants broke off
        sm = o["summary"]
        self.assertIsNone(sm["after"])
        self.assertAlmostEqual(sm["crush_end"], common.result(rec, "max_crush"))
        self.assertAlmostEqual(sm["mean_decel"], common.result(rec, "mean_decel"))
        mg = sm["margin"]
        self.assertAlmostEqual(mg["value"], common.result(rec, "occupant_space_margin"))
        self.assertEqual((mg["op"], mg["limit"], mg["ok"], mg["load_case_id"]), (">=", 0.0, mg["value"] >= 0.0, "C1-40kmh"))
        self.assertEqual((mg["where"], mg["member"], mg["t_s"]), ("occupant_L.foot_L", "dash_post_L", 0.0757))
        least = min(h["value"]["value"] for h in rec["hot_spots"] if h["quantity"] == "occupant_space_margin")
        self.assertAlmostEqual(least, mg["value"])
        sp = sm["split"]
        self.assertAlmostEqual(sp["value"], common.result(rec, "frame_split_fraction"))
        self.assertEqual((sp["op"], sp["limit"], sp["load_case_id"], sp["ok"]), ("<=", 0.03, "C1-split", sp["value"] <= 0.03))
        (pc,) = sp["pieces"]
        self.assertAlmostEqual(pc.pop("t_s"), 0.0133)
        self.assertEqual(pc, {"nodes": 5, "pct": 1.5, "members": ["cross_F"]})
        self.assertEqual(sp["not_counted"], 1)
        cm = sm["crush_margin"]
        self.assertAlmostEqual(cm["value"], common.result(rec, "crumple_length") - common.result(rec, "max_crush"))
        self.assertTrue(o["tyre_spring"])
        self.assertIsNone(sm["wall_free_after_s"])
        # the four wheel nodes: radius, links to the mounts the record names, the first frame at the wall by hand
        W = {w["id"]: w for w in o["wheels"]}
        self.assertEqual(sorted(W), ["wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR"])
        fl = W["wheel_FL"]
        self.assertEqual((fl["r"], fl["kg"], fl["parts"]), (300.0, 18.0, ["wheel_FL", "knuckle_FL", "caliper_FL"]))
        self.assertEqual(fl["mount_ids"], ["LAl1", "LAl2", "LAu1", "LAu2"])
        self.assertEqual([o["frame"]["node_ids"][i] for i in fl["mounts"]], fl["mount_ids"])
        P, wx = nm["frames"]["positions_mm"], o["wall_x_mm"]
        k = next(k for k, F in enumerate(P) if F[fl["node"]][0] + 300.0 >= wx - crash.TOL_MM)
        self.assertEqual(fl["t_wall_s"], nm["frames"]["t_s"][k])
        self.assertIsNone(W["wheel_RL"]["t_wall_s"])
        # wheel nodes are not counted as frame nodes beyond the wall; parts on a wheel move with its node
        self.assertEqual(len(o["frame"]["members"]), len(nm["members"]))
        e = next(x for x in o["ledger"]["parts"] if x["id"] == "knuckle_FL")
        self.assertEqual(e["nodes"][0][0], "wheel_FL~centre")
        j = [p["id"] for p in o["parts"]].index("knuckle_FL")
        off = common.unpack_f32(o["part_off"])
        npt, last = len(o["parts"]), len(P) - 1
        for a in range(3):
            self.assertAlmostEqual(off[(last * npt + j) * 3 + a], P[-1][fl["node"]][a] - P[0][fl["node"]][a], places=3)
        rows = {s["label"]: s for s in o["ledger"]["stated"]}
        self.assertTrue(rows["wheel node wheel_FL~centre"]["agrees"])
        self.assertTrue(any("離れた塊" in t and "cross_F" in t for t in o["notes"]))
        self.assertTrue(any("タイヤのばね" in t for t in o["notes"]))
        self.assertTrue(any("乗員への入力" in t and "1 ステップで止まる" not in t for t in o["notes"]))

    def test_crash_colour_flicker_and_hinge_growth(self):
        # the default colour (end hinge rotation) against the axial stress, on the trimmed contact record: the share
        # of members turning back by more than 5 % of the colour range per frame, and the hinge only growing
        o = make("crash", "crash_contact")
        rec = common.find_analysis(FX / "crash_contact", "crash_beam")[1]
        C, ids = o["colour"], o["frame"]["member_ids"]
        ne, brk = len(ids), [set(b) for b in o["motion"]["broken"]]
        nF = len(brk)
        skip = lambda k: {e for e in range(ne) if ids[e] in brk[k] or ids[e] in brk[k - 1] or (k + 1 < nF and ids[e] in brk[k + 1])}
        mean = lambda v: sum(v) / len(v)
        raw, avg, hcol = (common.unpack_f32(C[k]) for k in ("stress", "stress_avg", "hinge_colour"))
        f_raw = mean(crash.flicker_share(raw, nF, ne, 2 * C["stress_absmax"], skip))
        f_avg = mean(crash.flicker_share(avg, nF, ne, 2 * C["stress_avg_absmax"], skip))
        f_hng = mean(crash.flicker_share(hcol, nF, ne, C["hinge_max"], skip))
        # (the fixture keeps 17 frames 4.7 ms apart: the 5 ms window holds one sample, so the average is the raw value
        # here; the full run's 0.7 ms frames are in test_flicker_share_and_average below)
        self.assertGreater(f_raw, 0.0)             # the raw axial stress turns back somewhere
        self.assertLessEqual(f_avg, f_raw)
        self.assertEqual(f_hng, 0.0)               # the hinge colour never turns back
        self.assertEqual(C["avg_s"], 0.005)
        self.assertAlmostEqual(C["dt_s"], (rec["fields"][0]["frames"]["t_s"][-1] - rec["fields"][0]["frames"]["t_s"][0]) / (nF - 1))
        # the colour of the hinge: the record's rotation, or (when it goes back) its running maximum
        hinge = common.unpack_f32(C["hinge"])
        back = crash.decreases(hinge, nF, ne, C["hinge_max"], skip)
        self.assertEqual(C["hinge_running_max"], back["steps"] > 0)
        self.assertEqual((back["steps"], C["hinge_running_max"]), (0, False))   # the trimmed record: never back by > 1 %
        self.assertEqual(hcol, hinge)
        self.assertEqual(crash.decreases(hcol, nF, ne, C["hinge_max"], skip)["steps"], 0)
        # the average by hand for one member at a middle frame: the force series' samples within 2.5 ms / area
        m = sorted(C["force"])[0]   # a member the trimmed record keeps a force series for
        e, k = ids.index(m), nF // 2
        s = common.series(rec, f"member_axial_force.{m}")
        tk = rec["fields"][0]["frames"]["t_s"][k]
        near = [v for t, v in zip(s["t_s"], s["values"]) if abs(t - tk) <= crash.AVG_S / 2 + 1e-9]
        self.assertAlmostEqual(avg[k * ne + e], mean(near) / C["area_mm2"][m], delta=1e-3 * C["stress_avg_absmax"])
        self.assertTrue(any("色の既定はヒンジ回転" in t for t in o["notes"]))

    def test_flicker_share_and_average(self):
        # two members, 0.7 ms frames: member 0 swings +-10 every frame (a wave sampled at the record's interval),
        # member 1 grows; range 20 -> both steps 20 > 5 % of 20: member 0 turns back at every inner frame
        nF, dt = 15, 0.0007
        t = [k * dt for k in range(nF)]
        v = [x for k in range(nF) for x in (10.0 if k % 2 else -10.0, float(k))]
        sh = crash.flicker_share(v, nF, 2, 20.0)
        self.assertEqual(sh, [0.5] * (nF - 2))
        self.assertEqual(crash.flicker_share(v, nF, 2, 20.0, skip=lambda k: {0}), [0.0] * (nF - 2))
        # the 5 ms average (centred, +-2.5 ms: 7 samples of 0.7 ms) of the swing: +-10/7 inside
        a = crash._avg_at_frames({"t_s": t, "values": [v[2 * k] for k in range(nF)]}, t)
        self.assertAlmostEqual(a[7], sum(v[2 * k] for k in range(4, 11)) / 7)
        self.assertAlmostEqual(a[0], sum(v[2 * k] for k in range(0, 4)) / 4)   # cut short at the start
        self.assertLess(max(abs(x) for x in a[3:-3]), 10.0 / 7 + 1e-9)
        # going back: member 1 drops by 2 at frame 5 (range 14): one step, one member, one frame; 1 % of 14 is 0.14
        v2 = list(v)
        v2[5 * 2 + 1] = 2.0
        d = crash.decreases([v2[2 * k + 1] for k in range(nF)], nF, 1, 14.0)
        self.assertEqual(d, {"members": [0], "frames": [5], "steps": 1})
        self.assertEqual(crash.decreases([float(k) for k in range(nF)], nF, 1, 14.0)["steps"], 0)

    def test_rigid_fit(self):
        # one node: its translation, no rotation
        c, t, w = common.rigid_fit([[10, 20, 30]], [[1, -2, 3]])
        self.assertEqual((c, t, w), ([10, 20, 30], [1, -2, 3], [0.0, 0.0, 0.0]))
        # several nodes moved by a known small rigid motion: recovered
        pts = [[0, 0, 0], [1000, 0, 0], [0, 500, 0], [200, 300, 400]]
        c0 = [sum(p[k] for p in pts) / 4 for k in range(3)]
        t0, w0 = [1.0, -0.5, 2.0], [1e-4, -2e-4, 3e-4]
        disp = []
        for p in pts:
            r = [p[k] - c0[k] for k in range(3)]
            disp.append([t0[0] + w0[1] * r[2] - w0[2] * r[1], t0[1] + w0[2] * r[0] - w0[0] * r[2], t0[2] + w0[0] * r[1] - w0[1] * r[0]])
        c, t, w = common.rigid_fit(pts, disp)
        for k in range(3):
            self.assertAlmostEqual(c[k], c0[k])
            self.assertAlmostEqual(t[k], t0[k])
            self.assertAlmostEqual(w[k], w0[k], delta=1e-9)
        # two nodes on the x axis: the turn about x is not seen and comes back 0
        c, t, w = common.rigid_fit([[0, 0, 0], [100, 0, 0]], [[0, 0, -1], [0, 0, 1]])
        self.assertAlmostEqual(w[0], 0.0)
        self.assertAlmostEqual(w[1], -0.02)

    def test_nvh_part_motion(self):
        # by hand: a part on one node moves exactly with it; a part on three nodes by their rigid motion; a part
        # without carried_by with its support's frame node when the record has no wheel centre node
        d = {"nodes": [{"id": "a", "xyz_mm": [0, 0, 0]}, {"id": "b", "xyz_mm": [1000, 0, 0]}, {"id": "c", "xyz_mm": [0, 600, 0]}],
             "members": [{"id": "m1", "node_a": "a", "node_b": "b", "section": "s", "material": "st"},
                         {"id": "m2", "node_a": "a", "node_b": "c", "section": "s", "material": "st"}],
             "sections": {"s": {"shape": "rect_tube", "dims_mm": {"b": 40, "h": 80, "t": 2}}},
             "materials": {"st": {"E_MPa": 200000.0, "nu": 0.3, "density_kg_m3": 7850.0, "yield_MPa": 300.0}},
             "supports": [{"id": "S", "kind": "wheel", "position_mm": [0, 60, 0], "radius_mm": 300, "frame_node": "a",
                           "suspension": {"type": "spring", "stiffness_N_per_mm": 20.0}}],
             "parts": [{"id": "one", "kind": "battery", "box": {"center_mm": [1000, 0, 100], "size_mm": [50, 50, 50]}, "mass_kg": 5.0, "carried_by": ["b"]},
                       {"id": "three", "kind": "battery", "box": {"center_mm": [300, 200, 100], "size_mm": [50, 50, 50]}, "mass_kg": 5.0, "carried_by": ["a", "b", "c"]},
                       {"id": "wheel", "kind": "wheel", "box": {"center_mm": [0, 60, 0], "size_mm": [50, 50, 50]}, "mass_kg": 5.0}]}
        parts = common.parts_of(d)
        L = common.parts_ledger("modal", d, {"params": {"loading": "empty"}}, {}, parts)
        self.assertEqual(L["parts"][2]["nodes"][0][0], "wc:S")
        w0 = [0.0, 0.0, 1e-3]   # a turn about z plus a lift
        shape = {"a": [0.0, 0.0, 2.0], "b": [0.0, 1.0, 2.0], "c": [-0.6, 0.0, 2.0]}
        pm = nvh.part_motion(parts, L, d, shape)
        self.assertEqual(pm["nodes"], [["b"], ["a", "b", "c"], ["a"]])
        self.assertEqual(pm["t"][0], shape["b"])
        self.assertEqual(pm["w"][0], [0.0, 0.0, 0.0])
        for k in range(3):
            self.assertAlmostEqual(pm["w"][1][k], w0[k], delta=1e-10)   # rigid_fit's 1e-9 relative regularisation
        self.assertEqual(pm["t"][2], shape["a"])
        self.assertTrue(all(0.0 <= r <= 1.0 for r in pm["rel"]) and max(pm["rel"]) == 1.0)
        # the tyre and its spring as K1 has it: from the wheel node down to a fixed ground point
        wh = nvh.wheels_of(d, shape)
        self.assertEqual((wh[0]["node"], wh[0]["ground"], wh[0]["spring"]), ("a", [0.0, 60.0, -300.0], True))
        self.assertEqual(nvh.wheels_of(d, {**shape, "wc:S": [0, 0, 1]})[0]["node"], "wc:S")

    def test_nvh_part_data(self):
        o = self.obj["nvh"]
        rec = common.find_analysis(FX / "nvh", "modal")[1]
        npart = len(o["parts"])
        self.assertTrue(any("自分の自由度を持たない" in t for t in o["notes"]))
        self.assertEqual(len(o["wheels"]), 4)
        for m, f in zip(o["modes"], rec["fields"]):
            for k in ("t", "w", "c"):
                self.assertEqual(len(common.unpack_f32(m["parts"][k])), 3 * npart)
            self.assertEqual(len(m["parts"]["rel"]), npart)
            self.assertEqual(len(m["wheels"]), 4)
            # wheel_FL (no carried_by, support FL): with frame node LA's motion (the record has no wc:FL)
            j = [p["id"] for p in o["parts"]].index("wheel_FL")
            t = common.unpack_f32(m["parts"]["t"])[3 * j:3 * j + 3]
            for a in range(3):
                self.assertAlmostEqual(t[a], f["per_node"]["LA"][a], places=5)

    def test_nvh_strain_by_hand(self):
        d = {"nodes": [{"id": "a", "xyz_mm": [0, 0, 0]}, {"id": "b", "xyz_mm": [1000, 0, 0]}, {"id": "c", "xyz_mm": [2000, 0, 0]}],
             "members": [{"id": "m1", "node_a": "a", "node_b": "b", "section": "s", "material": "st"},
                         {"id": "m2", "node_a": "b", "node_b": "c", "section": "s", "material": "st"}],
             "sections": {"s": {"shape": "rect_tube", "dims_mm": {"b": 40, "h": 80, "t": 2}}},
             "materials": {"st": {"E_MPa": 200000.0}}}
        fr = common.frame_of(d)
        # rigid translation and rigid rotation about z: no strain
        for shape in ({"a": [1, 2, 3], "b": [1, 2, 3], "c": [1, 2, 3]},
                      {"a": [0, 0, 0], "b": [0, 1e-3 * 1000, 0], "c": [0, 1e-3 * 2000, 0]}):
            ms = nvh.member_strain(d, fr, shape)
            self.assertLess(max(ms["total"]), 1e-9)
        # stretch: axial = du / L
        ms = nvh.member_strain(d, fr, {"a": [0, 0, 0], "b": [1, 0, 0], "c": [3, 0, 0]})
        self.assertAlmostEqual(ms["axial"][0], 1e-3)
        self.assertAlmostEqual(ms["axial"][1], 2e-3)
        self.assertAlmostEqual(ms["bending"][0], 0.0)
        # a kink at b (b up by w): chord rotations +-w/L, node b's rotation is their mean 0, ends a and c
        # take their own member's chord; phi_b = -+w/L, curvature at b = 4 w / L^2, strain x h/2
        w, L = 1.0, 1000.0
        ms = nvh.member_strain(d, fr, {"a": [0, 0, 0], "b": [0, 0, w], "c": [0, 0, 0]})
        self.assertAlmostEqual(ms["bending"][0], 4 * w / L ** 2 * 40.0, places=12)
        sp = nvh.section_props(d["sections"]["s"])
        self.assertAlmostEqual(sp["A"], 40 * 80 - 36 * 76)
        self.assertAlmostEqual(sp["I_v"], (40 * 80 ** 3 - 36 * 76 ** 3) / 12)
        self.assertAlmostEqual(ms["energy"][0], 200000.0 * sp["I_v"] / L * 2 * (w / L) ** 2, places=6)

    def test_nvh_strain_data(self):
        o = self.obj["nvh"]
        ne = len(o["frame"]["member_ids"])
        self.assertIn("translations only", o["strain_method"])
        for m in o["modes"]:
            st, en = m["strain"], m["energy"]
            for k in ("axial", "bending", "total"):
                self.assertEqual(len(st[k]), ne)
            self.assertAlmostEqual(st["max"], max(st["total"]))
            self.assertGreater(st["max"], 0)
            self.assertAlmostEqual(st["per_mm"], st["max"] / m["amp_max"])
            self.assertEqual(len(en["share"]), ne)
            self.assertAlmostEqual(sum(s for s in en["share"] if s is not None), 1.0, places=9)
            shares = [t["share"] for t in en["top"]]
            self.assertEqual(shares, sorted(shares, reverse=True))
            self.assertTrue(0 < len(en["top"]) <= nvh.TOP)
            for t in en["top"]:
                self.assertIn(t["member"], o["frame"]["member_ids"])
                self.assertTrue(0 <= t["axial_part"] <= 1 + 1e-12)

    def test_assembly(self):
        o = self.obj["assembly"]
        rec = common.find_analysis(FX / "assembly", "assembly")[1]
        steps = o["steps"]
        self.assertEqual(len(steps), len(rec["assembly"]))
        self.assertEqual([s["order"] for s in steps], sorted(s["order"] for s in steps))
        for s in steps:
            self.assertIn(s["status"], ("ok", "blocked", "no_tool", "no_reach"))
            if s["pi"] >= 0 and s["path"]:
                p = o["parts"][s["pi"]]
                self.assertEqual(p["id"], s["part"])
                for a in range(3):   # the last path point plus off is the design's box centre
                    self.assertAlmostEqual(s["path"][-1][a] + s["off"][a], p["c"][a], places=6)
        n_tool = sum(1 for s in rec["assembly"] if s["tool_clearance"] is False and s["no_interference"] is not False)
        self.assertEqual(sum(1 for s in steps if s["status"] == "no_tool"), n_tool)
        self.assertEqual(len(o["interferences"]), 4)
        for it in o["interferences"]:
            kinds = [e["kind"] for e in it["ends"]]
            self.assertEqual(kinds, ["part", "member"])
            self.assertEqual(o["frame"]["member_ids"][it["ends"][1]["index"]], it["b"][len("member "):])
            self.assertGreater(it["depth"], 0)

    def test_keys_the_page_reads(self):
        # every D.<key> a field's script reads exists in that field's data; main.js and core.js read common keys
        crash_js = (VIEWER / "js" / "crash.js").read_text(encoding="utf-8")
        cut = crash_js.index("HV.crashB = ")
        parts = {f: [((VIEWER / "js" / f"{f}.js").read_text(encoding="utf-8"), o)] for f, o in self.obj.items()}
        parts["crash"] = [(crash_js[:cut], self.obj["crash"]), (crash_js[cut:], make("crash", "crash_b"))]
        nvh_js = (VIEWER / "js" / "nvh.js").read_text(encoding="utf-8")
        cut = nvh_js.index("HV.nvhShell = ")
        cut2 = nvh_js.index("HV.FRF_COL = ")   # the 接地面の加振 view: tests/viewer/test_viewer_frf.py checks its keys
        parts["nvh"] = [(nvh_js[:cut], self.obj["nvh"]), (nvh_js[cut:cut2], make_shell())]
        for f, lst in parts.items():
            for js, o in lst:
                with self.subTest(field=f, model=o.get("model")):
                    used = set(re.findall(r"\bD\.([A-Za-z_]+)", js))
                    self.assertTrue(used <= set(o), used - set(o))
        for name in ("main.js", "core.js"):
            used = set(re.findall(r"\bD\.([A-Za-z_]+)", (VIEWER / "js" / name).read_text(encoding="utf-8")))
            self.assertTrue(used <= COMMON_KEYS, used - COMMON_KEYS)

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            for f in SCRIPTS:
                r = subprocess.run([sys.executable, "-m", f"viewer.src.{f}", str(FX / f), "--out", d],
                                   cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(common.read_data_js(Path(d) / f"{f}.js")[0], f)

    def test_data_folder_is_ignored(self):
        self.assertIn("data/", (VIEWER / ".gitignore").read_text(encoding="utf-8").split())


class ShellDataTest(unittest.TestCase):
    """nvh_shell on the trimmed modal_shell record (fixtures/shell: 20 mm mesh, two members, modes 1 and 5)."""

    @classmethod
    def setUpClass(cls):
        cls.o = make_shell()
        cls.rec = common.read_json(FX / "shell" / "analysis.json")
        cls.mesh = common.read_json(FX / "shell" / "meshes" / "mesh-0001.json")
        cls.design = common.read_json(FX / "shell" / "design.json")

    def test_shape(self):
        o, sh = self.o, self.o["shell"]
        self.assertTrue(COMMON_KEYS <= set(o))
        self.assertEqual((o["field"], o["model"], o["source"]["run"], o["source"]["tool"]), ("nvh", "shell", "shell", "modal_shell"))
        self.assertEqual((sh["n_nodes"], sh["n_elements"]), (len(self.mesh["nodes_mm"]), len(self.mesh["elements"])))
        self.assertEqual(common.unpack(sh["quads"]), [i for q in self.mesh["elements"] for i in q])
        names = sh["member_names"]
        self.assertEqual([names[round(i)] for i in common.unpack(sh["member"])], self.mesh["element_member"])
        self.assertEqual([m["n"] for m in o["modes"]], [1, 5])
        self.assertEqual([m["kind"] for m in o["modes"]], ["body", "frame"])
        self.assertEqual(o["first"], {"body": 0, "frame": 1, "rollbar": None})
        self.assertEqual(o["beam_first"]["frame"], common.result(self.rec, "first_frame_mode_beam"))
        self.assertEqual(len(o["wheels"]), len(self.design["supports"]))

    def test_fields_packed_without_loss(self):
        shapes = [f for f in self.rec["fields"] if f["kind"] == "mode_shape"]
        strains = [f for f in self.rec["fields"] if f["kind"] == "stress"]
        for m, f, s in zip(self.o["modes"], shapes, strains):
            got = common.unpack(m["shape"])
            want = [c for p in f["per_mesh_node"] for c in p]
            self.assertEqual(m["shape"]["dtype"], "i16")
            self.assertLessEqual(max(abs(a - b) for a, b in zip(got, want)), 0.6 / 32767)
            self.assertLessEqual(max(abs(a - b) for a, b in zip(common.unpack(m["strain"]), s["per_element"])), 0.6 / 32767)

    def test_beam_pairing_from_the_record(self):
        for m in self.o["modes"]:
            e = next(e for e in self.rec["effects"] if e["name"].startswith(f"shell mode {m['n']} ("))
            b = m["beam"]
            self.assertIn(f"vs beam mode {b['mode']} (", e["name"])
            self.assertIn(f"MAC {b['mac']:.2f}", e["name"])
            self.assertAlmostEqual(b["diff"], e["value"])

    def test_joint_nodes_by_hand(self):
        j = nvh_shell.joint_nodes(self.design, self.mesh)
        P = self.mesh["nodes_mm"]
        xyz = {n["id"]: n["xyz_mm"] for n in self.design["nodes"]}
        kept = {m["id"]: m for m in self.design["members"] if m["id"] in set(self.mesh["element_member"])}
        ends = {m[k] for m in kept.values() for k in ("node_a", "node_b")}
        for nid in ends:
            self.assertTrue(j[nid], nid)
            far = max(math.dist(P[i], xyz[nid]) for i in j[nid])
            self.assertLess(far, 150.0)   # half the section's diagonal + 1.5 elements of 20 mm
        # a mesh with the translation (1, 2, 3) everywhere: every design node and wheel centre moves by it
        u = [[1.0, 2.0, 3.0]] * len(P)
        per = nvh_shell.node_motion(self.design, self.mesh, j, u)
        for v in per.values():
            for a, b in zip(v, (1.0, 2.0, 3.0)):
                self.assertAlmostEqual(a, b, places=9)

    def test_parts_and_wheels_move_in_the_bounce(self):
        m = self.o["modes"][0]
        moving = [i for i, ns in enumerate(self.o["part_nodes"]) if ns]
        self.assertTrue(moving)
        self.assertAlmostEqual(max(m["parts"]["rel"]), 1.0)
        # each tyre takes its node's motion from the mesh (the trimmed mesh holds no support's frame node: none moves)
        f = next(f for f in self.rec["fields"] if f["kind"] == "mode_shape")
        per = nvh_shell.node_motion(self.design, self.mesh, nvh_shell.joint_nodes(self.design, self.mesh), f["per_mesh_node"])
        for w, got in zip(self.o["wheels"], m["wheels"]):
            want = per.get(w["node"], [0.0, 0.0, 0.0])
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=9)
        self.assertTrue(any(abs(v[2]) > 0.05 for v in per.values()))   # the bounce lifts the kept frame nodes

    def test_command_line_and_no_paths(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, "-m", "viewer.src.nvh_shell", str(FX / "shell"), "--out", d],
                               cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            path = Path(d) / "nvh_shell.js"
            self.assertEqual(common.read_data_js(path)[0], "nvh_shell")
            self.assertNotRegex(path.read_text(encoding="ascii"), no_paths.IN_JSON_SHORT)
            src = sum(f.stat().st_size for f in (FX / "shell").rglob("*.json"))
            self.assertLess(path.stat().st_size, src / 3)   # packed: much smaller than the record and mesh

    def test_design_key(self):
        self.assertEqual(self.o["source"]["design_key"], common.design_key(self.design))
        self.assertNotEqual(self.o["source"]["design_key"], make("nvh")["source"]["design_key"])


def _system_python_with_playwright():
    exe = shutil.which("py")
    if not exe:
        return None
    r = subprocess.run([exe, "-c", "import playwright.sync_api"], capture_output=True)
    return exe if r.returncode == 0 else None


def js_fmt(v):
    """HV.fmt (js/core.js) for 0.01 <= |v| < 1000: 4 significant digits, no trailing zeros."""
    assert 0.01 <= abs(v) < 1000
    return f"{v:.4g}"


class TyreRowTest(unittest.TestCase):
    """The tyre stiffness as an input row, from the record's design, for the tools that use it (common.tyre_row)."""

    def test_row_from_the_design(self):
        basis = ("60 N/mm, range 45 to 90 N/mm: HIRAKU's judgement from three sources on ATV-class tyres (not "
                 "measurements of this vehicle's tyre)")
        d = {"variables": {"tyre_stiffness": {"value": 60.0, "unit": "N/mm", "basis": basis}}}
        r = common.tyre_row(d, "crash_beam")
        self.assertEqual((r["label"], r["value"]), ("タイヤの剛さ", "60 N/mm"))
        self.assertIn("3 つの資料からの判断 45〜90 の中ほど", r["source"])
        self.assertIn("150 N/mm", common.tyre_row({"variables": {"tyre_stiffness": {"value": 150.0, "unit": "N/mm"}}}, "tilt")["value"])
        self.assertIn("出典なし", common.tyre_row({"variables": {"tyre_stiffness": {"value": 150.0, "unit": "N/mm"}}}, "tilt")["source"])
        for tool in ("modal", "modal_shell", "frame_static", "thermal", "drive_energy", "assembly", "crash"):
            self.assertIsNone(common.tyre_row(d, tool), tool)   # K1 on the suspension springs, K3, K4, crash A: no tyre

    def test_crash_record_design(self):
        o = make("crash", "crash_contact")
        p = common.find_analysis(FX / "crash_contact", "crash_beam")[0]
        vdir = p if p.is_dir() else p.parents[1]
        t = (common.read_json(vdir / "design.json").get("variables") or {}).get("tyre_stiffness")
        self.assertEqual(o["tyre"]["value"], f"{t['value']:g} N/mm" if t else "設計にない (ツールの既定)")
        self.assertIsNone(make("crash")["tyre"])   # crash A (1-D) has no tyre


class NamesTest(unittest.TestCase):
    """The page's names from where HIRAKU's screen takes them (viewer/src/names.py -> viewer/js/names.js)."""

    def test_names_js_up_to_date(self):
        self.assertEqual((VIEWER / "js" / "names.js").read_text(encoding="utf-8"), names.render(),
                         "run python -m viewer.src.names")

    def test_field_names_from_the_contract(self):
        from hiraku import contract
        labels = names.field_labels()
        for f, d in names.FIELD_DOMAIN.items():
            with self.subTest(field=f):
                if f == "fatigue":
                    self.assertEqual(labels[f], "疲労")
                else:
                    self.assertTrue(labels[f].startswith(contract.DOMAIN_NAMES_JA[d]), labels[f])
                # each field's script takes its button's label from js/names.js, not a text of its own
                self.assertRegex((VIEWER / "js" / f"{f}.js").read_text(encoding="utf-8"),
                                 r"\n  label: HV\.FIELD_LABEL\." + f + r",")
        self.assertEqual((labels["handling"], labels["powertrain"], labels["nvh"]), ("操安", "動力", "音振"))
        self.assertEqual(labels["strength"], "強度信頼性 (静的強度)")
        self.assertEqual(len(set(labels.values())), len(labels))   # the two strength fields told apart
        self.assertEqual(set(names.FIELD_DOMAIN), set(re.search(r"FIELD_ORDER = \[([^\]]*)\]", (VIEWER / "js" / "main.js")
                         .read_text(encoding="utf-8")).group(1).replace("'", "").replace(" ", "").split(",")))
        self.assertEqual(names.title("nvh", "x", "シェル"), "音振 (シェル): x")
        self.assertEqual(names.retitle({"title": "強度: y"})["title"], "強度信頼性: y")

    def test_quantity_names_from_labels_js(self):
        q = names.quantity_names()
        text = (ROOT / "web" / "js" / "labels.js").read_text(encoding="utf-8")
        block = text[text.index("const QUANTITY_JA = {"):]
        block = block[:block.index("\n};")]
        keys = set(re.findall(r'^\s*([A-Za-z_]\w*):\s*"', block, re.M))
        self.assertEqual(set(q), keys)
        self.assertEqual(q["occupant_space_margin"], "乗員の空間の余裕")
        self.assertEqual(names.channel_names()["deceleration"], "減速度")
        self.assertFalse(set(names.VIEWER_QUANTITY_JA) & set(q))   # the viewer's own words only where labels.js has none


class PageTest(unittest.TestCase):
    """The page opened from disk in headless Chromium, with each field's data made from the fixtures.
    Needs Playwright for the system Python (py) and the network (three.js from jsDelivr)."""

    @classmethod
    def setUpClass(cls):
        cls.py = _system_python_with_playwright()
        if not cls.py:
            raise unittest.SkipTest("playwright is not available for the system Python (py)")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name) / "viewer"
        shutil.copytree(VIEWER, cls.dir, ignore=shutil.ignore_patterns("data", "src", "__pycache__", "*.py"))
        for f in SCRIPTS:
            common.write_data(f, make(f), cls.dir / "data")
        common.write_data("nvh_shell", make_shell(), cls.dir / "data")
        # the same page with the crash_beam (B) record as the crash field
        cls.dir_b = Path(cls.tmp.name) / "viewer_b"
        shutil.copytree(cls.dir, cls.dir_b)
        common.write_data("crash", make("crash", "crash_b"), cls.dir_b / "data")
        # a beam record marked as the shell's design: the beam / shell switch is offered
        same = make("nvh")
        same["source"]["design_key"] = make_shell()["source"]["design_key"]
        common.write_data("nvh", same, cls.dir_b / "data")
        # the crash_beam record with contact (K2 9bffe19c on) as the crash field
        cls.dir_c = Path(cls.tmp.name) / "viewer_c"
        shutil.copytree(cls.dir, cls.dir_c)
        cls.crash_c = make("crash", "crash_contact")
        common.write_data("crash", cls.crash_c, cls.dir_c / "data")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _check(self, d, f):
        r = subprocess.run([self.py, str(Path(__file__).with_name("page_check.py")), str(d), f, "", "3"],
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout)
        self.assertEqual(out["errors"], [])
        self.assertGreater(out["drawn_px"], 1000)
        return out

    def test_crash_b_page(self):
        out = self._check(self.dir_b, "crash")
        self.assertIn("B: 梁", out["title"])
        self.assertIn("停止", out["hud"])
        self.assertIn("tower_post_RA2", out["hud"])
        self.assertTrue(out["speed_shown"])
        self.assertEqual(out["layout_overlap"], [])
        self.assertIn("軸応力", out["panel"])
        self.assertIn("ホットスポット", out["inputs"] + out["page_text"])
        self.assertGreater(out["boxes_moved"], 0)

    def test_crash_contact_page_header_names_and_range(self):
        # at t = 0 (nothing deformed yet): the header's least room is the record's, the colour range the run's largest
        out = self._check(self.dir_c, "crash")
        rec = common.find_analysis(FX / "crash_contact", "crash_beam")[1]
        least = common.result(rec, "occupant_space_margin")
        self.assertIn(f"乗員の空間の余裕 {js_fmt(least)} mm", out["hud"])
        self.assertAlmostEqual(self.crash_c["summary"]["margin"]["value"], least)
        # the default colour: the end hinge rotation, one hue (no red), the run's largest
        self.assertAlmostEqual(out["contour"]["max"], self.crash_c["colour"]["hinge_max"], places=3)
        self.assertEqual(out["contour"].get("scale"), "seq")
        self.assertIn("ヒンジ回転", out["contour"]["title"])
        self.assertGreater(out["contour"]["max"], 1.0)
        self.assertIn("軸応力 (5 ms の平均)", out["panel"])
        self.assertIn("軸応力 (そのまま)", out["panel"])
        self.assertIn("計算全体の最大", out["contour"]["note"])
        # quantity names: the Japanese name, the code name after it (small grey type)
        self.assertIn("(occupant_space_margin)", out["codes"])
        self.assertIn("乗員の空間の余裕 (occupant_space_margin)", out["inputs"])
        self.assertIn("(frame_split_fraction)", out["codes"])
        # the field buttons: the contract's domain names, in the page's order
        self.assertEqual(out["switch"], [names.field_labels()[f] for f in ["strength", "fatigue", "rops", "nvh", "crash", "handling",
                                                                            "powertrain", "aero", "thermal", "assembly"]])

    def test_nvh_parts_move_with_the_frame(self):
        out = self._check(self.dir, "nvh")
        self.assertFalse(out["speed_shown"])
        self.assertEqual(out["layout_overlap"], [])
        self.assertGreater(out["boxes_moved"], 0)         # the part boxes follow the mode (phase 90 deg)
        self.assertGreater(out["tyres"], 0)

    def test_nvh_shell_page(self):
        out = self._check(self.dir, "nvh_shell")
        self.assertEqual((out["shown_field"], out["variant"]), ("nvh", "nvh_shell"))
        self.assertIn("[シェル]", out["title"])
        self.assertIn("梁のモード", out["tline"])
        self.assertIn("実寸ではない", out["hud"])
        self.assertIn("要素のひずみ", out["hud"])
        self.assertEqual(out["over_view"], [])
        self.assertEqual(out["layout_overlap"], [])
        self.assertGreater(out["boxes_moved"], 0)
        self.assertEqual(out["tyres"], 4)
        self.assertIn("別の設計", out["panel"])   # the beam fixture is another design: no comparison offered
        self.assertNotRegex(out["page_text"], no_paths.ON_PAGE)
        self.assertFalse(out["notes_open"])
        beam = self._check(self.dir, "nvh")
        self.assertEqual(beam["variant"], "nvh")
        self.assertIn("[梁]", beam["title"])
        same = self._check(self.dir_b, "nvh_shell")
        self.assertIn("モデル", same["panel"])
        self.assertNotIn("別の設計", same["panel"])

    def test_crash_a_page_says_no_shape(self):
        out = self._check(self.dir, "crash")
        self.assertIn("1次元モデル", out["hud"])

    def test_nvh_page_strain(self):
        out = self._check(self.dir, "nvh")
        self.assertIn("部材のひずみ", out["hud"])
        self.assertIn("実寸ではない", out["hud"])

    def test_part_click_returns_category(self):
        for d, f in ((self.dir, "nvh"), (self.dir, "crash"), (self.dir_b, "crash"), (self.dir, "assembly")):
            with self.subTest(field=f, b=d == self.dir_b):
                out = self._check(d, f)
                self.assertIn(out["part_pick"].get("cat"), set(common.CATEGORIES), out["part_pick"])
                self.assertIn("節点" if f != "assembly" else "形だけ", out["part_info"])
                self.assertIn("部品と計算での扱い", out["panel"])

    def test_fields_load_without_console_errors(self):
        for f in SCRIPTS:
            with self.subTest(field=f):
                r = subprocess.run([self.py, str(Path(__file__).with_name("page_check.py")), str(self.dir), f, "", "3"],
                                   capture_output=True, text=True, encoding="utf-8", timeout=300)
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                out = json.loads(r.stdout)
                self.assertEqual(out["errors"], [])
                self.assertEqual(out["shown_field"], f)
                self.assertGreater(out["nT"], 1)
                self.assertGreater(out["drawn_px"], 1000)
                self.assertNotIn("NOT COMPUTED", out["page_text"])
                self.assertNotRegex(out["page_text"], no_paths.ON_PAGE)   # no file paths on screen
                self.assertEqual(out["over_view"], [])                               # no text over the 3D view
                self.assertFalse(out["notes_open"])                                  # explanations closed by default


if __name__ == "__main__":
    unittest.main()
