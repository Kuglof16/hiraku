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


def node_beads(d, node):
    """The beads at a welded frame node: one per member ending there (not going through), less the
    carrier (web/js/tubes.js frameEnds: where no tube goes through, the one the others butt on);
    every member when all go through. All members of the frozen designs are tubes with dims_mm."""
    ms, through = node_pieces(d, node)
    ending = len(ms) - len(through)
    if not ending:
        return len(ms)
    return ending - (0 if through else 1)


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


EXTRA_RUNS = {"place": "20261104-190300-place", "ladder": "20261104-190400-ladder", "other": "20261104-190500-other"}
PLACED = {}  # filled by placed_design(): (owner, to) -> {"at": [...], "axis": [...] or None}


def placed_design():
    """The design with at_mm (and on the first, axis too) written by hand on two joints of
    connects_to whose other end is not a part (so no other entry names the same pair)."""
    d = design()
    parts = {p["id"] for p in d["parts"]}
    chosen = []
    for p in d["parts"]:
        if not p.get("box") or p.get("kind") == "ball_joint":
            continue
        for c in p.get("connects_to", []):
            if c["to"] not in parts and c["via"] != "ball_joint" and (p["id"], c["to"]) not in {x[:2] for x in chosen}:
                chosen.append((p["id"], c["to"], c["via"]))
                break
        if len(chosen) == 2:
            break
    PLACED.clear()
    for k, (owner, to, via) in enumerate(chosen):
        cen = np.asarray(next(p for p in d["parts"] if p["id"] == owner)["box"]["center_mm"], dtype=float)
        at = [round(float(x), 1) for x in cen + np.array([11.0, -7.0, 23.0])]
        axis = [0.0, 0.6, 0.8] if k == 0 else None
        PLACED[(owner, to)] = {"at": at, "axis": axis, "via": via}
        for p in d["parts"]:
            if p["id"] == owner:
                for c in p["connects_to"]:
                    if c["to"] == to and c["via"] == via:
                        c["at_mm"] = at
                        if axis:
                            c["axis"] = axis
    return d


def lab(hexcol):
    """CIELAB (D65) of a #rrggbb colour."""
    rgb = [int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    xyz = ((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047, 0.2126 * r + 0.7152 * g + 0.0722 * b, (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883)
    f = [c ** (1 / 3) if c > 0.008856 else 7.787 * c + 16 / 116 for c in xyz]
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


COLOUR_APART = 25.0  # CIE76 distance a joint mark's colour keeps from every module colour on the same bench
BEAD_REAL_APART = 10.0  # CIE76: the real look's drawn bead from its colour (light and tone mapping move it; 7 measured, the old ochre (131,110,81) 16)
# the bench's camera 0.6 m from the frame node at x -2020 (body frame, mm), from above and the left
LOOK_AT_NODE = """() => { const b = window.hiraku.bench; const t = b.camera.position.clone().set(-2020, 330, 400);
  b.body.updateWorldMatrix(true, false); b.body.localToWorld(t);
  const w = b.camera.position.clone().set(0.35, -0.6, 0.75).applyQuaternion(b.body.getWorldQuaternion(b.camera.quaternion.clone())).normalize();
  b.lookFrom({ target: t, position: t.clone().addScaledVector(w, 0.6), near: 0.01, far: 50 }); }"""


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


def pixel_steps(a, b):
    """How many pixels of two PNGs differ at all, and the largest step in any channel (RGBA)."""
    import io

    from PIL import Image

    x = np.asarray(Image.open(io.BytesIO(a)).convert("RGBA"), dtype=int)
    y = np.asarray(Image.open(io.BytesIO(b)).convert("RGBA"), dtype=int)
    d = np.abs(x - y).max(axis=2)
    return int((d > 0).sum()), int(d.max())


def where_differ(a, b, n=6):
    """The first n pixels (x, y) where two PNGs differ, with their RGBA in each."""
    import io

    from PIL import Image

    x = np.asarray(Image.open(io.BytesIO(a)).convert("RGBA"), dtype=int)
    y = np.asarray(Image.open(io.BytesIO(b)).convert("RGBA"), dtype=int)
    ys, xs = np.nonzero(np.abs(x - y).max(axis=2))
    return [((int(i), int(j)), x[j, i].tolist(), y[j, i].tolist()) for i, j in zip(xs[:n], ys[:n])]


def asm_events(events):
    """The assembly sample with joints on two parts: the battery bolted to node L1, the seat resting on it."""
    created = next(e for e in events if e["kind"] == "design.created")
    for p in created["data"]["parts"]:
        if p["id"] == "battery":
            p["connects_to"] = [{"to": "L1", "via": "bolt", "carries": "load"}]
        if p["id"] == "seat":
            p["connects_to"] = [{"to": "battery", "via": "rest", "carries": "load"}]
    return events


NAMED_RUN = "20261104-190300-named"  # asm_events with member.name written by hand on rail_L (named_events)
RAIL_NAME = "左のレール (前の車軸から後ろの車軸まで)"


def named_events(events):
    """The events with member.name (the contract's name for a person, 6f5845e) on rail_L0 only; the
    assembly sample's pin fastening (to "member rail_L", not a member of its design) turned to rail_L0."""
    events = json.loads(json.dumps(events).replace('"member rail_L"', '"member rail_L0"'))
    created = next(e for e in events if e["kind"] == "design.created")
    for m in created["data"]["members"]:
        if m["id"] == "rail_L0":
            m["name"] = RAIL_NAME
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
LIFT_MM = 0.2  # web/js/joints.js WELD_BEAD.lift_mm (the bead off the faces' planes)
CUT_GAP_MM = 0.3  # web/js/tubes.js TUBE.cut_gap_mm (a cut tube end off the receiver's face)
# each tube member's ends on the bench (web/js/tubes.js frameEnds): {id: {a: {kind, trim, onto}, b: ...}}
TUBE_ENDS = "() => Object.fromEntries(Object.entries(window.hiraku.bench.members).filter(([, i]) => i.ends).map(([k, i]) => [k, i.ends]))"
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
        for run_id, events in ((JOINTS_RUN, joints_events()), (HAND_RUN, joints_events(hand_design())), (ASM_RUN, asm_events(assembly_events())),
                               (NAMED_RUN, named_events(asm_events(assembly_events())))):
            (runs / run_id).mkdir(parents=True)
            (runs / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        from tests.web.modules_fixture import RUN_ID as MODULES_RUN, module_events
        cls.extra = {"place": placed_design(), "ladder": dict(design(), archetype="ladder_4w"), "other": dict(design(), archetype="care_bed")}
        for name, d in cls.extra.items():
            (runs / EXTRA_RUNS[name]).mkdir(parents=True)
            evs = joints_events(d)
            (runs / EXTRA_RUNS[name] / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs), encoding="utf-8")
        cls.modules_run = MODULES_RUN
        (runs / MODULES_RUN).mkdir(parents=True)
        (runs / MODULES_RUN / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in module_events()), encoding="utf-8")
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
            # a bead round each member ending at a welded frame node (a tube going through has none,
            # nor the carrier the others butt on where no tube goes through: web/js/tubes.js)
            beads = sum(node_beads(design(), n["id"]) for n in frame_joints(design()))
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
        ends = page.evaluate(TUBE_ENDS)
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
                # a weld's bead where its tube is cut (the end's trim from the node, along the member);
                # other marks within the widest member's side of the node
                off = float(np.linalg.norm(np.asarray(m["at"]) - np.asarray(nodes[m["to"]])))
                cut = None
                for x in at[m["to"]]:
                    far = nodes[x["node_b"] if x["node_a"] == m["to"] else x["node_a"]]
                    d = np.asarray(far, dtype=float) - np.asarray(nodes[m["to"]])
                    if m["shape"] == "bead" and float(d @ np.asarray(m["axis"])) / np.linalg.norm(d) > 0.999:
                        cut = ends[x["id"]]["a" if x["node_a"] == m["to"] else "b"]
                if cut is not None and cut["kind"] in ("cut", "butt"):
                    # a cut end stands CUT_GAP_MM off the receiver's face; its bead sits on the face
                    self.assertAlmostEqual(off, cut["trim"] - (CUT_GAP_MM if cut["kind"] == "cut" else 0.0), places=3, msg=m)
                else:
                    widest = max(side(x) for x in at[m["to"]])
                    self.assertLessEqual(off, widest, m)
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
                # on the face's plane (lifted off it by LIFT_MM), up the member by at most the leg; on the
                # outline, out by at most the leg
                self.assertGreaterEqual(plane.min(), -0.01, b["to"])
                self.assertLessEqual(plane.max(), LEG_MM + LIFT_MM + 0.01, b["to"])
                self.assertGreaterEqual(cheb.min(), -0.01, b["to"])
                self.assertLessEqual(cheb.max(), LEG_MM + LIFT_MM + 0.01, b["to"])
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

    def test_real_bead_in_its_own_colour(self):
        # the real look, joints on (the parts see-through), close on the frame node at x -2020: the
        # bead's pixels (where hiding the bead changes the picture) come out in the weld's colour, and
        # black when the bead's material is black: nothing laid over them (the see-through parts were
        # drawn after the opaque bead and tinted it a light ochre, whatever the bead's own colour)
        import io

        from PIL import Image

        page = self.bench("real")
        page.evaluate(LOOK_AT_NODE)

        def shot(js):
            page.evaluate(js)
            page.evaluate("() => window.hiraku.bench.render()")
            page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(r))))")
            return np.asarray(Image.open(io.BytesIO(page.locator("#viewport").screenshot())).convert("RGB"), dtype=int)

        shown = shot("() => 0")
        hidden = shot("() => { window.hiraku.bench.joints.meshes.bead.visible = false; }")
        black = shot("() => { const m = window.hiraku.bench.joints.meshes.bead; m.visible = true; m.material.color.set('#000000'); }")
        weld = page.evaluate("async () => (await import('/js/joints.js')).JOINT_KINDS.weld.color")
        self.assertEqual(page.errors, [])
        page.close()
        bead = np.abs(shown - hidden).max(axis=2) > 24
        self.assertGreater(int(bead.sum()), 200)
        # black: near black (the metal's reflection of the sky and the antialiasing at the edges; laid
        # over by the see-through parts it was about 125)
        self.assertLess(float(np.median(black[bead].max(axis=1))), 45)
        # the weld's colour: CIE76 within BEAD_REAL_APART of joints.js JOINT_KINDS.weld
        got = np.median(shown[bead], axis=0)
        self.assertLess(float(np.linalg.norm(lab("#%02x%02x%02x" % tuple(int(c) for c in got)) - lab(weld))), BEAD_REAL_APART, (got, weld))

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
            self.assertEqual(len(mine), node_beads(d, node) if kind == "weld" else 1, kind)

    def test_place_and_axis_from_the_data(self):
        # connects_to with at_mm: the mark at that point; with axis: along it (contract 070d49c); the
        # rest placed from the boxes, and the legend counts them (推し量りで置いた N 所)
        self.assertEqual(len(PLACED), 2)
        page = self.bench("zu", run=EXTRA_RUNS["place"])
        got = page.evaluate(SUMMARY)
        legend = page.evaluate(LEGEND)
        self.assertEqual(page.errors, [])
        page.close()
        for (owner, to), want in PLACED.items():
            mine = [m for m in got["marks"] if m["source"] == "part" and m["owner"] == owner and m["to"] == to]
            self.assertEqual(len(mine), 1, owner)
            m = mine[0]
            np.testing.assert_allclose(m["at"], want["at"], atol=1e-6)
            self.assertEqual(m["placed"], "data")
            if want["axis"]:
                np.testing.assert_allclose(m["axis"], want["axis"], atol=1e-6)
                self.assertEqual(m["axisFrom"], "data")
            else:
                self.assertEqual(m["axisFrom"], "guess")
        guessed = sum(1 for m in got["marks"] if m["source"] == "part" and m["placed"] == "guess")
        self.assertEqual(got["fromData"], 2)
        self.assertEqual(got["guessed"], guessed)
        self.assertGreater(guessed, 0)
        self.assertIn(f"場所はおおよそ {guessed} 所", legend["text"])
        # the counts do not change with the place
        self.assertEqual(got["counts"], expected_counts(design())[0])

    def test_frame_rule_by_archetype(self):
        want, rule = expected_counts(design())
        self.assertGreater(rule, 0)
        # ladder_4w: the rule, said as the contract's (ladder_4w の枠の節は溶接)
        page = self.bench("zu", run=EXTRA_RUNS["ladder"])
        got, legend = page.evaluate(SUMMARY), page.evaluate(LEGEND)
        self.assertEqual(page.errors, [])
        page.close()
        self.assertEqual((got["frameRule"], got["ruleBasis"], got["unstated"]), (rule, "archetype", 0))
        self.assertEqual(got["counts"], want)
        self.assertIn("ladder_4w の型の枠の節は溶接", legend["text"])
        # another archetype: no rule; the nodes without node.joint have no mark and are counted in a note
        page = self.bench("zu", run=EXTRA_RUNS["other"])
        got, legend = page.evaluate(SUMMARY), page.evaluate(LEGEND)
        self.assertEqual(page.errors, [])
        page.close()
        self.assertEqual((got["frameRule"], got["ruleBasis"], got["unstated"]), (0, None, rule))
        self.assertEqual(sum(1 for m in got["marks"] if m["source"] == "node"), 0)
        less = dict(want)
        less["weld"] -= rule
        self.assertEqual(got["counts"], {k: v for k, v in less.items() if v})
        self.assertIn(f"枠の節 {rule} 所はつなぎ方がデータに無いので印なし", legend["text"])
        self.assertNotIn("決まりで溶接", legend["text"])
        # no archetype (a design made before it was recorded): the rule on every frame node, said so
        page = self.bench("zu")
        got, legend = page.evaluate(SUMMARY), page.evaluate(LEGEND)
        page.close()
        self.assertEqual((got["frameRule"], got["ruleBasis"]), (rule, "old"))
        self.assertIn("前の設計なので", legend["text"])
        # THROUGH_COS in words
        self.assertIn("一直線に続く同じ断面の 2 本は 1 本の管として、つなぎ目に数えない", legend["text"])

    def test_colours_apart_with_modules(self):
        # joints and modules on one bench: each drawn joint kind's colour apart from each module colour
        page = self.open(f"?run={self.modules_run}&tab=bench&pace=0&t=1e6&on=joints,modules")
        got = page.evaluate("""async () => {
          const J = await import('/js/joints.js');
          const P = await import('/js/proposals.js');
          const s = window.hiraku.bench.joints.summary();
          // the legend's modules, and package (the layout's: the packager's colour, no parts of its own)
          const mods = ['package', ...[...document.querySelectorAll('[data-module]')].map((x) => x.dataset.module)];
          const every = ['package', 'frame', 'suspension', 'steering', 'occupant', 'powertrain', 'body'];
          return { kinds: Object.fromEntries(Object.keys(s.counts).map((k) => [k, J.kindOf(k).color])),
                   modules: Object.fromEntries([...new Set(mods)].map((m) => [m, P.moduleColor(m)])),
                   table: Object.fromEntries(Object.entries(J.JOINT_KINDS).map(([k, v]) => [k, v.color])),
                   allModules: { ...Object.fromEntries(every.map((m) => [m, P.moduleColor(m)])), outside: P.OUTSIDE_COLOR } };
        }""")
        self.assertEqual(page.errors, [])
        page.close()
        self.assertIn("pin", got["kinds"])
        self.assertIn("package", got["modules"])
        for k, kc in got["kinds"].items():
            for m, mc in got["modules"].items():
                self.assertGreaterEqual(float(np.linalg.norm(lab(kc) - lab(mc))), COLOUR_APART, (k, kc, m, mc))
        # and every kind of the table against every module colour and the red of a part outside its space
        for k, kc in got["table"].items():
            for m, mc in got["allModules"].items():
                self.assertGreaterEqual(float(np.linalg.norm(lab(kc) - lab(mc))), COLOUR_APART, (k, kc, m, mc))

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
                # zu: at the stage's own size (1920x1080, app.js STAGE_W/H), so the stage is not scaled.
                # At 1280x720 the stage is scaled by 2/3 and the viewport's ruled paper (app.css
                # .viewport, 1px lines every 32px) falls between device pixels: its lines are
                # antialiased, and the browser rounds them differently each time it rasterises a
                # region again. With 8 other screen tests running, 7 of 10 runs had 1469 pixels 1 step
                # off (a rectangle x 0-403, y 139-540 of the viewport; at 1143 of them the canvas is
                # fully clear, so it is the CSS paper, not the 3D). The canvas's own pixels
                # (toDataURL) and the camera were the same in every load, and one page shot again 2 s
                # later could change. At 1920x1080, 16 loads under the same load were one picture.
                page = self.bench(look, size=(1920, 1080) if look == "zu" else (1280, 720))
                shots.append(page.locator("#viewport").screenshot())
                page.close()
            if look == "zu":
                # bit for bit: no pixel differs. (Up to 7871fff a 1 in 10000 pixels was let pass; the
                # pixels seen then, (558,265) and 4 on the hollow tubes, were at 1280x720. If it comes
                # back, the message names the pixels.)
                self.assertEqual(pixel_steps(shots[0], shots[1]), (0, 0), (look, where_differ(shots[0], shots[1])))
            else:
                # the real look's post (ambient occlusion, antialiasing) is not bit for bit the same
                # from one page to the next: nearly every pixel the same
                self.assertLess(differing(shots[0], shots[1]), 0.01, look)

    def test_member_name(self):
        # member.name where the design has one (rail_L), the id where not: on the member's name tag
        # (名前: the short name before the bracket; pointed at: the whole name and the id) and in A7's
        # fastening text (what the part is joined to)
        page = self.open(f"?run={NAMED_RUN}&tab=bench&pace=0&t=1e6")
        tags = page.evaluate("() => window.hiraku.bench.names.children.filter((o) => o.element.dataset.member).map((o) => [o.element.dataset.member, o.element.textContent, o.element.title])")
        self.assertEqual(page.errors, [])
        page.close()
        by = {m: (text, title) for m, text, title in tags}
        self.assertIn("rail_L0", by, sorted(by))
        self.assertEqual(by["rail_L0"], ("左のレール", f"{RAIL_NAME} (rail_L0)"))
        others = [m for m in by if m != "rail_L0"]
        self.assertTrue(others)
        for m in others:
            self.assertEqual(by[m], (m, m))
        page = self.open(f"?run={NAMED_RUN}&tab=A7&pace=0&t=1e6")
        page.wait_for_function("window.hiraku.asm && window.hiraku.asm.rec")
        rows = page.evaluate("() => [...document.querySelectorAll('[data-order]')].map((x) => x.textContent)")
        self.assertEqual(page.errors, [])
        page.close()
        text = "".join(rows)
        self.assertIn("ピン: 左のレール", text)
        self.assertNotIn("部材 rail_L0", text)
        self.assertIn("節点 N1", text)  # a node: as before

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
