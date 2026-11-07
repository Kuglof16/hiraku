"""The real look's crash test dummy (web/js/shapes.js occupantDummyPieces, web/js/hybrid3.js).

- hybrid3.js keeps the Hybrid III 50th percentile male's table in inches (the values the
  coordinating team copied from the product sheet, written again here) and works millimetres out
  as 25.4 times them; the shape's ratios are the table's (worked out here again);
- the dummy keeps the design's lengths and joints' centres and takes only the shapes no
  calculation uses from the table: the chest's and the pelvis' breadth on the torso box's
  breadth, the leg's balls scaled by the knee pivot's height, the foot's breadth;
- in every design drawn (the four showcase runs, each version, and the tests' design fixtures
  with an occupant in segments) no piece reaches outside its box and the dummy meets no other
  part more than the design's own boxes do (a hand on the steering wheel holds it; the thighs on
  the seat are counted against the boxes' own overlap);
- at the knee room's record (H3 knee_room, the x from the seat back to the part ahead at knee
  height, less AIST J5) the drawn knee's front stands where the record puts the knee, to a few mm;
- the colours: the body yellow (roughness 0.45), the joints, the neck, the hands and the feet
  black #1c1c1c, the yellow at least 15 (CIE76 ΔE*ab) from the contour's yellow, the heat colour,
  the caution orange and the verdict's red;
- the workbench's くわしく says the line about the dummy in the real look only.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

import glob
import hashlib
import json
import math
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

from hiraku.solvers.k0_handcalc.rigid import rpy_matrix
from hiraku.solvers.k4_geometry import people
from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TOL_MM = 0.01
DUMMY_LINE = "絵のダミーの形は Hybrid III 50 パーセンタイル男性の比率。長さは計算に使う乗員の寸法"

# The table as the coordinating team copied it (in): Humanetics, Hybrid III 50th Percentile Male
# Technical Product Sheet (2012)
TABLE_IN = {
    "sitting_height": 34.8, "shoulder_pivot_height": 20.2, "h_point_height": 3.4, "h_point_from_seat_back": 5.4,
    "shoulder_pivot_from_back_line": 3.5, "thigh_clearance": 5.8, "elbow_wrist_pivot": 11.0,
    "head_back_from_back_line": 1.7, "shoulder_elbow": 13.3, "elbow_rest_height": 7.9, "buttock_knee": 23.3,
    "popliteal_height": 17.4, "knee_pivot_height": 19.4, "buttock_popliteal": 18.3, "chest_depth": 8.7,
    "foot_length": 10.2, "foot_breadth": 3.9, "shoulder_breadth": 16.9, "hip_breadth": 14.3,
    "chest_circumference": 38.8, "waist_circumference": 33.5,
}
TABLE_LB = {"head": 10.0, "neck": 3.4, "upper_torso": 37.9, "lower_torso": 50.8, "upper_arm": 4.4,
            "lower_arm_hand": 5.0, "upper_leg": 13.2, "lower_leg_foot": 12.0, "total": 171.3}

WRIST = 0.8  # web/js/shapes.js DUMMY.wrist
BODY_YELLOW = "#e8ae0e"
JOINT_BLACK = "#1c1c1c"
KEEP_APART = {"contour yellow": "#e8c547", "heat": "#ff5a1f", "caution orange": "#e9893a", "verdict red": "#c8402a"}

# The page's hybrid3.js
TABLE_OF = """async () => {
  const m = await import("/js/hybrid3.js");
  const mm = Object.fromEntries(Object.keys(m.H3_IN).map((k) => [k, m.h3mm(k)]));
  return { inch: m.H3_IN, lb: m.H3_LB, mm, shape: m.H3_SHAPE, note: m.DUMMY_NOTE_JA };
}"""

# Per occupant part: its meshes in the frame of the bench's parts group (ISO mm), and the
# workbench's くわしく.
DRAWN_OF = """async ([events]) => {
  await window.hiraku.load(events, 1);
  const b = window.hiraku.bench;
  await window.hiraku.seek(1);
  b.parts.updateWorldMatrix(true, true);
  const inv = b.parts.matrixWorld.clone().invert();
  const out = {};
  for (const [id, o] of Object.entries(b.partObjs)) {
    if (o.kind !== "occupant") continue;
    const meshes = [];
    o.group.traverse((x) => {
      if (!x.isMesh || x.isLineSegments2) return;
      const m = x.matrixWorld.clone().premultiply(inv).elements;
      const p = x.geometry.attributes.position;
      const verts = [];
      for (let i = 0; i < p.count; i += 1) {
        const [vx, vy, vz] = [p.getX(i), p.getY(i), p.getZ(i)];
        verts.push([m[0] * vx + m[4] * vy + m[8] * vz + m[12], m[1] * vx + m[5] * vy + m[9] * vz + m[13], m[2] * vx + m[6] * vy + m[10] * vz + m[14]]);
      }
      meshes.push({ type: x.geometry.type, verts, color: "#" + x.material.color.getHexString(), roughness: x.material.roughness ?? null,
                    piece: x.geometry.userData.occupant ?? null, radius: x.geometry.userData.radius_mm ?? null });
    });
    out[id] = { visible: o.group.visible, meshes };
  }
  const more = document.getElementById("bench-more");
  return { parts: out, more: { hidden: more.hidden, text: document.getElementById("bench-dummy-note").textContent } };
}"""


def events_with(design):
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    return [src[0], {**src[1], "data": design}]


def segmented(design):
    return any(p.get("kind") == "occupant" and "." in p.get("id", "") for p in design.get("parts", []))


def all_designs():
    """(name, design, {person: knee_room record} ) for the showcase runs' versions and the fixtures;
    the same design once."""
    out, seen = [], set()
    for run in sorted(glob.glob(str(ROOT / "showcase" / "*" / "events.jsonl"))):
        name = Path(run).parent.name
        designs, knee = {}, {}
        for line in Path(run).read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e["kind"] == "design.created":
                designs[e["design_version"]] = e["data"]
            d = e.get("data")
            if e["kind"] == "prediction.recorded" and isinstance(d, dict) and d.get("quantity") == "knee_room":
                knee[e["design_version"]] = d["value"]
        for v, d in designs.items():
            key = hashlib.sha1(json.dumps(d["parts"], sort_keys=True).encode()).hexdigest()
            if segmented(d) and key not in seen:
                seen.add(key)
                out.append((f"{name} v{v}", d, knee.get(v)))
    for f in sorted(glob.glob(str(HERE / "**" / "*.json"), recursive=True)):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(d, dict) and isinstance(d.get("parts"), list) and segmented(d):
            key = hashlib.sha1(json.dumps(d["parts"], sort_keys=True).encode()).hexdigest()
            if key not in seen:
                seen.add(key)
                out.append((str(Path(f).relative_to(HERE)), d, None))
    return out


def in_box(box, verts):
    """The vertices in the box's own frame, and the box's half sizes."""
    R = rpy_matrix(box.get("rpy_deg", [0.0, 0.0, 0.0]))
    return (np.asarray(verts) - np.asarray(box["center_mm"])) @ R, np.asarray(box["size_mm"]) / 2.0


def depth_in(box, verts):
    """How far the deepest vertex is inside the box (mm; <= 0: none inside)."""
    local, half = in_box(box, verts)
    return float((half - np.abs(local)).min(axis=1).max())


def depth_in_ring(box, verts):
    """How far the deepest vertex is inside a steering wheel's ring (shapes.js steering: a torus
    filling the box across y, z, its axis the box's x; mm, <= 0: none inside)."""
    local, half = in_box(box, verts)
    sx, sy, sz = 2 * half
    tube = max(6.0, min(sx / 2, min(sy, sz) * 0.05))
    ring = max(20.0, min(sy, sz) / 2 - tube)
    off_circle = np.hypot(np.hypot(local[:, 1], local[:, 2]) - ring, local[:, 0])
    return float((tube - off_circle).max())


def boxes_overlap(a, b):
    """The separating-axis depth of two boxes (mm; <= 0: apart)."""
    ca, Ra, ha = np.asarray(a["center_mm"], float), rpy_matrix(a.get("rpy_deg", [0, 0, 0])), np.asarray(a["size_mm"], float) / 2
    cb, Rb, hb = np.asarray(b["center_mm"], float), rpy_matrix(b.get("rpy_deg", [0, 0, 0])), np.asarray(b["size_mm"], float) / 2
    axes = [Ra[:, i] for i in range(3)] + [Rb[:, i] for i in range(3)]
    axes += [c / np.linalg.norm(c) for i in range(3) for j in range(3) for c in [np.cross(Ra[:, i], Rb[:, j])] if np.linalg.norm(c) > 1e-6]
    return min(sum(ha[k] * abs(Ra[:, k] @ ax) for k in range(3)) + sum(hb[k] * abs(Rb[:, k] @ ax) for k in range(3)) - abs((cb - ca) @ ax) for ax in axes)


def lab(h):
    """CIE L*a*b* (D65) of an sRGB colour."""
    rgb = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def ellipse_breadth(perimeter, depth):
    """The breadth of an ellipse of this perimeter (Ramanujan's first) and depth."""
    p = lambda a, b: math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))
    lo, hi = 0.0, perimeter / 2
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if p(mid / 2, depth / 2) < perimeter else (lo, mid)
    return (lo + hi) / 2


def leg_scale(design, person, side):
    """The design's knee pivot height (shank link + foot box height) over the Hybrid III's."""
    parts = {p["id"]: p for p in design["parts"]}
    s = sorted(parts[f"{person}.shank_{side}"]["box"]["size_mm"])
    foot = min(parts[f"{person}.foot_{side}"]["box"]["size_mm"])
    return (s[2] - s[0] + foot) / (TABLE_IN["knee_pivot_height"] * 25.4)


def persons(design):
    return list(dict.fromkeys(p["id"].split(".", 1)[0] for p in design["parts"] if p["kind"] == "occupant"))


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class DummyBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False, assets=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        cls.designs = all_designs()
        cls.errors = {}
        cls.drawn = {}
        for look in ("real", "plain"):
            page = cls.browser.new_page(viewport={"width": 1280, "height": 800})
            errors = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            page.on("console", lambda m, errors=errors: m.type == "error" and errors.append(m.text))
            page.goto(f"{cls.base}/?run=sample&pace=0&t=0&look={look}", wait_until="domcontentloaded")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
            page.evaluate("window.hiraku.ready")
            if look == "real":
                cls.table = page.evaluate(TABLE_OF)
                for name, d, _ in cls.designs:
                    cls.drawn[name] = page.evaluate(DRAWN_OF, [events_with(d)])
            else:
                cls.plain = page.evaluate(DRAWN_OF, [events_with(cls.designs[0][1])])
            cls.errors[look] = errors
            page.close()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp)

    def test_no_errors(self):
        self.assertEqual(self.errors, {"real": [], "plain": []})
        # the showcase's four car runs (the whole dummy, a head and limbs) are among the designs
        # drawn; other runs' occupants (the care bed's lying person: back, seat, thigh, leg) are too
        # (and others since: 20261107-164004-free-utv)
        whole = {n.split(" v")[0] for n, d, _ in self.designs if n.startswith("2026") and any(f"{p}.head" in {q["id"] for q in d["parts"]} for p in persons(d))}
        cars = {"20261105-091217-offroad-4w", "20261105-094333-offroad-4w", "20261105-150416-offroad-4w-scripted", "20261105-160725-offroad-4w"}
        self.assertLessEqual(cars, whole, whole)

    def test_table_in_inches_and_millimetres(self):
        self.assertEqual(self.table["inch"], TABLE_IN)
        self.assertEqual(self.table["lb"], TABLE_LB)
        for key, v in TABLE_IN.items():
            self.assertAlmostEqual(self.table["mm"][key], v * 25.4, places=9, msg=key)
        # some by name: sitting height, shoulder breadth, buttock-knee, knee pivot height
        self.assertAlmostEqual(self.table["mm"]["sitting_height"], 883.92, places=6)
        self.assertAlmostEqual(self.table["mm"]["shoulder_breadth"], 429.26, places=6)
        self.assertAlmostEqual(self.table["mm"]["buttock_knee"], 591.82, places=6)
        self.assertAlmostEqual(self.table["mm"]["knee_pivot_height"], 492.76, places=6)

    def test_shape_ratios_are_the_tables(self):
        t, s = TABLE_IN, self.table["shape"]
        want = {
            "chestBreadth": ellipse_breadth(t["chest_circumference"], t["chest_depth"]) / t["shoulder_breadth"],
            "chestDepth": t["chest_depth"] / t["shoulder_breadth"],
            "waist": t["waist_circumference"] / t["chest_circumference"],
            "hipBreadth": t["hip_breadth"] / t["shoulder_breadth"],
            "footBreadth": t["foot_breadth"] / t["foot_length"],
            "kneeHalf": (t["buttock_knee"] - t["buttock_popliteal"]) / 2 * 25.4,
            "thighHalf": t["thigh_clearance"] / 2 * 25.4,
            "ankleHalf": t["foot_breadth"] / 2 * 25.4,
            "kneeHeight": t["knee_pivot_height"] * 25.4,
        }
        self.assertEqual(set(s), set(want))
        for k, v in want.items():
            self.assertAlmostEqual(s[k], v, places=6, msg=k)
        # the ellipse of the chest's girth and depth: its perimeter is the girth again
        b, d = want["chestBreadth"] * t["shoulder_breadth"], t["chest_depth"]
        self.assertAlmostEqual(math.pi * (3 * (b + d) / 2 - math.sqrt((3 * b / 2 + d / 2) * (b / 2 + 3 * d / 2))), t["chest_circumference"], places=6)

    def test_joints_and_their_places(self):
        # per person 18 joints: the neck, the waist, both ends of the 8 limbs; at the design's joint
        # centres (a section's radius in from each end); the leg's radii the table's, scaled, cut to the box
        limbs = ("arm", "forearm", "thigh", "shank")
        checked = 0
        for name, d, _ in self.designs:
            got = self.drawn[name]["parts"]
            parts = {p["id"]: p for p in d["parts"]}
            for person in persons(d):
                if f"{person}.head" not in parts:
                    continue  # (the frozen zu copies: the torso holds the head, no forearms)
                joints = [m for i, o in got.items() if i.split(".", 1)[0] == person for m in o["meshes"] if m["piece"] == "joint"]
                self.assertEqual(len(joints), 2 + 16, (name, person))
                for limb in limbs:
                    for side in "LR":
                        p = parts[f"{person}.{limb}_{side}"]
                        size = p["box"]["size_mm"]
                        long = int(np.argmax(size))
                        r = min(s for i, s in enumerate(size) if i != long) / 2
                        balls = [m for m in got[p["id"]]["meshes"] if m["piece"] == "joint"]
                        mid = lambda loc: float((loc[:, long].min() + loc[:, long].max()) / 2)
                        ends = sorted((mid(in_box(p["box"], m["verts"])[0]), m["radius"]) for m in balls)
                        np.testing.assert_allclose([e[0] for e in ends], [-(size[long] / 2 - r), size[long] / 2 - r], atol=0.5, err_msg=f"{name} {p['id']}")
                        k = leg_scale(d, person, side)
                        sh = self.table["shape"]
                        want = {"thigh": (sh["thighHalf"] * k, sh["kneeHalf"] * k), "shank": (sh["kneeHalf"] * k, sh["ankleHalf"] * k),
                                "forearm": (r, r * WRIST)}.get(limb, (r, r))
                        want = sorted(min(x, r) for x in want)
                        self.assertEqual(sorted(round(e[1], 6) for e in ends), [round(x, 6) for x in want], (name, p["id"]))
                        checked += 1
        self.assertGreater(checked, 100)

    def test_torso_breadths_from_the_ratios(self):
        sh = self.table["shape"]
        for name, d, _ in self.designs:
            got = self.drawn[name]["parts"]
            parts = {p["id"]: p for p in d["parts"]}
            for person in persons(d):
                if f"{person}.head" not in parts:
                    continue
                box = parts[person]["box"]
                W, D = box["size_mm"][1], box["size_mm"][0]
                chest, waist, pelvis = got[person]["meshes"]
                self.assertEqual([chest["piece"], waist["piece"], pelvis["piece"]], ["rounded_box", "joint", "pelvis"])
                span = lambda m, i: float(np.ptp(in_box(box, m["verts"])[0][:, i]))
                back = lambda m: float(in_box(box, m["verts"])[0][:, 0].min())
                self.assertAlmostEqual(span(chest, 1), min(W, W * sh["chestBreadth"]), delta=TOL_MM, msg=name)
                self.assertAlmostEqual(span(chest, 0), min(D, W * sh["chestDepth"]), delta=TOL_MM, msg=name)
                self.assertAlmostEqual(span(waist, 1), span(chest, 1) * sh["waist"], delta=TOL_MM, msg=name)
                self.assertAlmostEqual(span(pelvis, 1), min(W, W * sh["hipBreadth"]), delta=TOL_MM, msg=name)
                self.assertAlmostEqual(span(pelvis, 0), D, delta=TOL_MM, msg=name)
                for m in (chest, waist, pelvis):
                    self.assertAlmostEqual(back(m), -D / 2, delta=TOL_MM, msg=name)  # against the seat back
                # the feet: the foot's breadth its length x the table's ratio, cut to the box
                for side in "LR":
                    fp = parts[f"{person}.foot_{side}"]
                    (foot,) = got[fp["id"]]["meshes"]
                    L, B = fp["box"]["size_mm"][0], fp["box"]["size_mm"][1]
                    self.assertAlmostEqual(span_box(fp["box"], foot, 1), min(B, L * sh["footBreadth"]), delta=TOL_MM, msg=name)

    def test_every_piece_inside_its_box(self):
        for name, d, _ in self.designs:
            for p in d["parts"]:
                if p["kind"] != "occupant":
                    continue
                for m in self.drawn[name]["parts"][p["id"]]["meshes"]:
                    local, half = in_box(p["box"], m["verts"])
                    over = np.abs(local).max(axis=0) - half
                    self.assertTrue((over <= TOL_MM).all(), (name, p["id"], m["piece"], over.tolist()))

    def test_meets_no_other_part(self):
        """The dummy's pieces against every other part's box: a vertex inside it by more than 1 mm
        counts. A hand on the steering wheel holds it (not counted; nor the zu copies' whole arms,
        which end on the rim with no hand). What is left goes no deeper than its segment's own box
        does (the design's overlap): the forearms in the wheel's box, but outside its drawn ring
        (shapes.js steering); the thighs and the seats are not met any more (their boxes overlap
        25 to 36 mm)."""
        report = []
        for name, d, _ in self.designs:
            others = [p for p in d["parts"] if p["kind"] != "occupant"]
            got = self.drawn[name]["parts"]
            for p in d["parts"]:
                if p["kind"] != "occupant":
                    continue
                verts = [v for m in got[p["id"]]["meshes"] for v in m["verts"]]
                for q in others:
                    deep = depth_in(q["box"], verts)
                    if deep <= 1.0:
                        continue
                    seg = p["id"].split(".", 1)[-1]
                    # a hand holds the wheel; so does the end of a whole arm with no hand (the zu copies)
                    person = p["id"].split(".", 1)[0]
                    no_hand = seg.startswith("arm") and not any(x["id"] == f"{person}.hand{seg[3:]}" for x in d["parts"])
                    if (seg.startswith("hand") or no_hand) and q["kind"] == "steering":
                        continue
                    own = boxes_overlap(p["box"], q["box"])
                    ring = depth_in_ring(q["box"], verts) if q["kind"] == "steering" else None
                    report.append((name, p["id"], q["id"], round(deep, 1), round(own, 1), ring))
                    self.assertLessEqual(deep, own + TOL_MM, (name, p["id"], q["id"]))
        # what is left: the arms in the wheel's box, outside its ring
        self.assertEqual({(r[1].split(".", 1)[1].split("_")[0], r[2].split("_")[0]) for r in report} - {("forearm", "steering")}, set(), report)
        self.assertTrue(report)
        for r in report:
            self.assertLessEqual(r[5], 0.0, r)

    def test_knee_room_where_the_record_has_it(self):
        """H3 knee_room = L - L_bk: the x from the seat back to the part ahead at knee height, less
        AIST J5. The drawn knee's front (the knee ball's front, its x) to that part, against the
        record: within 3 mm for the driver of every version of 20261105-160725 and -150416 and of the
        versions whose seat does not slide (v1: -69.25 recorded, -67.74 drawn). Not asserted: the
        200 mm slides of -091217 v2 and -094333 v2 to v4 (about 9 mm apart: the record sits the big
        driver elsewhere on the slide, k4_geometry.people.poses, than the design's occupant boxes),
        20261107-164004-free-utv (its occupant boxes are not K4's pose: the thigh's pitch goes from
        -6.4 deg at v1 to -10.0 deg at v5 while K4's knee room stays 55.75 mm; drawn at v1: 40.29 mm,
        the design's boxes as they are) and the passengers, whose knee is bent otherwise than the
        record's straight buttock-knee length."""
        rows = []
        for name, d, record in self.designs:
            if record is None:
                continue
            fit = people.fit(d)
            for k, (room, obstacle) in fit.items():
                if not k.startswith("knee_room."):
                    continue
                person = k.split(".", 1)[1]
                x0 = float(people._extent({p["id"]: p for p in d["parts"]}[obstacle])[0][0])
                knee_x = max(max(v[0] for v in m["verts"]) for side in "LR" for m in self.drawn[name]["parts"][f"{person}.thigh_{side}"]["meshes"] if m["piece"] == "joint")
                drawn = x0 - knee_x
                if person == "occupant_R":
                    self.assertAlmostEqual(room, record, places=6, msg=name)  # the record is K4's own
                rows.append((name, person, round(room, 2), round(drawn, 2)))
                own_pose = not name.startswith("20261107-164004")  # (its boxes are not K4's pose: the docstring)
                if person == "occupant_R" and own_pose and (people.seat_slide(d) == 0.0 or name.startswith(("20261105-160725", "20261105-150416"))):
                    self.assertLess(abs(drawn - room), 3.0, (name, person, room, drawn))
        self.assertTrue(any(r[2] < -69 for r in rows), rows)  # the -69.25 mm of the first versions is among them
        self.assertTrue(any(r[0].startswith("20261105-160725") for r in rows), rows)  # (its later versions: the same parts as v2)

    def test_colours(self):
        for name, d, _ in self.designs:
            got = self.drawn[name]["parts"]
            for person in persons(d):
                if f"{person}.head" not in got:
                    continue
                mine = [m for i, o in got.items() if i.split(".", 1)[0] == person for m in o["meshes"]]
                self.assertEqual({m["color"] for m in mine}, {BODY_YELLOW, JOINT_BLACK}, (name, person))
                self.assertEqual({m["color"] for m in mine if m["piece"] == "joint"}, {JOINT_BLACK}, (name, person))
        # the first design with the whole dummy (a head): not the care bed's lying person
        name, d = next((n, d) for n, d, _ in self.designs for p in persons(d)[:1] if f"{p}.head" in self.drawn[n]["parts"])
        real = self.drawn[name]["parts"]
        person = persons(d)[0]
        by = lambda seg: real[f"{person}.{seg}" if seg else person]["meshes"]
        for seg in ("neck", "hand_L", "hand_R", "foot_L", "foot_R"):
            self.assertEqual({m["color"] for m in by(seg)}, {JOINT_BLACK}, seg)
        for seg in ("head", "arm_L", "thigh_R", None):
            body = [m for m in by(seg) if m["piece"] != "joint"]
            self.assertEqual({m["color"] for m in body}, {BODY_YELLOW}, seg)
            self.assertEqual({m["roughness"] for m in body}, {0.45}, seg)
        for what, other in KEEP_APART.items():
            self.assertGreaterEqual(math.dist(lab(BODY_YELLOW), lab(other)), 15.0, what)
        # (the yellow the outreach team had, #e8b21e, is 13.1 from the contour's yellow: too near)
        self.assertLess(math.dist(lab("#e8b21e"), lab(KEEP_APART["contour yellow"])), 15.0)
        self.assertLess(math.dist(lab("#e8b21e"), lab(BODY_YELLOW)), 3.5)

    def test_details_line(self):
        # the real look: the workbench's くわしく holds the line; the diagram look: no くわしく
        for name, _, _ in self.designs:
            self.assertEqual(self.drawn[name]["more"], {"hidden": False, "text": DUMMY_LINE}, name)
        self.assertTrue(self.plain["more"]["hidden"])
        self.assertEqual(self.table["note"], DUMMY_LINE)


def span_box(box, mesh, i):
    return float(np.ptp(in_box(box, mesh["verts"])[0][:, i]))


if __name__ == "__main__":
    unittest.main()
