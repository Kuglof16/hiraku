"""The screen on records it once read wrongly (a code review of web/js):

- lane-change cones (points, no size) and gates (lines, [0, width, 0]) as the K2 tool writes them
- a verdict whose limit is 0 (margin_pct null, the margin in its own unit)
- the "最大" tag: the field's own largest value and where it is
- an A6 tile of a field with frames: the moment with the largest value, named
- a solid (hex8 / hex20) mesh: its outer faces, its edges, no shell free edges, counts by kind
- the event feed: a read that does not move on does not ask again at once
- an unknown cost (project.cost_usd null) said as unknown, and the run list's driver tag

The values put in the records are shape examples (not results); the counts checked are
computed here from the shapes. Browser tests: python -m tests.web.browser (needs Playwright).
"""

import copy
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.solvers.k2_vehicle.shelf.lane_change import dlc_scene
from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from tests.web.test_browser import ENABLED, sync_playwright


def sample_events():
    return [copy.deepcopy(json.loads(line)) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]


def _of(events, kind):
    return next(e for e in events if e["kind"] == kind)


def hex8_block():
    """Two hex8 bricks side by side along x (a 400 x 200 x 100 mm block), shape example.
    Node (i, j, k) at x 200 i, y -100 + 200 j, z 300 + 100 k is number 6 k + 3 j + i."""
    nodes = [[200.0 * i, -100.0 + 200.0 * j, 300.0 + 100.0 * k] for k in range(2) for j in range(2) for i in range(3)]
    n = lambda i, j, k: 6 * k + 3 * j + i  # noqa: E731
    # contract: nodes 0-3 counter-clockwise seen from +zeta (the bottom face), 4-7 above them
    elements = [[n(i, 0, 0), n(i + 1, 0, 0), n(i + 1, 1, 0), n(i, 1, 0), n(i, 0, 1), n(i + 1, 0, 1), n(i + 1, 1, 1), n(i, 1, 1)] for i in range(2)]
    return {"mesh_id": "mesh-0001", "design_version": 1, "element_type": "solid", "formulation": "hex8 (shape example)",
            "nodes_mm": nodes, "elements": elements, "element_member": ["rail_L0", "rail_L0"],
            "notes": "shape example: two bricks (screen tests)"}


def hex_events():
    """The sample run with the hex8 block's mesh.created and a stress field on it."""
    events = sample_events()
    mesh = hex8_block()
    ana_ev = _of(events, "analysis.finished")
    ana_ev["data"]["fields"].append({"kind": "stress", "label": "ソリッドの応力 (形の見本)", "unit": "MPa", "mesh_id": mesh["mesh_id"],
                                     "per_element": [40.0, 90.0]})
    created = {"seq": 0, "ts": ana_ev["ts"], "t": ana_ev["t"] - 0.05, "run_id": events[0]["run_id"], "kind": "mesh.created",
               "agent": "system", "design_version": 1, "data": mesh}
    i = events.index(ana_ev)
    events = events[:i] + [created] + events[i:]
    for k, e in enumerate(events):
        e["seq"] = k
    return events, mesh


def dlc_events():
    """The sample run whose analysis has the lane-change tool's own cones and gates."""
    events = sample_events()
    scene = dlc_scene(1.6, 1.0)  # the K2 tool's function (vehicle 1.6 m wide, lane to the left)
    _of(events, "analysis.finished")["data"]["scene"] = scene
    return events, scene


def zero_limit_events():
    """The sample run whose verdict is against a limit of 0: margin_pct null, the margin in MPa.
    Shape example: value 2 MPa >= 0 MPa, so the margin is 2 - 0 = 2 MPa."""
    events = sample_events()
    v = _of(events, "verdict.recorded")["data"]
    v.update(value={"value": 2.0, "unit": "MPa"}, op=">=", limit={"value": 0.0, "unit": "MPa"}, margin_pct=None,
             margin={"value": 2.0 - 0.0, "unit": "MPa"}, **{"pass": True})
    return events, v


# Shape example of a field at three moments (frames, no per_member): the largest at the second.
FRAMES = {"t_s": [0.0, 0.4, 0.8],
          "per_member": [{"rail_L0": 10.0, "rail_R0": 8.0}, {"rail_L0": 150.0, "rail_R0": 40.0}, {"rail_L0": 90.0, "rail_R0": 20.0}],
          "max": [10.0, 150.0, 90.0]}


def frames_events():
    events = sample_events()
    f = next(x for x in _of(events, "analysis.finished")["data"]["fields"] if x["kind"] == "stress")
    del f["per_member"]
    f["frames"] = copy.deepcopy(FRAMES)
    return events


class FixturesFitTheContract(unittest.TestCase):
    def test_events_fit_the_contract(self):
        for events in (hex_events()[0], dlc_events()[0], zero_limit_events()[0], frames_events()):
            for e in events:
                self.assertEqual(contract.errors("event", e), [], e["kind"])

    def test_the_tool_writes_cones_as_points_and_gates_as_lines(self):
        _, scene = dlc_events()
        cones = [o for o in scene if o["kind"] == "cone"]
        gates = [o for o in scene if o["kind"] == "gate"]
        self.assertTrue(cones and gates)
        self.assertTrue(all("size_mm" not in o for o in cones))
        self.assertTrue(all(o["size_mm"][0] == 0 and o["size_mm"][2] == 0 and o["size_mm"][1] > 0 for o in gates))


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ReviewFixesBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())  # runs/ is not made: the server has no runs of its own
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, events=None, query="run=sample&pace=0&t=0"):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        if events is not None:
            page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        return page, errors

    def test_lane_change_cones_and_gates_are_drawn_on_the_road_from_the_front(self):
        events, scene = dlc_events()
        page, errors = self.open(events)
        got = page.evaluate(
            """() => { const b = window.hiraku.bench; const objs = b.sceneDesc.objects;
              // boxes in the road's frame (ISO mm): the world box taken back through props.world
              const w = b.props.world; w.updateMatrixWorld(true); const back = w.matrixWorld.clone().invert();
              const boxes = w.children.slice(0, objs.length).map((g) => {
                const C = b.bboxIso.constructor; const box = new C().setFromObject(g).applyMatrix4(back);
                return [box.min.toArray(), box.max.toArray()]; });
              return { kinds: objs.map((o) => o.kind), boxes, view: b.view }; }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["kinds"], [o["kind"] for o in scene])
        self.assertEqual(got["view"], "front")
        for o, (lo, hi) in zip(scene, got["boxes"]):
            x, y, z = o["position_mm"]
            # stands on the road at its recorded point, and has a height to be seen
            self.assertAlmostEqual(lo[2], z, delta=1e-6, msg=o)
            self.assertGreater(hi[2] - lo[2], 100, o)
            self.assertLessEqual(lo[0], x)
            self.assertGreaterEqual(hi[0], x)
            if o["kind"] == "gate":
                # its two posts at the line's ends
                w = o["size_mm"][1]
                self.assertLess(lo[1], y - w / 2 + 1e-6)
                self.assertGreater(hi[1], y + w / 2 - 1e-6)
            else:
                self.assertLess(lo[1], y)
                self.assertGreater(hi[1], y)
        page.close()

    def test_a_zero_limit_verdict_shows_its_margin_in_its_unit(self):
        events, v = zero_limit_events()
        page, errors = self.open(events)
        want = page.evaluate("async (q) => { const { fmtQ } = await import('/js/labels.js'); return fmtQ(q, 3); }", v["margin"])
        got = page.evaluate(
            """() => ({ log: [...document.querySelectorAll("#log li")].map((li) => li.textContent).find((x) => x.includes("判定")) ?? "",
                        card: document.getElementById("card-cmp").textContent })"""
        )
        self.assertEqual(errors, [])
        self.assertIn(f"余裕 {want}", got["log"])
        self.assertIn(f"余裕 {want}", got["card"])
        self.assertNotIn("— %", got["log"] + got["card"])
        page.close()

    def test_the_max_tag_is_the_fields_largest_value_and_its_place(self):
        page, errors = self.open()
        got = page.evaluate(
            """async () => { const { contourOf } = await import('/js/fields.js');
              const field = { kind: 'damage', unit: '1', per_member: { m1: 0.2, m2: 0.7, m3: 0.4 } };
              // a result of another quantity, in the same unit, at another member
              const other = { analysis_id: 'ana-0009', status: 'ok', fields: [field],
                results: [{ quantity: 'min_safety_factor', value: 1.8, unit: '1', where: 'm3' }] };
              const same = { ...other, results: [...other.results, { quantity: 'fatigue_damage', value: 0.7, unit: '1', where: 'm2' }] };
              const a = contourOf(other, field, null); const b = contourOf(same, field, null);
              return [a.where, a.result, b.result]; }"""
        )
        self.assertEqual(errors, [])
        where, a, b = got
        self.assertEqual(where, "m2")
        self.assertEqual(a, {"quantity": "damage", "value": 0.7, "unit": "1", "where": "m2"})
        self.assertEqual(b["quantity"], "fatigue_damage")  # the recorded result that is this value, named
        page.close()

    def test_a_tile_of_a_field_with_frames_shows_its_peak_moment(self):
        page, errors = self.open(frames_events(), query="run=sample&pace=0&t=0&tab=grid")
        k = max(range(len(FRAMES["t_s"])), key=lambda i: FRAMES["max"][i])
        peak = FRAMES["per_member"][k]
        top = max(peak, key=peak.get)
        want = page.evaluate(
            "async ([v, t]) => { const { fmtVU, fmt } = await import('/js/labels.js'); return [fmtVU(v, 'MPa', 4), fmt(t, 3)]; }",
            [peak[top], FRAMES["t_s"][k]],
        )
        cap = page.evaluate(
            """async () => { await window.hiraku.seek(window.hiraku.player.playhead);
              return document.querySelector('#tiles .tile[data-domain="strength"] .cap').textContent; }"""
        )
        self.assertEqual(errors, [])
        self.assertIn(f"最大 {want[0]}", cap)
        self.assertIn(f"(t {want[1]} s)", cap)
        self.assertLessEqual(len(cap), 40)  # a short caption
        page.close()

    def test_a_hex8_mesh_draws_its_outer_faces_and_edges(self):
        events, mesh = hex_events()
        page, errors = self.open(events)
        got = page.evaluate(
            """(id) => { const b = window.hiraku.bench; const o = b.shellObjs[id];
              return { shown: !!o && o.group.visible, verts: o ? o.vElem.length : 0, elems: o ? [...new Set(o.vElem)] : [],
                       edges: o ? o.edges.geometry.index.count / 2 : 0,
                       log: [...document.querySelectorAll("#log li")].map((li) => li.textContent).find((x) => x.includes("メッシュを作った")) ?? "" }; }""",
            mesh["mesh_id"],
        )
        self.assertEqual(errors, [])
        self.assertTrue(got["shown"])
        # 2 bricks: 12 faces less the shared one (counted twice) = 10 outer faces, 2 triangles each
        self.assertEqual(got["verts"], 10 * 2 * 3)
        self.assertEqual(sorted(got["elems"]), [0, 1])
        # edges of a 2 x 1 x 1 block's surface: 4 x 2 along x, 3 x 2 along y, 3 x 2 along z
        self.assertEqual(got["edges"], 8 + 6 + 6)
        self.assertIn("六面体 8 節点 2", got["log"])
        self.assertNotIn("三角", got["log"])
        page.close()

    def test_mesh_kinds_faces_edges_and_free_edges(self):
        page, errors = self.open()
        got = page.evaluate(
            """async (blk) => { const m = await import('/js/meshkind.js'); const q = await import('/js/quality.js');
              const hex20 = { nodes_mm: Array.from({ length: 20 }, () => [0, 0, 0]), elements: [Array.from({ length: 20 }, (_, i) => i)], element_type: 'solid' };
              const quad = { nodes_mm: [[0,0,0],[1,0,0],[1,1,0],[0,1,0]], elements: [[0, 1, 2, 3]], element_type: 'shell' };
              const mixed = { nodes_mm: blk.nodes_mm.concat([[0, 0, 0]]), elements: [...blk.elements, [0, 1, 4, 3], [0, 1, 12]],
                              element_kind: ['hex8', 'hex8', 'quad4', 'tri3'], element_type: 'mixed' };
              const f20 = m.outerFaces(hex20);
              return { hex8Free: q.freeEdges(blk).length, quadFree: q.freeEdges(quad).length,
                       hex20Faces: f20.length, hex20Tris: f20.reduce((s, f) => s + f.tris.length, 0), hex20Edges: m.edgePairs(hex20).length / 2,
                       counts: m.kindCounts(mixed), text: m.kindCountText(mixed), mixedFree: q.freeEdges(mixed).length }; }""",
            hex8_block(),
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["hex8Free"], 0)  # a solid has no free edges
        self.assertEqual(got["quadFree"], 4)
        # one hex20: 6 faces of 6 triangles (round the mid-edge nodes), 12 edges of 2 segments
        self.assertEqual((got["hex20Faces"], got["hex20Tris"], got["hex20Edges"]), (6, 36, 24))
        self.assertEqual(got["counts"], {"hex8": 2, "quad4": 1, "tri3": 1})
        self.assertEqual(got["text"], "四角 1・三角 1・六面体 8 節点 2")
        # the quad and the triangle share the side 0-1: 3 + 2 free sides
        self.assertEqual(got["mixedFree"], 5)
        page.close()

    def test_an_unknown_cost_is_said_so_not_zero(self):
        # project.cost_usd null (claude/lead-refactor ea80fa4: driver claude_code_plugin, HIRAKU cannot
        # know the cost). This branch's schema does not take null yet, so these events are not checked here.
        events = sample_events()
        _of(events, "run.started")["data"].update(cost_usd=None, driver="claude_code_plugin")
        for e in events:
            if e["kind"] == "cost.updated":
                e["data"]["cost_usd"] = None
        page, errors = self.open(events)
        pill = page.evaluate("() => document.getElementById('pill-cost').textContent")
        self.assertEqual(errors, [])
        # no value: a dash only (neither 不明 nor the limit)
        self.assertEqual(pill.strip(), "費用 —")
        # (and on the screen for people no pill at all: test_judge_words_browser)
        self.assertFalse(page.evaluate("() => document.getElementById('pill-cost').checkVisibility()"))
        self.assertNotIn("$", pill)
        self.assertNotIn("不明", pill)
        page.close()
        # a scripted run calls no Claude: the same dash
        events = sample_events()
        _of(events, "run.started")["data"].update(driver="scripted")
        page, errors = self.open(events)
        self.assertEqual(page.evaluate("() => document.getElementById('pill-cost').textContent").strip(), "費用 —")
        self.assertEqual(errors, [])
        page.close()
        # a known cost is still shown as before
        page, errors = self.open(sample_events())
        cost = next(e["data"]["cost_usd"] for e in reversed(sample_events()) if e["kind"] == "cost.updated")
        pill = page.evaluate("() => document.getElementById('pill-cost').textContent")
        self.assertEqual(errors, [])
        self.assertIn(f"費用 ${cost:.2f}", pill)
        page.close()

    def test_the_run_list_says_what_drove_each_run(self):
        from hiraku.webserve.launch import Launcher

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        runs = tmp / "runs"
        drivers = {"20261103-100000-plugin": "claude_code_plugin", "20261103-100001-sdk": "agent_sdk",
                   "20261103-100002-script": "scripted", "20261103-100003-old": None}
        for rid, drv in drivers.items():
            (runs / rid).mkdir(parents=True)
            data = {"want": rid, **({"driver": drv} if drv else {})}
            (runs / rid / "events.jsonl").write_text(json.dumps({"seq": 0, "t": 0, "kind": "run.started", "data": data}) + "\n", encoding="utf-8")
        # the list's launcher without an API key, whatever the window running the test has (nothing is started)
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=Launcher(runs, env={}))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/?list=1", wait_until="domcontentloaded")
        page.wait_for_selector("#runlist table")
        got = page.evaluate(
            """() => Object.fromEntries([...document.querySelectorAll("#runlist tr[data-run]")]
                 .map((tr) => [tr.dataset.run, [tr.querySelector(".rsum").textContent, !!tr.closest("details.older")]]))"""
        )
        self.assertEqual(errors, [])
        # the row's title starts with how it was driven (test_runlist_rows_browser has the rest of it);
        # a record without the driver is kept under the closed 「この PC の記録」
        words = {"claude_code_plugin": "Claude の運転", "agent_sdk": "Claude の運転", "scripted": "決まった手順", None: "記録"}
        for rid, drv in drivers.items():
            self.assertEqual(got[rid], [words[drv], drv is None], rid)
        page.close()

    def test_the_feed_does_not_ask_again_at_once_when_a_read_does_not_move_on(self):
        page, errors = self.open()
        calls = page.evaluate(
            """async () => { const { EventFeed } = await import('/js/player.js'); const real = window.fetch; let calls = 0;
              // a line still being written: the server's next_offset stays at 5 of 100 bytes
              // (the ndjson form: no lines, where to read next in the headers)
              window.fetch = async () => { calls += 1; return new Response('', { headers: { 'X-Hiraku-Next-Offset': '5', 'X-Hiraku-Size': '100', 'X-Hiraku-Reset': '0', 'X-Hiraku-Bad-Lines': '0' } }); };
              try { const feed = new EventFeed('x', () => {}); await feed.readAll(); } finally { window.fetch = real; }
              return calls; }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(calls, 2)  # 0 -> 5 moved on, so once more; 5 -> 5 did not, so back to the poll
        page.close()


if __name__ == "__main__":
    unittest.main()
