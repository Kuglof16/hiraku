"""The joints on the 3D (web/js/joints.js): the toggle 接合 of the workbench, its legend, and A7.

The design is the frozen zu copy of offroad-4w v1 (tests/web/zu_designs/), so these tests do not
follow data/designs/ when it is generated again. The joints are counted here from the design on
their own (part.connects_to: one joint per two ends and via; the frame's nodes where two or more
members meet: node.joint, or welded by the rule when it is absent) and compared with what the
screen draws and its legend says. A copy of the design with node.joint written by hand on three
nodes (weld, bolt, pin) checks that each node kind gets its own mark.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path

import numpy as np

from hiraku.solvers.k0_handcalc.rigid import rpy_matrix
from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from tests.web.test_assembly_browser import assembly_events

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

HERE = Path(__file__).resolve().parent
ZU_V1 = HERE / "zu_designs" / "offroad-4w-v1.json"
JOINTS_RUN = "20261104-190000-joints"  # the frozen design alone (run.started, design.created)
HAND_RUN = "20261104-190100-hand"  # hand_design() alone
ASM_RUN = "20261104-190200-asm"  # test_assembly_browser.assembly_events with connects_to on two parts (asm_events)
# The marks of web/js/joints.js JOINT_KINDS, by kind
SHAPE = {"weld": "bead", "bolt": "hex", "bushing": "ring", "ball_joint": "ball", "pin": "pin", "spline": "spline",
         "rest": "dot", "friction": "dot", "wire": "dot", "hose": "dot"}
HAND_NODES = {"weld": None, "bolt": None, "pin": None}  # filled by hand_design(): a node per kind
TOL_MM = 1.0


def design():
    return json.loads(ZU_V1.read_text(encoding="utf-8"))


def joints_events(d=None):
    """The contract sample's run.started and the design (no analyses): the bench with the design only."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    created = next(e for e in src if e["kind"] == "design.created")
    out = [copy.deepcopy(src[0]), {**copy.deepcopy(created), "data": d if d is not None else design()}]
    for i, ev in enumerate(out):
        ev["seq"] = i
    return out


def members_at(d):
    at = {}
    for m in d["members"]:
        for end in (m["node_a"], m["node_b"]):
            at.setdefault(end, []).append(m)
    return at


def node_pieces(d, node):
    """(members at the node, the indices of members going through it): two members of one section
    within acos(0.995) of opposite directions are one tube (web/js/joints.js THROUGH_COS)."""
    xyz = {n["id"]: np.asarray(n["xyz_mm"], dtype=float) for n in d["nodes"]}
    ms = members_at(d).get(node, [])
    dirs = []
    for m in ms:
        v = xyz[m["node_b"] if m["node_a"] == node else m["node_a"]] - xyz[node]
        dirs.append(v / np.linalg.norm(v))
    through = set()
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            if i in through or j in through:
                continue
            if ms[i]["section"] == ms[j]["section"] and float(dirs[i] @ dirs[j]) <= -0.995:
                through |= {i, j}
    return ms, through


def frame_joints(d):
    """The frame nodes that are joints: two or more pieces meet (a tube through counted once), or node.joint."""
    out = []
    for n in d["nodes"]:
        ms, through = node_pieces(d, n["id"])
        if len(ms) >= 2 and (len(ms) - len(through) // 2 >= 2 or "joint" in n):
            out.append(n)
    return out


def expected_counts(d):
    """{kind: n} counted from the design: (two ends, via) of connects_to, and the frame nodes."""
    pairs = {(c["via"], tuple(sorted((p["id"], c["to"])))) for p in d["parts"] for c in p.get("connects_to", [])}
    counts = Counter(via for via, _ in pairs)
    frame = frame_joints(d)
    for n in frame:
        counts[n.get("joint", "weld")] += 1
    rule = sum(1 for n in frame if "joint" not in n)
    return dict(counts), rule


def hand_design():
    """The design with node.joint written on three frame joints: weld, bolt, pin."""
    d = design()
    frame = frame_joints(d)
    for kind, n in zip(("weld", "bolt", "pin"), frame[:3]):
        n["joint"] = kind
        HAND_NODES[kind] = n["id"]
    return d


def dist_to_box(part, p):
    b = part["box"]
    R = rpy_matrix(b.get("rpy_deg", [0.0, 0.0, 0.0]))
    local = (np.asarray(p) - np.asarray(b["center_mm"])) @ R
    return float(np.linalg.norm(np.maximum(np.abs(local) - np.asarray(b["size_mm"]) / 2.0, 0.0)))


def dist_to_segment(a, b, p):
    a, b, p = map(np.asarray, (a, b, p))
    ab = b - a
    t = float(np.clip((p - a) @ ab / (ab @ ab), 0.0, 1.0)) if ab @ ab > 0 else 0.0
    return float(np.linalg.norm(p - (a + t * ab)))


def dist_to_target(d, to, p):
    parts = {x["id"]: x for x in d["parts"]}
    nodes = {n["id"]: n["xyz_mm"] for n in d["nodes"]}
    members = {m["id"]: m for m in d["members"]}
    supports = {s["id"]: s for s in d["supports"]}
    if to in parts:
        return dist_to_box(parts[to], p)
    if to in members:
        return dist_to_segment(nodes[members[to]["node_a"]], nodes[members[to]["node_b"]], p)
    if to in nodes:
        return float(np.linalg.norm(np.asarray(p) - np.asarray(nodes[to])))
    if to in supports:
        return float(np.linalg.norm(np.asarray(p) - np.asarray(supports[to]["position_mm"])))
    sup, _, hp = to.partition(".")
    return float(np.linalg.norm(np.asarray(p) - np.asarray(supports[sup]["suspension"]["hardpoints_mm"][hp])))


def differing(a, b):
    """The fraction of pixels of two PNGs that differ by more than 8 in some channel."""
    import io

    from PIL import Image

    x = np.asarray(Image.open(io.BytesIO(a)).convert("RGB"), dtype=int)
    y = np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=int)
    return float((np.abs(x - y).max(axis=2) > 8).mean())


def asm_events(events):
    """The assembly sample with joints on two parts: the battery bolted to node L1, the seat resting on it."""
    created = next(e for e in events if e["kind"] == "design.created")
    for p in created["data"]["parts"]:
        if p["id"] == "battery":
            p["connects_to"] = [{"to": "L1", "via": "bolt", "carries": "load"}]
        if p["id"] == "seat":
            p["connects_to"] = [{"to": "battery", "via": "rest", "carries": "load"}]
    return events


SUMMARY = "() => { const j = window.hiraku.bench.joints; return { ...j.summary(), group: j.group.visible } }"
LEGEND = """() => {
  const el = document.querySelector('#viewport > .joint-legend');
  if (!el || el.hidden) return null;
  return { counts: Object.fromEntries([...el.querySelectorAll('[data-count]')].map((x) => [x.dataset.count, +x.textContent])),
           text: el.textContent };
}"""
BUTTON = "() => document.querySelector('#toggles button[data-toggle=joints]')?.className ?? null"
LEG_MM = 5.0  # web/js/joints.js WELD_BEAD.leg_mm
# every weld mark's drawn bead: the bar's vertices through each of its pieces' instance matrices
BEAD_VERTICES = """() => {
  const j = window.hiraku.bench.joints; const mesh = j.meshes.bead; const pos = mesh.geometry.getAttribute('position');
  const M = mesh.matrix.clone(); const V = mesh.position.clone();
  return j.slots.filter((s) => s.shape === 'bead').map((s) => {
    const pts = [];
    for (const sub of s.subs) { mesh.getMatrixAt(sub.i, M); for (let k = 0; k < pos.count; k += 1) pts.push(V.fromBufferAttribute(pos, k).applyMatrix4(M).toArray()); }
    return { to: s.joint.to, source: s.joint.source, owner: s.joint.owner, at: s.mark.at, axis: s.mark.axis, how: s.mark.bead.how, seams: s.mark.bead.seams, pts };
  });
}"""
# the bead's and the bushing's meshes: geometry type, the bar's size, an instance's colour
MESHES = """() => Object.fromEntries(Object.entries(window.hiraku.bench.joints.meshes).map(([k, m]) => {
  m.geometry.computeBoundingBox(); const s = m.geometry.boundingBox.getSize(m.position.clone());
  return [k, { type: m.geometry.type, size: s.toArray(), color: [m.instanceColor.array[0], m.instanceColor.array[1], m.instanceColor.array[2]] }];
}))"""


def member_yz(a, b):
    """The y and z of a member's section as workbench3d.js memberBasis lays them (b along y, h along z)."""
    x = (b - a) / np.linalg.norm(b - a)
    up = np.array([1.0, 0, 0]) if abs(x[2]) > 0.95 else np.array([0, 0, 1.0])
    z = up - x * (up @ x)
    z /= np.linalg.norm(z)
    return np.cross(z, x), z


def on_member_face(xyz, d, m, p, tol=0.5):
    """p on the surface of member m's box (a face of its section's sides, within its length)."""
    a, b = xyz[m["node_a"]], xyz[m["node_b"]]
    x = (b - a) / np.linalg.norm(b - a)
    y, z = member_yz(a, b)
    dims = d["sections"][m["section"]]["dims_mm"]
    r = np.asarray(p) - (a + b) / 2
    lx, ly, lz = abs(r @ x), abs(r @ y), abs(r @ z)
    inside = lx <= np.linalg.norm(b - a) / 2 + tol and ly <= dims["b"] / 2 + tol and lz <= dims["h"] / 2 + tol
    return inside and (abs(ly - dims["b"] / 2) <= tol or abs(lz - dims["h"] / 2) <= tol)


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class JointsBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        for run_id, events in ((JOINTS_RUN, joints_events()), (HAND_RUN, joints_events(hand_design())), (ASM_RUN, asm_events(assembly_events()))):
            (runs / run_id).mkdir(parents=True)
            (runs / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, assets=False)
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

    def open(self, query, size=(1280, 720)):
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        page.errors = errors
        return page

    def bench(self, look="zu", on=True, run=JOINTS_RUN, size=(1280, 720)):
        return self.open(f"?run={run}&tab=bench&pace=0&t=1e6&look={look}{'&on=joints' if on else ''}", size)

    def test_counts_match_the_design(self):
        want, rule = expected_counts(design())
        self.assertGreater(want["bolt"], 50)
        for look in ("zu", "real"):
            page = self.bench(look)
            got = page.evaluate(SUMMARY)
            legend = page.evaluate(LEGEND)
            self.assertEqual(page.errors, [], look)
            page.close()
            self.assertEqual(got["counts"], want, look)
            self.assertEqual(got["frameRule"], rule, look)
            self.assertEqual(got["unplaced"], 0, look)
            # the legend: the same numbers, and the rule's note while frame nodes are drawn by it
            self.assertEqual(legend["counts"], want, look)
            self.assertIn("決まりで溶接として描いている", legend["text"])
            # one mark per joint of connects_to; a bead per member end at a welded frame node
            marks = got["marks"]
            per_part = Counter(m["kind"] for m in marks if m["source"] == "part")
            pairs = {(c["via"], tuple(sorted((p["id"], c["to"])))) for p in design()["parts"] for c in p.get("connects_to", [])}
            self.assertEqual(dict(per_part), dict(Counter(v for v, _ in pairs)), look)
            # a bead round each member ending at a welded frame node (a tube going through has none)
            beads = 0
            for n in frame_joints(design()):
                ms, through = node_pieces(design(), n["id"])
                beads += len(ms) - len(through)
            self.assertEqual(sum(1 for m in marks if m["source"] == "node"), beads, look)
            # one instance per mark, but a weld's bead: one per piece of its seam
            self.assertEqual(sum(n for k, n in got["instances"].items() if k != "bead"), sum(1 for m in marks if m["shape"] != "bead"), look)
            self.assertEqual(got["instances"]["bead"], sum(len(m["bead"]["seams"]) for m in marks if m["shape"] == "bead"), look)
            for m in marks:
                self.assertEqual(m["shape"], SHAPE[m["kind"]], m)

    def test_marks_are_by_their_boxes(self):
        # a mark of connects_to: halfway between the owner's box and its other end (each at most half
        # the gap away, 1 mm of slack); a frame node's mark: within its widest member's side of the node
        d = design()
        parts = {p["id"]: p for p in d["parts"]}
        nodes = {n["id"]: n["xyz_mm"] for n in d["nodes"]}
        at = members_at(d)
        side = lambda m: max(d["sections"][m["section"]]["dims_mm"].values())
        page = self.bench("zu")
        marks = page.evaluate(SUMMARY)["marks"]
        page.close()
        checked = 0
        for m in marks:
            if m["source"] == "part":
                da = dist_to_box(parts[m["owner"]], m["at"])
                db = dist_to_target(d, m["to"], m["at"])
                self.assertLessEqual(da, m["gap"] / 2 + TOL_MM, m)
                self.assertLessEqual(db, m["gap"] / 2 + TOL_MM, m)
                self.assertGreaterEqual(da + db + TOL_MM, m["gap"] - TOL_MM, m)
            else:
                widest = max(side(x) for x in at[m["to"]])
                self.assertLessEqual(float(np.linalg.norm(np.asarray(m["at"]) - np.asarray(nodes[m["to"]]))), widest, m)
            checked += 1
        self.assertEqual(checked, len(marks))
        # the parts with rpy_deg are among them (the boxes are turned)
        self.assertTrue(any("rpy_deg" in parts[m["owner"]]["box"] for m in marks if m["source"] == "part"))

    def test_weld_beads_follow_the_seams(self):
        # a frame node's bead: round the ending member's section outline (a rect tube: its 4 sides),
        # on the face's plane (the mark's at, across its axis), every vertex of the drawn bead within
        # the bead's leg of that outline; a bracket's bead: on the faces of the members it sits on
        d = design()
        parts = {p["id"]: p for p in d["parts"]}
        xyz = {n["id"]: np.asarray(n["xyz_mm"], dtype=float) for n in d["nodes"]}
        at = members_at(d)
        page = self.bench("zu")
        got = page.evaluate(SUMMARY)
        drawn = page.evaluate(BEAD_VERTICES)
        legend = page.evaluate(LEGEND)
        page.close()
        self.assertEqual(len(drawn), sum(1 for m in got["marks"] if m["shape"] == "bead"))
        hows = Counter(b["how"] for b in drawn)
        self.assertEqual(dict(hows), {k: v for k, v in got["beadSeams"].items() if v})
        rect = 0
        for b in drawn:
            pts = np.asarray(b["pts"])
            if b["how"] == "section":
                ax = np.asarray(b["axis"])
                m = next(x for x in at[b["to"]] if self.unit(xyz[x["node_b"] if x["node_a"] == b["to"] else x["node_a"]] - xyz[b["to"]]) @ ax > 0.999)
                sec = d["sections"][m["section"]]
                self.assertTrue(len(b["seams"]) == (4 if sec["shape"] == "rect_tube" else 16), b["to"])
                if sec["shape"] != "rect_tube":
                    continue
                rect += 1
                y, z = member_yz(xyz[m["node_a"]], xyz[m["node_b"]])
                w, h = sec["dims_mm"]["b"], sec["dims_mm"]["h"]
                rel = pts - np.asarray(b["at"])
                plane = rel @ ax
                cheb = np.maximum(np.abs(rel @ y) - w / 2, np.abs(rel @ z) - h / 2)
                # on the face's plane, up the member by at most the leg; on the outline, out by at most the leg
                self.assertGreaterEqual(plane.min(), -0.01, b["to"])
                self.assertLessEqual(plane.max(), LEG_MM + 0.01, b["to"])
                self.assertGreaterEqual(cheb.min(), -0.01, b["to"])
                self.assertLessEqual(cheb.max(), LEG_MM + 0.01, b["to"])
                # the seam lines themselves: on the plane, on the outline
                for s in b["seams"]:
                    for p in (s["a"], s["b"]):
                        r = np.asarray(p) - np.asarray(b["at"])
                        self.assertAlmostEqual(float(r @ ax), 0.0, places=3)
                        self.assertAlmostEqual(max(abs(float(r @ y)) - w / 2, abs(float(r @ z)) - h / 2), 0.0, places=3)
            elif b["how"] == "contact":
                # each seam's ends on the owner's box and on a face of a member at the node
                for s in b["seams"]:
                    for p in (s["a"], s["b"]):
                        self.assertLessEqual(dist_to_box(parts[b["owner"]], p), 0.5, b["owner"])
                        self.assertTrue(any(on_member_face(xyz, d, x, p) for x in at[b["to"]]), (b["owner"], p))
        self.assertGreater(rect, 20)
        # 16 of the 18 welded brackets sit on a rail's face; bracket_column (to LDT and RDT) has its box's
        # bottom at dash_cross's centre line (z 845.9), not on a face, and the rollbar's box is its top bar
        self.assertEqual(hows["contact"], 16)
        self.assertEqual(got["beadSeams"]["as_is"], 4)
        self.assertIn("脚 5 mm", legend["text"])
        if got["beadSeams"]["as_is"]:
            self.assertIn(f"溶接の印のうち {got['beadSeams']['as_is']} は", legend["text"])

    def test_weld_bead_apart_from_bushing(self):
        # the bead: a thin bar of the leg's triangle, in its own colour; the bushing: a ring, black
        for look in ("zu", "real"):
            page = self.bench(look)
            got = page.evaluate(MESHES)
            page.close()
            self.assertIn("bead", got)
            self.assertIn("ring", got)
            self.assertNotEqual(got["bead"]["type"], got["ring"]["type"])
            size = got["bead"]["size"]
            self.assertAlmostEqual(size[1], LEG_MM, places=3)
            self.assertAlmostEqual(size[2], LEG_MM, places=3)
            bead, ring = np.asarray(got["bead"]["color"]), np.asarray(got["ring"]["color"])
            self.assertGreater(float(np.abs(bead - ring).max()), 0.05, look)
            self.assertGreater(float(bead[0] - bead[2]), 0.0, look)  # brown: more red than blue

    @staticmethod
    def unit(v):
        return v / np.linalg.norm(v)

    def test_node_joint_kinds(self):
        d = hand_design()
        want, rule = expected_counts(d)
        page = self.bench("zu", run=HAND_RUN)
        got = page.evaluate(SUMMARY)
        legend = page.evaluate(LEGEND)
        self.assertEqual(page.errors, [])
        page.close()
        self.assertEqual(got["counts"], want)
        self.assertEqual(legend["counts"], want)
        self.assertEqual(got["frameRule"], rule)
        self.assertEqual(rule, expected_counts(design())[1] - 3)
        for kind, shape in (("weld", "bead"), ("bolt", "hex"), ("pin", "pin")):
            node = HAND_NODES[kind]
            mine = [m for m in got["marks"] if m["source"] == "node" and m["to"] == node]
            self.assertTrue(mine, kind)
            self.assertEqual({m["shape"] for m in mine}, {shape}, kind)
            self.assertEqual({m["rule"] for m in mine}, {False}, kind)  # written in the data, not the rule
            ms, through = node_pieces(d, node)
            self.assertEqual(len(mine), len(ms) - len(through) if kind == "weld" else 1, kind)

    def test_toggle_brings_them_in_and_out(self):
        page = self.bench("zu", on=False)
        self.assertEqual(page.evaluate(BUTTON), "")  # off at first
        got = page.evaluate(SUMMARY)
        self.assertFalse(got["on"])
        self.assertFalse(got["group"])
        self.assertIsNone(page.evaluate(LEGEND))
        opacity = "() => Math.max(...Object.values(window.hiraku.bench.partObjs).flatMap((o) => { const a = []; o.group.traverse((x) => x.isMesh && x.material.userData.baseColor !== undefined && a.push(x.material.opacity)); return a; }))"
        before = page.evaluate(opacity)
        page.click("#toggles button[data-toggle=joints]")
        page.evaluate("window.hiraku.seek(1e6)")
        self.assertEqual(page.evaluate(BUTTON), "on")
        got = page.evaluate(SUMMARY)
        self.assertTrue(got["on"] and got["group"])
        self.assertIsNotNone(page.evaluate(LEGEND))
        self.assertLessEqual(page.evaluate(opacity), 0.14 + 1e-6)  # the parts see-through
        self.assertTrue(all(m["shown"] for m in got["marks"]))
        # the parts off: their marks go, the frame's stay
        page.click("#toggles button[data-toggle=parts]")
        page.evaluate("window.hiraku.seek(1e6)")
        marks = page.evaluate(SUMMARY)["marks"]
        drawn = set(page.evaluate("Object.keys(window.hiraku.bench.partObjs)"))
        # (a wheel listed as a part is drawn from its support, with the wheels: its marks stay)
        self.assertTrue(all(m["shown"] == (m["owner"] not in drawn) for m in marks))
        self.assertTrue(any(not m["shown"] for m in marks))
        page.click("#toggles button[data-toggle=parts]")
        page.click("#toggles button[data-toggle=joints]")
        page.evaluate("window.hiraku.seek(1e6)")
        got = page.evaluate(SUMMARY)
        self.assertFalse(got["on"] or got["group"])
        self.assertIsNone(page.evaluate(LEGEND))
        self.assertAlmostEqual(page.evaluate(opacity), before)
        self.assertEqual(page.errors, [])
        page.close()

    def test_real_look_parts_come_back(self):
        page = self.bench("real", on=False)
        state = "() => Object.values(window.hiraku.bench.partObjs).flatMap((o) => { const a = []; o.group.traverse((x) => x.isMesh && x.material.userData.baseColor !== undefined && a.push([x.material.transparent, x.material.opacity, x.material.depthWrite])); return a; })"
        before = page.evaluate(state)
        page.click("#toggles button[data-toggle=joints]")
        page.evaluate("window.hiraku.seek(1e6)")
        self.assertTrue(all(t and o < 0.3 and not dw for t, o, dw in page.evaluate(state)))
        page.click("#toggles button[data-toggle=joints]")
        page.evaluate("window.hiraku.seek(1e6)")
        self.assertEqual(page.evaluate(state), before)
        self.assertEqual(page.errors, [])
        page.close()

    def test_same_picture_at_t(self):
        for look in ("zu", "real"):
            shots = []
            for _ in range(2):
                page = self.bench(look)
                shots.append(page.locator("#viewport").screenshot())
                page.close()
            if look == "zu":
                self.assertEqual(shots[0], shots[1], look)
            else:
                # the real look's post (ambient occlusion, antialiasing) is not bit for bit the same
                # from one page to the next: nearly every pixel the same
                self.assertLess(differing(shots[0], shots[1]), 0.01, look)

    def test_assembly(self):
        page = self.open(f"?run={ASM_RUN}&tab=A7&pace=0&t=1e6")
        page.wait_for_function("window.hiraku.asm && window.hiraku.asm.rec")
        btn = page.locator(".asm-3d > .asm-joints-btn")
        self.assertEqual(btn.get_attribute("class"), "asm-joints-btn")
        btn.click()
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        get = "() => window.hiraku.asm.view.joints.summary()"
        got = page.evaluate(get)
        self.assertTrue(got["on"])
        self.assertTrue(page.locator(".asm-3d > .joint-legend").is_visible())
        # the sample's frame nodes by the rule, the battery's bolt and the seat resting on it
        asm_design = next(e for e in asm_events(assembly_events()) if e["kind"] == "design.created")["data"]
        self.assertEqual(got["counts"], expected_counts(asm_design)[0])
        self.assertEqual({k: got["counts"][k] for k in ("bolt", "rest")}, {"bolt": 1, "rest": 1})
        legend = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('.asm-3d > .joint-legend [data-count]')].map((x) => [x.dataset.count, +x.textContent]))")
        self.assertEqual(legend, got["counts"])
        # opened on the vehicle built: every mark shown
        self.assertTrue(all(m["shown"] for m in got["marks"]))
        frame = "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"

        def at(u):
            page.evaluate(f"window.hiraku.asm.u = {u}")
            page.evaluate(frame)
            return {m["owner"]: m for m in page.evaluate(get)["marks"] if m["owner"] != "frame"}

        # step 1 (the battery) halfway in: its bolt moves with it; the seat (step 2) not there yet
        mid = at(0.5)
        self.assertFalse(mid["seat"]["shown"])
        self.assertTrue(mid["battery"]["shown"])
        pos = page.evaluate("window.hiraku.asm.view.partObjs.battery.group.position.toArray()")
        self.assertGreater(np.linalg.norm(pos), 1.0)
        np.testing.assert_allclose(np.asarray(mid["battery"]["now"]) - np.asarray(mid["battery"]["at"]), pos, atol=1e-6)
        # step 1 in place: the bolt where it is fastened
        one = at(1)
        np.testing.assert_allclose(one["battery"]["now"], one["battery"]["at"], atol=1e-6)
        self.assertFalse(one["seat"]["shown"])
        self.assertTrue(at(3)["seat"]["shown"])
        self.assertEqual(page.errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
