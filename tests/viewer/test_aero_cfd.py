"""The data script of the aero field's CFD variant (viewer/src/aero_cfd.py), on small analytic cases. No GPU, no browser."""

import argparse
import json
import math
import re
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hiraku.cfd.vehicle import moment_centre, to_grid
from viewer.src import aero_cfd, common

DESIGN = Path(__file__).resolve().parents[1] / "verification" / "fixtures" / "n15-v005-design.json"
PATHS = re.compile(r"[A-Za-z]:[\\/]|/" + "Users" + r"/|/home/|\\\\|@[a-z0-9.-]+\.[a-z]{2,}", re.I)  # the home folder's name in pieces, as qa.judge_scan wants
AIR, U_LATTICE = 11.0, 0.05
DX = 0.25


def write_case(folder: Path, yaw: float, design_version: int = 5, solid: bool = True, **extra) -> Path:
    """A case folder as VehicleRun.save writes it: uniform air along +x, a block of solid nodes around the vehicle."""
    nx, ny, nz = 60, 36, 22
    origin = np.array([-5.0, -4.5, 0.0])
    ux = np.full((nz, ny, nx), U_LATTICE, np.float32)
    uy, uz = np.zeros_like(ux), np.zeros_like(ux)
    dr = np.full_like(ux, 3.0e-4)
    if solid:
        z, y, x = np.meshgrid(*(origin[a] + (np.arange(n) + 0.5) * DX for a, n in ((2, nz), (1, ny), (0, nx))), indexing="ij")
        m = (np.abs(x) < 1.0) & (np.abs(y) < 0.5) & (z < 1.0)
        for a in (ux, uy, uz, dr):
            a[m] = np.nan
    folder.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(folder / "fields.npz", dx=DX, origin=origin, u_lattice=U_LATTICE, air_speed_ms=AIR, rho=1.225, dt=0.001, dr=dr, ux=ux, uy=uy, uz=uz)
    summary = {"design_version": design_version, "yaw_deg": yaw, "speed_ms": AIR * math.cos(math.radians(yaw)), "air_speed_ms": AIR, "dx": DX,
               "forces_N": {"Fx": -100.0 - yaw, "Fy": 7.0 * yaw, "Fz": 20.0, "Mx": 1.0, "My": -2.0, "Mz": 3.0 + yaw},
               "coefficients": {"q_Pa": 74.1, "reference_area_m2": 1.78, "Cd": 0.75 + 0.01 * yaw, "CdA_m2": 1.33, "Cy": 0.05 * yaw, "Cl": 0.15},
               "convergence": {"spread_pct": 3.5}, "numerics": {"u_lattice": U_LATTICE}, "assumptions": ["空気は高さによらず一様"], **extra}
    (folder / "case.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return folder


def args_for(run: Path, **kw) -> argparse.Namespace:
    d = dict(run=run, design=DESIGN, status=None, loading="two_cargo", seeds=(6, 4), spacing=0.25, downstream=7.0, yaws=None)
    d.update(kw)
    return argparse.Namespace(**d)


def unpacked(y: dict) -> dict:
    L = y["lines"]
    return {"points": np.array(common.unpack(L["points"])).reshape(-1, 3), "speed": np.array(common.unpack(L["speed"])), "cp": np.array(common.unpack(L["cp"])),
            "time": np.array(common.unpack(L["time"])), "offsets": np.array(common.unpack(L["offsets"]))}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.sweep = cls.root / "sweep"
        for y, name in ((10.0, "y+10.0"), (-5.0, "y-05.0"), (0.0, "y+00.0"), (5.0, "y+05.0")):
            write_case(cls.sweep / name, y)
        cls.D = aero_cfd.build(args_for(cls.sweep))
        cls.design = common.read_json(DESIGN)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class TestSweep(Base):
    def test_shape_of_the_data(self):
        D = self.D
        self.assertEqual(D["field"], "aero_cfd")
        self.assertEqual(D["model"], "cfd")
        for key in ("source", "frame", "parts", "scene", "vectors", "results", "assumptions", "inputs", "notes", "ledger", "wheels", "ground", "nets", "table", "yaws", "status", "on_board"):
            self.assertIn(key, D)
        self.assertEqual(D["source"]["tool"], "cfd_aero")
        self.assertEqual(D["source"]["design_version"], 5)
        self.assertEqual(D["source"]["design_key"], common.design_key(self.design))
        self.assertEqual(len(D["on_board"]), len(D["parts"]))
        self.assertEqual(len(D["ledger"]["parts"]), len(D["parts"]))

    def test_yaws_in_order_of_size_and_the_table_is_each_cases_own(self):
        self.assertEqual([y["yaw_deg"] for y in self.D["yaws"]], [0.0, 5.0, -5.0, 10.0])
        self.assertEqual([r["yaw_deg"] for r in self.D["table"]], [0.0, 5.0, -5.0, 10.0])
        for r in self.D["table"]:
            yaw = r["yaw_deg"]
            self.assertEqual(r["Fx"], -100.0 - yaw)
            self.assertEqual(r["Fy"], 7.0 * yaw)
            self.assertEqual(r["Mz"], 3.0 + yaw)
            self.assertEqual(r["Cd"], 0.75 + 0.01 * yaw)
            self.assertEqual(r["Cy"], 0.05 * yaw)
            self.assertEqual(r["spread_pct"], 3.5)
            for k in ("Fz", "Mx", "My", "CdA_m2", "Cl"):
                self.assertIn(k, r)

    def test_lines_are_in_design_axes_along_the_air_of_that_yaw(self):
        ref, _ = moment_centre(self.design)
        for y in self.D["yaws"]:
            a = unpacked(y)
            R = to_grid(y["yaw_deg"])
            grid = (a["points"] - ref) * 1e-3 @ R.T  # design mm -> grid metres
            self.assertGreater(y["count"], 0)
            for n in range(y["count"]):
                s, e = a["offsets"][n], a["offsets"][n + 1]
                g = grid[s:e]
                # straight along grid +x: the uniform air, whatever the yaw (the steps of 1 mm allow a little)
                np.testing.assert_allclose(g[:, 1:], np.tile(g[0, 1:], (e - s, 1)), atol=2.5e-3)
                self.assertTrue((np.diff(g[:, 0]) > 0).all())

    def test_speed_time_and_cp_come_from_the_field(self):
        a = unpacked(self.D["yaws"][0])
        np.testing.assert_allclose(a["speed"], AIR, atol=0.011)
        np.testing.assert_allclose(a["cp"], 3.0e-4 / 3.0 / (0.5 * U_LATTICE ** 2), atol=0.0011)
        ref, _ = moment_centre(self.design)
        grid = (a["points"] - ref) * 1e-3 @ to_grid(0.0).T
        for n in range(self.D["yaws"][0]["count"]):
            s, e = a["offsets"][n], a["offsets"][n + 1]
            self.assertEqual(a["time"][s], 0.0)
            self.assertTrue((np.diff(a["time"][s:e]) > 0).all())
            np.testing.assert_allclose(a["time"][s:e] * AIR, grid[s:e, 0] - grid[s, 0], atol=3e-3)

    def test_lines_end_at_the_block_or_the_given_downstream_end(self):
        y = self.D["yaws"][0]
        self.assertEqual(sum(y["ends"].values()), y["count"])
        self.assertGreater(y["ends"]["domain"], 0)
        ref, _ = moment_centre(self.design)
        a = unpacked(y)
        grid = (a["points"] - ref) * 1e-3 @ to_grid(0.0).T
        self.assertLessEqual(grid[:, 0].max(), 7.0 + 1e-3)

    def test_the_near_distance_is_measured_from_the_vehicles_box(self):
        for y in self.D["yaws"]:
            near = np.array(y["lines"]["near"])
            self.assertEqual(len(near), y["count"])
            self.assertTrue((near >= 0).all())
            self.assertGreater(near.max(), 0)
            self.assertTrue(all(isinstance(v, int) for v in y["lines"]["near"]))

    def test_lines_on_a_cameras_side_are_marked_for_hiding(self):
        for y in self.D["yaws"]:
            side = y["lines"]["side"]
            self.assertEqual(len(side), y["count"])
            self.assertTrue(set(side) <= {0, 1, 2, 3})
            self.assertIn(1, side)
            self.assertIn(2, side)
            np.testing.assert_allclose(y["side_axis"], to_grid(y["yaw_deg"])[1])
        np.testing.assert_allclose(self.D["centre_mm"], moment_centre(self.design)[0])

    def test_a_line_is_marked_by_its_seed_and_by_where_it_passes(self):
        from hiraku.cfd.streamlines import Lines

        lo, hi = np.array([-1.0, -0.5, 0.0]), np.array([1.0, 0.5, 1.5])
        def line(*pts):
            p = np.array(pts, float)
            return p
        rows = [line((-3, -1, 0.5), (0, -1, 0.5)),  # starts and runs off the y- flank
                line((-3, 1, 0.5), (0, 1, 0.5)),    # the y+ flank
                line((-3, 0, 0.5), (-1, 0, 0.5)),   # the middle, up to the nose
                line((-3, 0, 2.0), (0, -1, 2.0)),   # over the top, drifting to y-: not between the camera and the vehicle
                line((-3, -0.4, 2.0), (0, -0.2, 2.0))]  # inside the width at the seed
        pts = np.vstack(rows)
        off = np.concatenate([[0], np.cumsum([len(r) for r in rows])])
        L = Lines(pts, np.ones(len(pts)), np.zeros(len(pts)), np.zeros(len(pts)), off, np.arange(5), np.zeros(5, np.int8))
        self.assertEqual(aero_cfd.line_sides(L, lo, hi).tolist(), [1, 2, 0, 0, 1])

    def test_results_list_every_quantity_of_every_yaw(self):
        self.assertEqual(len(self.D["results"]), 10 * 4)
        self.assertEqual(self.D["results"][0], {"q": "Fx", "v": -100.0, "u": "N", "where": "横風の角度 0°"})
        self.assertEqual(self.D["results"][10]["where"], "横風の角度 +5°")
        self.assertEqual(self.D["results"][20]["where"], "横風の角度 -5°")

    def test_colour_scale_reaches_the_speeds(self):
        for y in self.D["yaws"]:
            self.assertGreaterEqual(y["speed_top"], AIR)
            self.assertLess(y["speed_top"], 2.0 * AIR)
            self.assertEqual(y["air_speed_ms"], AIR)
        self.assertEqual(aero_cfd.speed_top(np.array([15.9])), 16.0)
        self.assertEqual(aero_cfd.speed_top(np.array([11.0])), 12.0)


class TestGeometry(Base):
    def test_parts_and_wheels(self):
        ids = [p["id"] for p in self.D["parts"]]
        self.assertEqual(ids, [p["id"] for p in self.design["parts"] if p.get("box")])
        self.assertEqual(len(self.D["wheels"]), 4)
        self.assertEqual({w["r"] for w in self.D["wheels"]}, {s["radius_mm"] for s in self.design["supports"]})
        self.assertEqual(self.D["wheels"][0]["c"], self.design["supports"][0]["position_mm"])
        self.assertEqual(self.D["ground"]["z"], moment_centre(self.design)[1])
        self.assertIn(True, self.D["on_board"])

    def test_loading_decides_who_is_on_board(self):
        two = sum(aero_cfd.build(args_for(self.sweep / "y+00.0", loading="two_cargo"))["on_board"])
        empty = sum(aero_cfd.build(args_for(self.sweep / "y+00.0", loading="empty"))["on_board"])
        self.assertLess(empty, two)

    def test_side_net_wires_stay_in_the_plane_of_the_box_at_the_pitch(self):
        part = next(p for p in self.design["parts"] if p.get("kind") == "side_net")
        wires = np.array(aero_cfd.net_wires(part, 50.0)).reshape(-1, 2, 3)
        self.assertGreater(len(wires), 4)
        c = np.array(part["box"]["center_mm"])
        size = np.array(part["box"]["size_mm"])
        np.testing.assert_allclose(np.linalg.norm(wires[:, 0] - wires[:, 1], axis=1).max(), sorted(size)[-1], atol=1e-6)
        self.assertLessEqual(np.linalg.norm(wires - c, axis=2).max(), np.linalg.norm(size) / 2.0 + 1e-6)
        packed = np.array(common.unpack(self.D["nets"]))
        self.assertEqual(packed.size % 6, 0)
        self.assertGreater(packed.size, 0)


class TestStatus(Base):
    def test_default_is_not_checked(self):
        self.assertEqual(self.D["status"]["text"], aero_cfd.STATUS_DEFAULT)
        self.assertIn("確かめていない", self.D["status"]["text"])

    def test_the_records_physically_validated_is_the_status(self):
        for flag, text in ((False, "実験と比べて確かめていない"), (True, "実験と比べて確かめた")):
            with tempfile.TemporaryDirectory() as d:
                c = write_case(Path(d) / "s" / "c1", 0.0, validation="ケースの文")
                (Path(d) / "s" / "aero_record.json").write_text(json.dumps({"kind": "cfd_aero_record", "physically_validated": flag}), encoding="utf-8")
                D = aero_cfd.build(args_for(Path(d) / "s"))
                self.assertEqual(D["status"]["text"], text)

    def test_the_status_received_is_the_status_shown(self):
        with tempfile.TemporaryDirectory() as d:
            c = write_case(Path(d) / "c1", 0.0, validation={"status": "風洞の値と比べて 5 % 以内", "source": "比較の記録"})
            D = aero_cfd.build(args_for(c))
            self.assertEqual(D["status"], {"text": "風洞の値と比べて 5 % 以内", "source": "比較の記録"})
            c2 = write_case(Path(d) / "c2", 0.0, validation="比較はこれから")
            self.assertEqual(aero_cfd.build(args_for(c2))["status"]["text"], "比較はこれから")
            self.assertEqual(aero_cfd.build(args_for(c2, status="指定した文"))["status"]["text"], "指定した文")


class TestFolders(Base):
    def test_one_case_or_a_sweep(self):
        self.assertEqual(aero_cfd.case_folders(self.sweep / "y+05.0"), [self.sweep / "y+05.0"])
        self.assertEqual(len(aero_cfd.case_folders(self.sweep)), 4)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                aero_cfd.case_folders(Path(d))

    def test_the_finest_grid_of_each_case_a_coarser_grid_study_left_out(self):
        s = [{"speed_ms": 11.0, "yaw_deg": 0.0, "dx": 0.08}, {"speed_ms": 11.0, "yaw_deg": 0.0, "dx": 0.056}, {"speed_ms": 11.0, "yaw_deg": 5.0, "dx": 0.08}, {"speed_ms": 0.0, "yaw_deg": 5.0, "dx": 0.08}]
        folders, kept = aero_cfd.finest_per_case([Path("a"), Path("b"), Path("c"), Path("d")], s)
        self.assertEqual([p.name for p in folders], ["b", "c", "d"])
        self.assertEqual(kept, s[1:])

    def test_a_record_of_a_run_finds_its_sweep_or_says_why_not(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run"
            vdir = run / "v005"
            vdir.mkdir(parents=True)
            rec = {"tool": "cfd_aero", "design_version": 5, "load_case_id": "CFD-40kmh-yaw0"}
            self.assertIn("run のフォルダにない", aero_cfd.for_record(run, vdir, rec))
            sweep = run / "cfd" / "v5" / "11mps_56mm"
            write_case(sweep / "c1", 0.0)
            (sweep / "aero_record.json").write_text(json.dumps({"kind": "cfd_aero_record", "physically_validated": False, "analyses": [{"load_case_id": "CFD-40kmh-yaw0"}], "aero_map": {"loading": "two_cargo"}}), encoding="utf-8")
            self.assertIn("design.json", aero_cfd.for_record(run, vdir, rec))
            (vdir / "design.json").write_text(DESIGN.read_text(encoding="utf-8"), encoding="utf-8")
            D = aero_cfd.for_record(run, vdir, rec)
            self.assertEqual(D["field"], "aero_cfd")
            self.assertEqual(D["status"]["text"], "実験と比べて確かめていない")

    def test_only_the_yaws_asked_for(self):
        D = aero_cfd.build(args_for(self.sweep, yaws=[-5.0, 10.0]))
        self.assertEqual([y["yaw_deg"] for y in D["yaws"]], [-5.0, 10.0])
        with self.assertRaises(LookupError):
            aero_cfd.build(args_for(self.sweep, yaws=[33.0]))

    def test_a_case_of_another_design_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            c = write_case(Path(d) / "other", 0.0, design_version=7)
            with self.assertRaises(ValueError):
                aero_cfd.build(args_for(c))

    def test_no_design_is_an_error_and_design_json_beside_the_case_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            c = write_case(Path(d) / "c", 0.0)
            with self.assertRaises(FileNotFoundError):
                aero_cfd.build(args_for(c, design=None))
            (Path(d) / "design.json").write_text(DESIGN.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(aero_cfd.build(args_for(c, design=None))["source"]["design_version"], 5)

    def test_command_line_writes_the_data_file_with_no_paths(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            common.main("aero_cfd", aero_cfd.build, extra=aero_cfd.extra, argv=[str(self.sweep / "y+00.0"), "--design", str(DESIGN), "--out", str(out), "--seeds", "5", "3", "--spacing", "0.25"])
            path = out / "aero_cfd.js"
            field, obj = common.read_data_js(path)
            self.assertEqual(field, "aero_cfd")
            self.assertEqual(obj["source"]["run"], "y+00.0")
            self.assertEqual(obj["yaws"][0]["count"], 15)
            # the text without the packed arrays: nothing that looks like a path or a mail address
            def texts(o):
                if isinstance(o, dict):
                    if "b64" in o:
                        return
                    for v in o.values():
                        yield from texts(v)
                elif isinstance(o, list):
                    for v in o:
                        yield from texts(v)
                elif isinstance(o, str):
                    yield o
            bad = [t for t in texts(obj) if PATHS.search(t)]
            self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
