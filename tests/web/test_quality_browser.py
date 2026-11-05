"""The mesh's own views on the workbench (web/js/quality.js) on the record of
quality_fixture: the mesh check's colours, the elements past a limit, the zoom to the
worst element, the free edges, the list, the thickness colours and the setup's marks.

The fixture's fit to the contract is checked only when the contract has mesh.quality
(and analysis.setup); the browser tests run with python -m tests.web.browser and need Playwright (see
test_browser.py).
"""

import copy
import json
import math
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import make_server
from tests.web.quality_fixture import THICK_MM, failed_events, free_edges, quality_events
from tests.web.test_browser import ENABLED, sync_playwright

MESH_PROPS = contract.schema()["$defs"].get("mesh", {}).get("properties", {})
ANA_PROPS = contract.schema()["$defs"].get("analysis", {}).get("properties", {})
HAS_QUALITY = "quality" in MESH_PROPS
HAS_SETUP = "setup" in ANA_PROPS

OVER_HEX = "#6d1b3b"  # colormap.js OVER_COLOR
# docs/contract.md (pre 処理の中身): the names of analysis.setup.checks
CONTRACT_CHECKS = ("equilibrium_residual", "load_balance_residual", "model_mass", "mass_difference", "free_rigid_body_modes")

# The colour drawn on each element of the shown shell (the first corner's), as linear
# rgb strings, and the colour of a hex the same way the workbench sets it.
PAINT = """(hex) => { const b = window.hiraku.bench; const o = Object.values(b.shellObjs).find((x) => x.group.visible);
  const col = o.faces.geometry.getAttribute("color").array; const per = []; let last = -1;
  for (let k = 0; k < o.vElem.length; k += 1) if (o.vElem[k] !== last) { last = o.vElem[k]; per.push([col[k * 3], col[k * 3 + 1], col[k * 3 + 2]].map((x) => x.toFixed(5)).join(",")); }
  const C = o.faces.material.color.constructor; const ref = hex ? new C().set(hex).toArray().map((x) => x.toFixed(5)).join(",") : null;
  return { per, ref, mesh: o.rec.mesh_id }; }"""

LEGEND = """() => ({ kinds: [...document.querySelectorAll("#legend .kinds:not(.variants):not(.metrics) button")].map((x) => [x.textContent, x.classList.contains("on")]),
  metrics: [...document.querySelectorAll("#legend .metrics button")].map((x) => [x.dataset.metric, x.classList.contains("on")]),
  acts: [...document.querySelectorAll("#legend .acts button")].map((x) => x.textContent),
  over: document.querySelector("#legend .lim")?.dataset.over ?? null, free: document.querySelector("#legend .fe")?.dataset.free ?? null,
  ttl: document.querySelector("#legend .ttl")?.textContent ?? "", ticks: [...document.querySelectorAll("#legend .tk")].map((x) => x.textContent),
  card: document.getElementById("meshcard") && !document.getElementById("meshcard").hidden ? document.getElementById("meshcard").innerText : null,
  ct: (() => { const c = window.hiraku.bench.keys.contour; return c ? { mode: c.mode ?? null, kind: c.kind, metric: c.metric ?? null, min: c.min, max: c.max, over: c.overCount ?? null } : null; })() })"""


class QualityFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events, cls.mesh, cls.ana, cls.flaws = quality_events()

    @unittest.skipUnless(HAS_QUALITY, "contract without mesh.quality")
    def test_mesh_fits_the_contract(self):
        ev = next(e for e in self.events if e["kind"] == "mesh.created")
        self.assertEqual(contract.errors("mesh", self.mesh), [])
        self.assertEqual(contract.errors("event", ev), [])
        _, failed = failed_events()
        self.assertEqual(contract.errors("mesh", failed), [])

    @unittest.skipUnless(HAS_QUALITY and HAS_SETUP, "contract without mesh.quality / analysis.setup")
    def test_events_fit_the_contract(self):
        for e in self.events:
            self.assertEqual(contract.errors("event", e), [], e["kind"])

    def test_the_flaws_are_in_the_record(self):
        q = self.mesh["quality"]
        n = len(self.mesh["elements"])
        for k, vals in q["per_element"].items():
            self.assertEqual(len(vals), n, k)
        # the squashed element is the one with the smallest corner angle, past its limit
        self.assertEqual(q["metrics"]["min_angle_deg"]["worst_element"], self.flaws["squashed"])
        self.assertLess(q["metrics"]["min_angle_deg"]["worst"], q["metrics"]["min_angle_deg"]["limit"])
        self.assertGreater(q["metrics"]["aspect_ratio"]["worst"], q["metrics"]["aspect_ratio"]["limit"])
        # the hole's four sides are free edges
        fe = set(free_edges(self.mesh["elements"]))
        self.assertTrue(set(self.flaws["hole_edges"]) <= fe)
        self.assertEqual(q["topology"]["free_edges"], len(fe))
        self.assertEqual(q["verdict"], "warn")
        st = self.ana["setup"]
        self.assertEqual((len(st["constraints"]), len(st["loads"]), len(st["checks"])), (4, 2, 2))
        # the checks are named as the contract names them
        self.assertLessEqual(set(st["checks"]), set(CONTRACT_CHECKS))
        self.assertEqual(st["checks"]["mass_difference"]["unit"], "%")
        self.assertAlmostEqual(sum(s["weight"] for s in st["loads"][0]["spread_to"]), 1.0, places=5)


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class QualityBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events, cls.mesh, cls.ana, cls.flaws = quality_events()
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

    def open(self, events=None):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?run=sample&pace=0&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events or self.events)
        return page, errors

    def click(self, page, selector, until):
        page.click(selector)
        page.wait_for_function(until)

    def to_quality(self, page):
        self.click(page, "#legend button[data-mode=quality]", "window.hiraku.bench.keys.contour?.mode === 'quality'")

    def test_quality_colours_the_elements_and_counts_the_ones_past_the_limit(self):
        page, errors = self.open()
        before = page.evaluate(LEGEND)
        self.assertEqual(before["ct"]["kind"], "stress")
        self.assertEqual([k[0] for k in before["kinds"]], ["応力", "変位", "メッシュの品質", "板厚と材料", "解く前の設定"])
        stress = page.evaluate(PAINT, None)["per"]
        self.to_quality(page)
        q = self.mesh["quality"]
        got = page.evaluate(LEGEND)
        self.assertEqual(got["ct"]["metric"], "aspect_ratio")
        self.assertEqual(got["metrics"], [[k, k == "aspect_ratio"] for k in q["per_element"]])
        self.assertEqual(dict(got["kinds"])["メッシュの品質"], True)
        self.assertEqual(dict(got["kinds"])["応力"], False)
        paint = page.evaluate(PAINT, None)["per"]
        self.assertEqual(len(paint), len(self.mesh["elements"]))
        changed = sum(1 for a, b in zip(stress, paint) if a != b)
        self.assertGreater(changed, len(paint) // 2)
        for k, m in q["metrics"].items():
            self.click(page, f"#legend button[data-metric={k}]", f"window.hiraku.bench.keys.contour?.metric === '{k}'")
            got = page.evaluate(LEGEND)
            # the scale's bad end is the limit; the good end the best recorded value
            vals = q["per_element"][k]
            good = max(vals) if k == "min_angle_deg" else min(vals)
            self.assertEqual((got["ct"]["min"], got["ct"]["max"]), (good, m["limit"]), k)
            self.assertEqual(got["ct"]["over"], m["over_limit"], k)
            self.assertEqual(got["over"], str(m["over_limit"]), k)
            drawn = page.evaluate(PAINT, OVER_HEX)
            self.assertEqual(sum(1 for c in drawn["per"] if c == drawn["ref"]), m["over_limit"], k)
            self.assertEqual(drawn["per"][m["worst_element"]], drawn["ref"], k)
        # back to the stress colours
        self.click(page, "#legend button[data-field=stress]", "!window.hiraku.bench.keys.contour?.mode")
        self.assertEqual(page.evaluate(PAINT, None)["per"], stress)
        self.assertIsNone(page.evaluate(LEGEND)["card"])
        self.assertEqual(errors, [])
        page.close()

    def test_zoom_to_the_worst_element(self):
        page, errors = self.open()
        self.to_quality(page)
        self.click(page, "#legend button[data-metric=min_angle_deg]", "window.hiraku.bench.keys.contour?.metric === 'min_angle_deg'")
        self.assertIn("いちばん悪い要素に寄る", page.evaluate(LEGEND)["acts"])
        cam = "() => { const b = window.hiraku.bench; return { pos: b.camera.position.toArray(), target: b.controls.target.toArray(), elem: b.focused ? b.focused.elem : null }; }"
        far = page.evaluate(cam)
        self.click(page, "#legend [data-act=zoom]", "!!window.hiraku.bench.focused")
        near = page.evaluate(cam)
        self.assertEqual(near["elem"], self.mesh["quality"]["metrics"]["min_angle_deg"]["worst_element"])
        self.assertEqual(near["elem"], self.flaws["squashed"])
        self.assertLess(math.dist(near["pos"], near["target"]), math.dist(far["pos"], far["target"]) / 3)
        # the camera looks at the element's centre (its nodes' mean, through the design-to-view matrix)
        P = self.mesh["nodes_mm"]
        c = [sum(P[n][k] for n in self.mesh["elements"][near["elem"]]) / 4 for k in range(3)]
        at = page.evaluate("(c) => { const b = window.hiraku.bench; return b.focused.center.clone().set(...c).applyMatrix4(b.iso.matrix).toArray(); }", c)
        self.assertLess(math.dist(near["target"], at), 1e-6)
        self.click(page, "#legend [data-act=zoom]", "!window.hiraku.bench.focused")
        self.assertLess(math.dist(page.evaluate(cam)["pos"], far["pos"]), 1e-9)
        self.assertEqual(errors, [])
        page.close()

    def test_free_edges_are_drawn_and_matched_with_the_record(self):
        page, errors = self.open()
        self.to_quality(page)
        lines = "() => { const l = window.hiraku.bench.freeEdgeLines; return l ? l.geometry.attributes.instanceStart.count : null; }"
        n = self.mesh["quality"]["topology"]["free_edges"]
        self.assertEqual(page.evaluate(lines), n)
        got = page.evaluate(LEGEND)
        self.assertEqual(got["free"], str(n))
        self.assertIn(f"自由縁\t{n}", got["card"])
        page.close()
        # a record whose count differs: both numbers are on the list
        events = copy.deepcopy(self.events)
        next(e for e in events if e["kind"] == "mesh.created")["data"]["quality"]["topology"]["free_edges"] = n + 3
        page, errors2 = self.open(events)
        self.to_quality(page)
        self.assertEqual(page.evaluate(lines), n)
        self.assertIn(f"記録 {n + 3}・要素から数えて {n}", page.evaluate(LEGEND)["card"])
        self.assertEqual(errors + errors2, [])
        page.close()

    def test_the_list_gives_the_verdict_metrics_topology_and_notes(self):
        page, errors = self.open()
        self.to_quality(page)
        q = self.mesh["quality"]
        card = page.evaluate(
            """() => { const c = document.getElementById("meshcard");
              return { verdict: c.querySelector(".verdict").dataset.verdict, cls: c.querySelector(".verdict").className, text: c.querySelector(".verdict").textContent,
                       unsolved: !!c.querySelector(".unsolved"),
                       rows: Object.fromEntries([...c.querySelectorAll("tr[data-metric]")].map((r) => [r.dataset.metric, [...r.children].slice(1).map((x) => x.textContent)])),
                       topo: Object.fromEntries([...c.querySelectorAll("tr[data-topology]")].map((r) => [r.dataset.topology, r.children[1].textContent])),
                       notes: c.querySelector(".notes")?.textContent ?? "", box: c.getBoundingClientRect().toJSON(), legend: document.getElementById("legend").getBoundingClientRect().toJSON() }; }"""
        )
        self.assertEqual((card["verdict"], card["unsolved"]), ("warn", False))
        self.assertIn("v-warn", card["cls"])
        self.assertIn("注意", card["text"])
        for k, m in q["metrics"].items():
            # each recorded value as the screen writes a value with its unit (labels.js)
            want = page.evaluate(
                """async ([m, u]) => { const { fmtVU } = await import("/js/labels.js");
                  return [fmtVU(m.worst, u), fmtVU(m.p95, u), fmtVU(m.limit, u), String(m.over_limit)]; }""",
                [m, "deg" if k.endswith("_deg") else "1"],
            )
            self.assertEqual(card["rows"][k], want, k)
        self.assertEqual(card["topo"], {k: str(v) for k, v in q["topology"].items()})
        self.assertEqual(card["notes"], q["notes"])
        # the card and the legend do not overlap
        a, b = card["box"], card["legend"]
        self.assertFalse(a["left"] < b["right"] and b["left"] < a["right"] and a["top"] < b["bottom"] and b["top"] < a["bottom"], (a, b))
        self.assertEqual(errors, [])
        page.close()

    def test_a_failed_mesh_shows_its_check_and_says_it_was_not_solved(self):
        events, mesh = failed_events()
        page, errors = self.open(events)
        got = page.evaluate(LEGEND)
        self.assertEqual(got["ct"]["mode"], "quality")
        self.assertIn("解かれていない", got["card"])
        v = page.evaluate("() => { const e = document.querySelector('#meshcard .verdict'); return [e.dataset.verdict, e.className]; }")
        self.assertEqual(v[0], "fail")
        self.assertIn("v-fail", v[1])
        self.assertEqual(errors, [])
        page.close()

    def test_thickness_colours_and_materials(self):
        page, errors = self.open()
        self.click(page, "#legend button[data-mode=thickness]", "window.hiraku.bench.keys.contour?.mode === 'thickness'")
        th = self.mesh["member_thickness_mm"]
        per = [th[m] for m in self.mesh["element_member"]]
        drawn = page.evaluate(PAINT, None)["per"]
        # one colour per thickness, the same on every element of it
        by = {}
        for t, c in zip(per, drawn):
            by.setdefault(t, set()).add(c)
        self.assertEqual(sorted(by), sorted(set(th.values())))
        self.assertIn(THICK_MM, by)
        self.assertTrue(all(len(c) == 1 for c in by.values()), by)
        self.assertEqual(len({next(iter(c)) for c in by.values()}), len(by))
        rows = page.evaluate(
            """() => [...document.querySelectorAll("#meshcard tr[data-t]")].map((r) => [Number(r.dataset.t), ...[...r.children].slice(1).map((x) => x.textContent)])"""
        )
        design = next(e for e in self.events if e["kind"] == "design.created")["data"]
        mat = {m["id"]: design["materials"][m["material"]]["name"] for m in design["members"]}
        want = []
        for t in sorted(set(th.values())):
            ids = [k for k, v in th.items() if v == t]
            want.append([t, str(len(ids)), str(per.count(t)), "、".join(dict.fromkeys(mat[i] for i in ids))])
        self.assertEqual(rows, want)
        self.assertIsNone(page.evaluate("() => window.hiraku.bench.freeEdgeLines"))  # the free edges are the quality view's
        self.assertEqual(errors, [])
        page.close()

    def test_setup_marks_and_list(self):
        page, errors = self.open()
        self.click(page, "#legend button[data-mode=setup]", "window.hiraku.bench.keys.contour?.mode === 'setup'")
        st = self.ana["setup"]
        marks = page.evaluate(
            """() => { const g = window.hiraku.bench.setupMarks; const out = {}; const spread = [];
              for (const o of g.children) { const k = o.userData.kind; if (!k) continue; out[k] = (out[k] ?? 0) + 1;
                if (k === "spread") spread.push([o.userData.node, o.userData.weight, o.material.opacity]); }
              return { counts: out, spread, visible: window.hiraku.bench.overGhosts.visible }; }"""
        )
        trans = sum(1 for c in st["constraints"] for d in c["dofs"] if d.startswith("u"))
        rot = sum(1 for c in st["constraints"] for d in c["dofs"] if d.startswith("r"))
        spread = st["loads"][0]["spread_to"]
        self.assertEqual(marks["counts"], {"fix-t": trans, "fix-r": rot, "load": 2, "moment": 1, "spread": len(spread)})
        self.assertTrue(marks["visible"])
        self.assertEqual([[s[0], s[1]] for s in marks["spread"]], [[s["node"], s["weight"]] for s in spread])
        # darker the larger the weight
        by_w = sorted(marks["spread"], key=lambda s: s[1])
        self.assertEqual([s[2] for s in by_w], sorted(s[2] for s in by_w))
        self.assertAlmostEqual(max(s[2] for s in by_w), 1.0)
        card = page.evaluate(
            """() => { const c = document.getElementById("meshcard");
              return { text: c.innerText, nodes: [...c.querySelectorAll("tr[data-node]")].map((r) => Number(r.dataset.node)),
                       loads: c.querySelectorAll("tr[data-load]").length,
                       checks: Object.fromEntries([...c.querySelectorAll("tr[data-check]")].map((r) => [r.dataset.check, [r.children[0].textContent, r.children[1].textContent]])),
                       ir: !!c.querySelector(".ir") }; }"""
        )
        self.assertEqual(card["nodes"], [c["node"] for c in st["constraints"]])
        self.assertIn(f"拘束 4 点 (並進 {trans}・回転 {rot})", card["text"])
        self.assertEqual(card["loads"], 2)
        self.assertEqual(sorted(card["checks"]), sorted(st["checks"]))
        # every check as recorded, under its Japanese name; the mass difference is the record's, with its sign
        names, fmt = page.evaluate(
            """async (q) => { const { CHECK_JA } = await import("/js/quality.js"); const { fmtVU } = await import("/js/labels.js");
              return [CHECK_JA, Object.fromEntries(Object.entries(q).map(([k, v]) => [k, fmtVU(v.value, v.unit)]))]; }""",
            st["checks"],
        )
        for k in CONTRACT_CHECKS:
            self.assertIn(k, names)
        md = st["checks"]["mass_difference"]["value"]
        self.assertEqual(card["checks"]["model_mass"], [names["model_mass"], fmt["model_mass"]])
        self.assertEqual(card["checks"]["mass_difference"], [names["mass_difference"], ("+" if md > 0 else "") + fmt["mass_difference"]])
        self.assertFalse(card["ir"])
        self.assertEqual(errors, [])
        page.close()
        # inertia relief: said on the list
        events = copy.deepcopy(self.events)
        next(e for e in events if e["kind"] == "analysis.finished")["data"]["setup"]["inertia_relief"] = True
        page, errors = self.open(events)
        self.click(page, "#legend button[data-mode=setup]", "window.hiraku.bench.keys.contour?.mode === 'setup'")
        self.assertIn("慣性リリーフ", page.evaluate("() => document.getElementById('meshcard').innerText"))
        self.assertEqual(errors, [])
        page.close()

    def test_a_mesh_without_quality_has_no_quality_button(self):
        events = copy.deepcopy(self.events)
        del next(e for e in events if e["kind"] == "mesh.created")["data"]["quality"]
        del next(e for e in events if e["kind"] == "analysis.finished")["data"]["setup"]
        page, errors = self.open(events)
        self.assertEqual([k[0] for k in page.evaluate(LEGEND)["kinds"]], ["応力", "変位", "板厚と材料"])
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
