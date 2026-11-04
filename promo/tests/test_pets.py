import importlib.util
import json
import math
import re
import socket
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PETS = ROOT / "promo" / "pets"
PET3D = (PETS / "pet3d.js").read_text(encoding="utf-8")

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


def has_network(host="cdn.jsdelivr.net", port=443, timeout=3.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


ROLE_ROW = r'\{ (?:module: "(\w+)", )?id: "([^"]+)", name: "([^"]+)", color: "(#[0-9a-f]{6})", prop: "(\w+)", propName: "([^"]+)" \}'


def role_rows():
    """(id, name, colour, prop, propName, module or "") of every ROLES row.
    v10: the module rows start with the module key, so the rows starting
    with `{ id:` are exactly the ten app roles."""
    block = PET3D[PET3D.index("const ROLES = ["):]
    block = block[: block.index("];")]
    return [r[1:] + (r[0],) for r in re.findall(ROLE_ROW, block)]


def roles_in_pet3d():
    """(id, name, colour, prop, propName) of all roles, in order."""
    return [r[:5] for r in role_rows()]


def module_roles():
    """(id, name, colour, prop, propName, module) of the v10 module designers."""
    return [r for r in role_rows() if r[5]]


def app_roles():
    """The ten roles the app knows (the rows without a module key)."""
    return [r[:5] for r in role_rows() if not r[5]]


BODY_ROW = r'"?([\w.]+)"?: \{ kind: "([^"]+)", quantity: (?:"([^"]+)"|null), analysis: "([^"]+)" \}'


def bodies_in_pet3d():
    block = PET3D[PET3D.index("const BODIES = {"):]
    block = block[: block.index("};")]
    rows = re.findall(BODY_ROW, block)
    # quantity None = a body without a contour
    return [(rid, kind, q or None, a) for rid, kind, q, a in rows]


def module_bodies_in_pet3d():
    block = PET3D[PET3D.index("const MODULE_BODIES = {"):]
    block = block[: block.index("};")]
    return [(m, kind, q or None, a) for m, kind, q, a in re.findall(BODY_ROW, block)]


def lab(h):
    """sRGB hex -> CIE L*a*b* (D65)."""
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    xyz = (0.4124564 * c[0] + 0.3575761 * c[1] + 0.1804375 * c[2],
           0.2126729 * c[0] + 0.7151522 * c[1] + 0.0721750 * c[2],
           0.0193339 * c[0] + 0.1191920 * c[1] + 0.9503041 * c[2])
    f = lambda t: t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29
    fx, fy, fz = (f(v / n) for v, n in zip(xyz, (0.95047, 1.0, 1.08883)))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def de2000(c1, c2):
    """CIEDE2000 colour difference (Sharma, Wu and Dalal 2005 formulation)."""
    L1, a1, b1 = lab(c1)
    L2, a2, b2 = lab(c2)
    Cb = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dhp = 0 if C1p * C2p == 0 else (h2p - h1p + 180) % 360 - 180
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    else:
        hbp = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    RC = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    SL = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    SC, SH = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    RT = -math.sin(math.radians(60 * math.exp(-(((hbp - 275) / 25) ** 2)))) * RC
    dL, dC, dH = (L2 - L1) / SL, (C2p - C1p) / SC, dHp / SH
    return math.sqrt(dL ** 2 + dC ** 2 + dH ** 2 + RT * dC * dH)


def stops(src):
    return re.findall(r"#[0-9a-f]{6}", re.search(r"STOPS = \[([^\]]+)\]", src).group(1))


def band_colors_from_colormap_js():
    """BAND_COLORS as web/js/colormap.js computes them (JS Math.round)."""
    cm = (ROOT / "web" / "js" / "colormap.js").read_text(encoding="utf-8")
    bands = int(re.search(r"BANDS = (\d+)", cm).group(1))
    rgb = [[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in stops(cm)]
    out = []
    for k in range(bands):
        x = min(1.0, max(0.0, (k + 0.5) / bands)) * (len(rgb) - 1)
        i = min(len(rgb) - 2, int(x // 1))
        f = x - i
        c = [math.floor(a + (b - a) * f + 0.5) for a, b in zip(rgb[i], rgb[i + 1])]
        out.append("#" + "".join("%02x" % v for v in c))
    return out


def app_role_ids():
    schema = json.loads((ROOT / "hiraku" / "contract" / "hiraku.schema.json").read_text(encoding="utf-8"))
    pattern = None

    def walk(node):
        nonlocal pattern
        if isinstance(node, dict):
            p = node.get("pattern")
            if isinstance(p, str) and "cae\\." in p and "lead" in p:
                pattern = p
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    m = re.match(r"\^\((.*)\|cae\\\.\((.*)\)\)\$", pattern)
    plain = [x for x in m.group(1).split("|") if x not in ("system", "user")]
    return plain + ["cae." + d for d in m.group(2).split("|")]


class Roles(unittest.TestCase):
    def test_ten_roles_match_the_app_ids(self):
        # the first ten are the app's roles, then the six module designers (v10)
        roles = roles_in_pet3d()
        self.assertEqual(len(roles), 16)
        self.assertEqual([r[0] for r in roles[:10]], app_role_ids())
        self.assertEqual([r[0] for r in roles[:10]], [r[0] for r in app_roles()])
        self.assertEqual(roles[10:], [m[:5] for m in module_roles()])
        # the cast checks of the cuts read the rows starting with `{ id:` (the ten)
        self.assertEqual(re.findall(r'\{ id: "([a-z.]+)", name:', PET3D), app_role_ids())

    def test_module_roles(self):
        mods = module_roles()
        self.assertEqual([m[5] for m in mods], ["frame", "suspension", "steering", "occupant", "powertrain", "body"])
        self.assertEqual([m[0] for m in mods], ["designer.frame", "designer.suspension", "designer.steering",
                                                "designer.occupant", "designer.powertrain", "designer.body"])
        self.assertEqual([m[3] for m in mods], ["torch", "coil", "pedal", "seat", "plug", "fender"])
        for m in mods:
            self.assertTrue(m[1].endswith("の設計役"), m)
            # the id is written in ROLES only (nowhere else in pet3d.js)
            self.assertEqual(PET3D.count('"%s"' % m[0]), 1, m[0])
            self.assertRegex(PET3D, r"\b%s: 0\.\d+," % m[3])  # PROP_MASS row
        # each module row has a body row (by the module key) of the CAD kind, without a contour
        bodies = module_bodies_in_pet3d()
        self.assertEqual([b[0] for b in bodies], [m[5] for m in mods])
        for _, kind, q, a in bodies:
            self.assertEqual(kind, "cad-model")
            self.assertIsNone(q)
            self.assertIn("CAD", a)
        self.assertIn("for (const r of ROLES) if (r.module) BODIES[r.id] = MODULE_BODIES[r.module];", PET3D)

    def test_module_colours_are_distinct(self):
        old = [r[2] for r in app_roles()]
        new = [m[2] for m in module_roles()]
        self.assertEqual(len(set(new)), 6)
        d = min([de2000(a, b) for a in new for b in old] + [de2000(a, b) for i, a in enumerate(new) for b in new[i + 1:]])
        self.assertGreater(d, 20, d)
        # same colour -> 0; the older ten have a close pair (designer / aero)
        self.assertLess(de2000("#2fa7d6", "#2fa2c3"), 5)
        self.assertAlmostEqual(de2000("#57b8a0", "#57b8a0"), 0.0)

    def test_ten_older_pets_unchanged_in_the_source(self):
        # the designer keeps its face colour; only rows with a module key are tinted
        self.assertIn("const face = mixHex(C.inkFaint, C.s[0], 0.3);", PET3D)
        self.assertIn("ctx.role.module ? mixHex(face, hexNum(ctx.role.color), MODULE_TINT) : face", PET3D)
        # the new live parts are set only by the new props
        props = PET3D[PET3D.index("// ---- v10: the module designers' props ----"):PET3D.index("function buildCheck(")]
        for k in ("live.spark =", "live.coil ="):
            self.assertEqual(PET3D.count(k), props.count(k), k)
            self.assertGreater(props.count(k), 0, k)
        self.assertNotIn("Math.random", props)

    def test_props_are_distinct(self):
        roles = roles_in_pet3d()
        self.assertEqual(len({r[3] for r in roles}), 16)
        self.assertEqual(len({r[4] for r in roles}), 16)
        for r in roles:
            self.assertIn(f'case "{r[3]}"', PET3D, r[0])

    def test_colours_come_from_the_repo(self):
        css = (ROOT / "promo" / "screens" / "common.css").read_text(encoding="utf-8")
        app = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
        for rid, _, colour, _, _ in app_roles():
            if rid.startswith("cae."):
                m = re.search(r"--d-%s:\s*(#[0-9a-f]{6})" % rid[4:], css)
            else:
                m = re.search(r'%s: "(#[0-9a-f]{6})"' % rid, app)
            self.assertIsNotNone(m, rid)
            self.assertEqual(m.group(1), colour, rid)

    def test_stress_steps_match_common_css(self):
        css = (ROOT / "promo" / "screens" / "common.css").read_text(encoding="utf-8")
        want = [re.search(r"--s%d:\s*#([0-9a-f]{6})" % i, css).group(1) for i in range(1, 6)]
        got = re.search(r"s: \[([^\]]+)\]", PET3D).group(1)
        self.assertEqual([x.strip()[2:] for x in got.split(",")], want)

    def test_contour_bands_match_colormap_js(self):
        cm = (ROOT / "web" / "js" / "colormap.js").read_text(encoding="utf-8")
        self.assertEqual(re.search(r"BANDS = (\d+)", PET3D).group(1), re.search(r"BANDS = (\d+)", cm).group(1))
        self.assertEqual(stops(PET3D), stops(cm))
        self.assertIn("web/js/colormap.js", PET3D)

    def test_low_poly_mesh_levels(self):
        # three.js IcosahedronGeometry detail 1 -> 80 faces, detail 2 -> 180
        self.assertRegex(PET3D, r"DETAIL = \{ coarse: 1, fine: 2 \}")

    def test_version_mark_is_v10(self):
        viewer = (PETS / "viewer.html").read_text(encoding="utf-8")
        self.assertIn('<div id="ver">v10</div>', viewer)
        readme = (PETS / "README.md").read_text(encoding="utf-8")
        self.assertIn("版は v10", readme)
        self.assertIn("// v9:", PET3D)
        self.assertIn("// v10:", PET3D)
        # README: the table of the six (id, name, prop, colour) and where the app takes the colours
        for rid, name, colour, _, _, _ in module_roles():
            self.assertRegex(readme, r"\| `%s` \| %s \| [^|\n]+ \| `%s` \|" % (re.escape(rid), re.escape(name), colour), rid)
        self.assertIn("アプリの色は ここ", readme)
        # viewer: the module row under the ten
        self.assertIn("ROLES.filter((r) => !r.module).length", viewer)

    def test_v9_states_lite_and_cost_in_the_source(self):
        # the two states, their marks, o.since / o.mark, lite and cost
        for name in ("function waitPose(t)", "function blockPose(u)", "function blockedU(t, o)",
                     "function buildWait(THREE, M, hulls)", "function buildStop(THREE, M, hulls)",
                     "function liteThree(T)", "function prunePet(THREE, root, hulls, dots)", "function cost()"):
            self.assertIn(name, PET3D)
        self.assertIn("o.since", PET3D)
        self.assertIn("ex.mark === false", PET3D)
        self.assertIn("if (opts.lite) THREE = liteThree(THREE);", PET3D)
        self.assertIn("if (opts.lite) prunePet(THREE, root, hulls, ctx.dots);", PET3D)
        self.assertIn("const BLOCK = { react: 3.0,", PET3D)
        # the stop sign is red (C.miss) like the question mark
        stop = PET3D[PET3D.index("function buildStop("):]
        self.assertIn("color: C.miss", stop[: stop.index("return g;")])
        self.assertRegex(PET3D, r"window\.HirakuPets = \{[^}]*\bWAIT\b[^}]*\bBLOCK\b[^}]*\bLITE\b")
        viewer = (PETS / "viewer.html").read_text(encoding="utf-8")
        self.assertIn('<button data-v="waiting">待ち</button><button data-v="blocked">止められた</button>', viewer)
        self.assertIn('data-key="lite"', viewer)
        self.assertIn('p.set("lite", "1")', viewer)
        self.assertIn("lite: S.lite", viewer)
        readme = (PETS / "README.md").read_text(encoding="utf-8")
        for word in ("`waiting`", "`blocked`", "o.since", "`{ mark: false }`", "`lite`", "`pet.cost()`", "poseTime"):
            self.assertIn(word, readme)

    def test_packager_style_switch(self):
        # v8: the box is the default; opts.packagerStyle = "burr" / "rings"
        # pick the v7 bodies; the viewer has a switch shown for the packager
        # only, in the URL
        self.assertIn('ctx.opts.packagerStyle === "rings"', PET3D)
        self.assertIn('ctx.opts.packagerStyle === "burr"', PET3D)
        self.assertIn("return bodyPackagerBox(ctx);", PET3D)
        viewer = (PETS / "viewer.html").read_text(encoding="utf-8")
        self.assertIn('<div class="seg" data-key="pkg">', viewer)
        self.assertIn('<button data-v="box">箱</button><button data-v="burr">組木</button><button data-v="rings">輪</button>', viewer)
        self.assertIn('pkg: "box" }', viewer)
        self.assertIn('if (S.pkg !== "box") p.set("pkg", S.pkg);', viewer)
        self.assertIn('S.role === "packager"', viewer)
        self.assertIn('p.set("pkg", S.pkg)', viewer)
        self.assertIn("packagerStyle: S.pkg", viewer)
        readme = (PETS / "README.md").read_text(encoding="utf-8")
        self.assertIn("packagerStyle", readme)
        self.assertIn("`pkg`", readme)

    def test_states_and_buttons(self):
        states = re.search(r"const STATES = \[([^\]]+)\]", PET3D).group(1)
        self.assertEqual(re.findall(r'"(\w+)"', states), ["idle", "report", "pass", "mismatch", "working", "run", "waiting", "blocked"])
        viewer = (PETS / "viewer.html").read_text(encoding="utf-8")
        # the working state is called 作業中 (the internal name stays "working")
        self.assertIn('<button data-v="working">作業中</button>', viewer)
        self.assertIn('<button data-v="run">走る</button>', viewer)
        self.assertNotIn("解析中", viewer)
        self.assertNotIn("解析中", PET3D)
        readme = (PETS / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("解析中", readme)
        self.assertIn("作業中", readme)
        self.assertIn("`run`", readme)
        # run across: an option of create and a URL argument of the viewer
        self.assertIn('opts.run === "across"', PET3D)
        self.assertIn('p.set("run", "across")', viewer)

    def test_viewer_turns_freely(self):
        viewer = (PETS / "viewer.html").read_text(encoding="utf-8")
        # trackball: no up / down limit; the pose (offset + up) goes to the URL
        self.assertIn("TrackballControls", viewer)
        self.assertNotIn("OrbitControls", viewer)
        self.assertIn('p.set("orbit"', viewer)
        self.assertIn('p.set("pan"', viewer)

    def test_each_role_has_its_own_body(self):
        bodies = bodies_in_pet3d()
        self.assertEqual([b[0] for b in bodies], [r[0] for r in app_roles()])
        self.assertEqual(len({b[1] for b in bodies}), 10, "body kinds must differ")
        quantities = [b[2] for b in bodies if b[2] is not None]
        self.assertEqual(len(quantities), 8, "only the designer and the packager have no contour")
        self.assertEqual(len(set(quantities)), 8, "contour quantities must differ")
        block = PET3D[PET3D.index("const BUILDERS = {"):]
        block = block[: block.index("};")]
        for rid, kind, _, _ in bodies:
            self.assertRegex(block, r'"%s": body\w+,' % re.escape(kind), rid)
        quantity = {b[0]: b[2] for b in bodies}
        self.assertEqual(quantity["cae.aero"], "圧力 (相対)")
        self.assertEqual(quantity["cae.crash"], "衝撃の応力 (相対)")
        self.assertIsNone(quantity["packager"])
        self.assertIsNone(quantity["designer"])
        kind = {b[0]: b[1] for b in bodies}
        self.assertEqual(kind["lead"], "fe-sphere")
        self.assertEqual(kind["designer"], "cad-model")
        self.assertEqual(kind["packager"], "assembly")
        self.assertEqual(kind["cae.handling"], "multibody")
        self.assertEqual(kind["cae.strength"], "stress-concentration")
        self.assertEqual(kind["cae.powertrain"], "combustion")
        self.assertEqual(kind["cae.crash"], "impact-wave")
        self.assertEqual(quantity["cae.strength"], "応力集中 (公称からの上がり分)")
        self.assertEqual(quantity["cae.handling"], "ばねの力 (相対)")
        self.assertEqual(quantity["cae.nvh"], "変位 (相対)")
        self.assertEqual(quantity["cae.thermal"], "温度 (相対)")
        # README carries the table of bodies
        readme = (PETS / "README.md").read_text(encoding="utf-8")
        mods = [(m[0], b[1], b[2], b[3]) for m, b in zip(module_roles(), module_bodies_in_pet3d())]
        for rid, kind, q, _ in bodies + mods:
            self.assertRegex(readme, r"`%s`[^\n]*`%s`[^\n]*%s" % (re.escape(rid), re.escape(kind), re.escape(q or "なし")), rid)

    def test_v6_fixes_in_the_source(self):
        def fn(name):
            body = PET3D[PET3D.index("function %s(ctx" % name):]
            return body[: body.index("\n  }\n")]

        # lead is built like the v2 body: jittered icosphere nodes + the limb-load stress
        lead = fn("bodyLead")
        self.assertIn("icoNodes(ctx, (d) => shapePoint(THREE, d))", lead)
        self.assertIn("stressBody(ctx,", lead)
        self.assertIn("stressFields(pts, ctx.roots, ctx.propMass)", fn("stressBody"))
        # designer: a CAD model (fillets, one colour, edge lines); no contour,
        # no mesh dots, no dimension lines
        des = fn("bodyDesigner")
        self.assertIn("roundedBox(", des)
        self.assertIn("cadMaterial(", des)
        for word in ("addFe(", "contourMaterial(", "addDots(", "ConeGeometry", "dimension"):
            self.assertNotIn(word, des)
        # packager: no contour, no flow lines, no node dots, no screws;
        # v7: a six-piece burr (bars with notches, rounded, surface nets),
        # not the v6 jigsaw pieces; the rings are the other style
        pkg = fn("bodyPackagerBurr")
        rings = fn("bodyPackagerRings")
        box = fn("bodyPackagerBox")
        for src in (pkg, rings, box):
            for word in ("addFe(", "contourMaterial(", "flowMaterial(", "addDots(", "screw", "tabPts(", "ExtrudeGeometry"):
                self.assertNotIn(word, src)
        self.assertIn("surfaceNets(", pkg)
        self.assertIn("roundBoxSdf(", pkg)
        self.assertEqual(len(re.findall(r'\{ name: "[xyz]-\w+", label: "[^"]+", a: [012],', pkg)), 6)
        # v8 box: shared rounded blocks, instanced edge rods, no surface nets
        self.assertIn("InstancedMesh", box)
        self.assertIn("roundedBlock(", box)
        self.assertIn("geoms.has(key)", box)
        self.assertNotIn("surfaceNets(", box)
        self.assertEqual(len(re.findall(r'\{ name: "[\w-]+", label: "[^"]+", kind: "(?:cuboid|L|T|bar)",', box)), 7)
        self.assertIn("TubeGeometry", rings)
        self.assertIn("MeshPhysicalMaterial", rings)
        # aero: the pressure coefficient (Bernoulli), not the speed
        aero = fn("bodyAero")
        self.assertIn("1 - (1.5 * s) ** 2", aero)
        # nvh: slower, the undeformed lines without a depth test (no blinking)
        nvh = fn("bodyNvh")
        self.assertIn("depthTest: false", nvh)
        self.assertNotIn("opacity: 0.86", nvh)
        freqs = [float(x) for x in re.findall(r"f: ([0-9.]+)", nvh)]
        self.assertTrue(freqs and max(freqs) <= 0.6, freqs)
        self.assertNotIn("ramp(t)", nvh)
        # handling: a multibody model (joints, springs); not the v3 blobs and
        # not the v4 friction circles / lane change track
        han = fn("bodyHandling")
        for word in ("SphereGeometry(0.11", "TorusGeometry(1, 0.06", "pathOk", "cones", "flowMaterial("):
            self.assertNotIn(word, han)
        for word in ("lowArm", "upArm", "knuckle", "spring", "damper", "SphereGeometry(", "triad", "MeshPhysicalMaterial", "metalness"):
            self.assertIn(word, han)
        # strength: the hole and the Kirsch field; not the v2 limb loads
        st = fn("bodyStrength")
        self.assertIn("kirsch", st)
        self.assertNotIn("stressBody(", st)
        self.assertNotIn("icoNodes(", st)
        # crash (unchanged): nodes move only by the small dent
        self.assertRegex(fn("bodyCrash"), r"DENT = \{ depth: 0\.0[0-9]+")

    def test_pose_uses_no_clock_or_random(self):
        for word in ("Date.now", "performance.now", "Math.random", "requestAnimationFrame"):
            self.assertNotIn(word, PET3D)

    def test_plain_script(self):
        self.assertIsNone(re.search(r"^\s*(import|export)\s", PET3D, re.M))
        self.assertIn("window.HirakuPets", PET3D)

    def test_pose_terms_and_anim_in_the_source(self):
        # the extra pose terms a page may pass to update (o), and the shared
        # jump curves (anim) the title cuts use
        for term in ("ex.squash", "ex.lean", "ex.tilt", "ex.antenna", "ex.turn", "ex.arm",
                     "ex.lookX", "ex.lookY", "ex.report", "ex.check", "ex.shadow"):
            self.assertIn(term, PET3D)
        self.assertIn("const ex = o || {};", PET3D)
        self.assertRegex(PET3D, r"window\.HirakuPets = \{[^}]*\banim\b")
        for name in ("function hop(tau, J)", "function jumpY(q, rise, over)", "function easeBack(u, over)"):
            self.assertIn(name, PET3D)

    def test_walk_sit_and_route_in_the_source(self):
        # walking and sitting pose terms (zero when not given, so other cuts' pets
        # move as before), and the route of beats the title cuts use
        for term in ("ex.legs", "ex.arms", "ex.drop", "ex.sit"):
            self.assertIn(term, PET3D)
        self.assertIn("-h.sx * (ex.legs || 0) - legSit", PET3D)
        self.assertIn("const legSit = run ? 0 : (ex.sit || 0) * LEG.sit_legs;", PET3D)
        for name in ("function walkLegs(d, D, L, swing)", "function route(t, start, beats, ctx)",
                     "function routeEnd(beats)", "function travel(u, ramp)"):
            self.assertIn(name, PET3D)
        self.assertRegex(PET3D, r"window\.HirakuPets = \{[^}]*\bLEG\b")
        kinds = re.search(r"const BEAT_KINDS = \[([^\]]*)\]", PET3D).group(1)
        self.assertEqual(re.findall(r'"(\w+)"', kinds), ["walk", "hop", "hops", "turn", "sit", "act", "fade", "tick"])


@unittest.skipUnless(HAS_PLAYWRIGHT, "needs playwright (the video Python, not the HIRAKU venv)")
@unittest.skipUnless(HAS_PLAYWRIGHT and has_network(), "needs the network (three.js comes from jsDelivr)")
class Viewer(unittest.TestCase):
    def open(self, page, url):
        page.goto(url)
        page.wait_for_function("window.petViewer !== undefined", timeout=30000)
        return page.evaluate("window.petViewer.ready")

    def test_ready_resolves(self):
        from playwright.sync_api import sync_playwright

        url = (PETS / "viewer.html").as_uri() + "?view=one&role=cae.crash&state=pass&bg=night&t=1.2"
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 800, "height": 600})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url)
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                info = page.evaluate("window.petViewer.ready")
                self.assertEqual(info["roles"], 16)
                self.assertAlmostEqual(info["t"], 1.2)
                self.assertIn("t=1.2", page.evaluate("location.search"))
                self.assertEqual(errors, [])
            finally:
                browser.close()

    def test_contour_and_legend(self):
        from playwright.sync_api import sync_playwright

        url = (PETS / "viewer.html").as_uri() + "?view=one&role=cae.strength&state=pass&t=0.4"
        js = """() => {
          const HP = window.HirakuPets;
          const sw = [...document.querySelectorAll('#legend .sw')].map(e => getComputedStyle(e).backgroundColor);
          const tk = [...document.querySelectorAll('#legend .tk')].map(e => e.textContent);
          return { bands: HP.BANDS, colors: HP.BAND_COLORS, sw, tk,
                   legend: getComputedStyle(document.getElementById('legend')).display,
                   ramp: [0, 1.0, 2.0, 3.3, 4.5].map(t => HP.loadFactor(t, 'working')),
                   still: HP.loadFactor(2.0, 'pass') };
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 800, "height": 600})
                page.goto(url)
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                page.evaluate("window.petViewer.ready")
                r = page.evaluate(js)
                want = band_colors_from_colormap_js()
                self.assertEqual(r["bands"], 10)
                self.assertEqual(r["colors"], want)
                # legend: 10 swatches, highest band on top, 11 ticks 0.0 .. 1.0
                rgb = ["rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in want]
                self.assertEqual(r["sw"], rgb[::-1])
                self.assertEqual(r["tk"], ["%.1f" % (i / 10) for i in range(11)])
                self.assertEqual(r["legend"], "block")
                self.assertEqual(page.evaluate("document.querySelector('#legend .ttl').textContent"), "応力集中 (公称からの上がり分)")
                # working steps the load 0 -> 1, other states carry the full load
                self.assertEqual(r["ramp"][0], 0)
                self.assertTrue(0 < r["ramp"][1] < r["ramp"][2] < 1)
                self.assertEqual(r["ramp"][3], 1)
                self.assertEqual(r["ramp"][4], 0)
                self.assertEqual(r["still"], 1)

                # no legend in the grid view
                page.goto(url.replace("view=one", "view=grid"))
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                page.evaluate("window.petViewer.ready")
                self.assertEqual(page.evaluate("getComputedStyle(document.getElementById('legend')).display"), "none")

                # the legend title is the quantity of the role
                for role, title in (("cae.aero", "圧力 (相対)"), ("cae.crash", "衝撃の応力 (相対)"), ("cae.thermal", "温度 (相対)"),
                                    ("cae.powertrain", "燃焼の温度 (相対)"), ("lead", "相対の応力 (自重と手足)"),
                                    ("cae.handling", "ばねの力 (相対)")):
                    page.goto(url.replace("cae.strength", role))
                    page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                    page.evaluate("window.petViewer.ready")
                    self.assertEqual(page.evaluate("document.querySelector('#legend .ttl').textContent"), title, role)

                # the packager and the designer have no contour: no legend in the one-pet view either
                for role in ("packager", "packager&pkg=rings", "designer"):
                    page.goto(url.replace("cae.strength", role))
                    page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                    page.evaluate("window.petViewer.ready")
                    self.assertEqual(page.evaluate("getComputedStyle(document.getElementById('legend')).display"), "none", role)
                    # the burr / rings switch is there for the packager only
                    shown = page.evaluate("getComputedStyle(document.getElementById('pkg-grp')).display") != "none"
                    self.assertEqual(shown, role.startswith("packager"), role)
                    self.assertEqual(page.evaluate("window.petViewer.state.pkg"), "rings" if "rings" in role else "box", role)
                    if "rings" in role:
                        self.assertIn("pkg=rings", page.evaluate("location.search"))
            finally:
                browser.close()

    def test_values_follow_the_loads(self):
        # lead: the stress contour follows the limb loads (v2 model)
        from playwright.sync_api import sync_playwright

        # build every pet in the viewer page (it has three.js on its import map)
        page_url = (PETS / "viewer.html").as_uri() + "?view=grid&t=0"
        js = """async () => {
          const THREE = await import('three');
          const out = {};
          for (const mesh of ['coarse', 'fine']) {
            for (const r of window.HirakuPets.ROLES.filter((r) => r.id === 'lead')) {
              const p = window.HirakuPets.create(THREE, { role: r.id, mesh });
              const f = p.field;
              const node = (n) => f.loads.find(l => l.name === n).node;
              const argmax = (a) => a.indexOf(Math.max(...a));
              const top = f.nodes.reduce((b, q, i) => (q[1] > f.nodes[b][1] ? i : b), 0);
              out[mesh + ':' + r.id] = {
                counts: p.counts,
                maxSettled: Math.max(...f.settled),
                argSettled: argmax(f.settled), armR: node('armR'),
                argMis: argmax(f.mismatch), legL: node('legL'),
                top: f.settled[top], legR: f.settled[node('legR')],
                mis: p.contour(1, 'mismatch').max, work: p.contour(0, 'working').max,
              };
              p.dispose();
            }
          }
          return out;
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 400, "height": 300})
                page.goto(page_url)
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                page.evaluate("window.petViewer.ready")
                res = page.evaluate(js)
            finally:
                browser.close()
        # lead is the v2 body: same mesh, same load model
        self.assertEqual(len(res), 2)
        for k, v in res.items():
            want = {"nodes": 42, "elements": 80} if k.startswith("coarse") else {"nodes": 92, "elements": 180}
            self.assertEqual(v["counts"], want, k)
            self.assertAlmostEqual(v["maxSettled"], 1.0, places=6, msg=k)
            # the prop arm root carries the largest value, the head top a small one
            self.assertEqual(v["argSettled"], v["armR"], k)
            self.assertLess(v["top"], 0.15, k)
            self.assertGreater(v["legR"], 0.5, k)
            # mismatch: the concentration moves to the left leg root
            self.assertEqual(v["argMis"], v["legL"], k)
            self.assertGreater(v["mis"], 1.0, k)
            self.assertEqual(v["work"], 0, k)

    def test_bodies_per_role(self):
        from playwright.sync_api import sync_playwright

        page_url = (PETS / "viewer.html").as_uri() + "?view=grid&t=0"
        js = """async () => {
          const THREE = await import('three');
          const HP = window.HirakuPets;
          // contour meshes = the body itself (not flow lines, props or marks)
          // (or, for a body without a contour, the meshes marked userData.body)
          const isBody = (o) => o.isMesh && ((o.material && o.material.userData && o.material.userData.hk) || o.userData.body);
          const snap = (p) => {
            const out = [];
            p.group.updateMatrixWorld(true);
            p.group.traverse((o) => {
              if (isBody(o)) out.push(Array.from(o.geometry.attributes.position.array), Array.from(o.matrixWorld.elements),
                o.geometry.attributes.aVal ? Array.from(o.geometry.attributes.aVal.array) : []);
            });
            return out;
          };
          const height = (p) => {
            const box = new THREE.Box3();
            p.group.updateMatrixWorld(true);
            p.group.traverse((o) => { if (isBody(o)) box.expandByObject(o); });
            return box.max.y - box.min.y;
          };
          const out = {};
          for (const r of HP.ROLES) {
            const a = HP.create(THREE, { role: r.id });
            const b = HP.create(THREE, { role: r.id });
            a.update(0, 'idle');
            const h = height(a);
            const states = {};
            let same = true;
            for (const [t, s] of [[1.37, 'working'], [2.9, 'working'], [1.2, 'mismatch'], [0.45, 'pass'], [0.2, 'run'], [0.47, 'run']]) {
              a.update(t, s); b.update(0.1, 'idle'); b.update(t, s);
              same = same && JSON.stringify(snap(a)) === JSON.stringify(snap(b));
              const c = a.contour(t, s);
              states[s + t] = { quantity: c.quantity, max: c.max, min: c.min, info: c.info.length };
            }
            out[r.id] = { kind: a.body.kind, quantity: a.body.quantity, h, same, states };
            a.dispose(); b.dispose();
          }
          return { out, bodies: HP.BODIES };
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 400, "height": 300})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(page_url)
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                page.evaluate("window.petViewer.ready")
                res = page.evaluate(js)
                self.assertEqual(errors, [])
            finally:
                browser.close()
        out = res["out"]
        # 16 roles; the six module designers share the designer's cad-model
        self.assertEqual(len(out), 16)
        self.assertEqual(len({v["kind"] for v in out.values()}), 10)
        heights = sorted(v["h"] for v in out.values())
        median = heights[len(heights) // 2]
        for rid, v in out.items():
            self.assertEqual(v["kind"], res["bodies"][rid]["kind"], rid)
            # same height in the line-up (within 12 % of the median)
            self.assertLess(abs(v["h"] / median - 1), 0.12, (rid, v["h"], median))
            # same t and state -> same geometry and values
            self.assertTrue(v["same"], rid)
            for k, c in v["states"].items():
                self.assertEqual(c["quantity"], v["quantity"], (rid, k))
                if v["quantity"] is None:
                    # no contour: no values, only the description lines
                    self.assertIsNone(c.get("max"), (rid, k))
                    self.assertGreater(c["info"], 0, (rid, k))
                    continue
                self.assertTrue(math.isfinite(c["max"]) and math.isfinite(c["min"]), (rid, k))
                self.assertGreaterEqual(c["min"], -1e-9, (rid, k))
                self.assertGreaterEqual(c["max"], c["min"], (rid, k))
                self.assertGreater(c["info"], 0, (rid, k))


    def test_v6_bodies(self):
        # designer without contour, packager as a puzzle, aero pressure at the
        # nose, handling as a metal mechanism, strength concentrated at the
        # hole edge, arrows outside
        from playwright.sync_api import sync_playwright

        page_url = (PETS / "viewer.html").as_uri() + "?view=grid&t=0"
        js = """async () => {
          const THREE = await import('three');
          const HP = window.HirakuPets;
          const out = {};
          const contourMats = (p) => {
            let n = 0;
            p.group.traverse((o) => { if (o.material && o.material.userData && (o.material.userData.hk || o.material.userData.flow)) n++; });
            return n;
          };
          // designer: no contour bands, a CAD material, no values
          const de = HP.create(THREE, { role: 'designer' });
          let cad = 0;
          de.group.traverse((o) => { if (o.material && o.material.userData && o.material.userData.cad) cad++; });
          const dc = de.contour(1.0, 'working');
          out.designer = { hk: contourMats(de), cad, quantity: dc.quantity, hasMax: 'max' in dc, info: dc.info.length };
          de.dispose();
          // packager (v7, now packagerStyle = 'burr'): no contour; a six-piece burr, six bars on three axes
          const pk = HP.create(THREE, { role: 'packager', packagerStyle: 'burr' });
          // in body coordinates (the body group, centre of the body at the origin)
          let bodyGroup = null;
          pk.group.traverse((o) => { if (o.userData.bar) bodyGroup = o.parent.parent; });
          const bars = () => {
            pk.group.updateMatrixWorld(true);
            const inv = new THREE.Matrix4().copy(bodyGroup.matrixWorld).invert();
            const r = {};
            pk.group.traverse((o) => {
              if (!o.userData.bar) return;
              const m = new THREE.Matrix4().multiplyMatrices(inv, o.matrixWorld);
              o.geometry.computeBoundingBox();
              const b = o.geometry.boundingBox.clone().applyMatrix4(m);
              r[o.userData.bar] = { axis: o.userData.axis, box: [b.min.x, b.min.y, b.min.z, b.max.x, b.max.y, b.max.z],
                                    tris: o.geometry.index.count / 3 };
            });
            return r;
          };
          pk.update(1.2, 'idle'); const rest = bars();
          // cells of one bar must not overlap another bar: sample points inside each bar (rays)
          const barMeshes = [];
          pk.group.traverse((o) => { if (o.userData.bar) barMeshes.push(o); });
          const sides = barMeshes.map((o) => o.material.side);
          barMeshes.forEach((o) => { o.material.side = THREE.DoubleSide; });
          const barRay = new THREE.Raycaster();
          const inside = (o, p) => {
            // odd number of crossings along +x = inside that bar
            barRay.set(p, new THREE.Vector3(1, 0.0123, 0.0071).normalize());
            barRay.far = 10;
            return barRay.intersectObject(o, false).length % 2 === 1;
          };
          // the centres of the 4 x 4 x 4 cells of the middle cube (body coordinates, u = 0.28)
          let both = 0, one = 0, tried = 0;
          for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) for (let k = 0; k < 4; k++) {
            const p = bodyGroup.localToWorld(new THREE.Vector3(-0.42 + 0.28 * i, -0.42 + 0.28 * j, -0.42 + 0.28 * k));
            const n = barMeshes.filter((o) => inside(o, p)).length;
            tried++;
            if (n > 1) both++;
            if (n === 1) one++;
          }
          barMeshes.forEach((o, i) => { o.material.side = sides[i]; });
          const c = pk.contour(1.0, 'working');
          const states = {}, lit = {};
          for (const [t, s] of [[0.55, 'working'], [1.65, 'working'], [1.2, 'mismatch'], [0.1, 'pass'], [0.8, 'pass'], [2.0, 'pass']]) {
            pk.update(t, s);
            const b = bars();
            states[s + t] = Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v.box.map((x, q) => x - rest[k].box[q])]));
            let n = 0;
            pk.group.traverse((o) => { if (o.userData.bar && o.material.emissiveIntensity > 0.3) n++; });
            lit[s + t] = n;
          }
          const mats = lit;
          out.packager = { hk: contourMats(pk), rest, quantity: c.quantity, hasMax: 'max' in c, parts: pk.body.parts, style: pk.body.style,
                           counts: pk.counts, both, one, tried, states, info: c.info, lit: mats };
          pk.dispose();
          // the other style: three metal rings, linked (no ring touches another)
          const rg = HP.create(THREE, { role: 'packager', packagerStyle: 'rings' });
          const ringMeshes = [];
          rg.group.traverse((o) => { if (o.userData.ring) ringMeshes.push(o); });
          const curves = ringMeshes.map((o) => o.geometry.parameters.path);
          let gap = Infinity;
          for (let a = 0; a < 3; a++) for (let b = a + 1; b < 3; b++) {
            for (let i = 0; i < 240; i++) for (let j = 0; j < 240; j++) {
              gap = Math.min(gap, curves[a].getPoint(i / 240).distanceTo(curves[b].getPoint(j / 240)));
            }
          }
          const rc = rg.contour(1.0, 'idle');
          out.rings = { n: ringMeshes.length, tube: ringMeshes[0].geometry.parameters.radius, gap, style: rg.body.style, hk: contourMats(rg),
                        quantity: rc.quantity, hasMax: 'max' in rc, metal: ringMeshes.map((o) => o.material.metalness),
                        bars: (() => { let n = 0; rg.group.traverse((o) => { if (o.userData.bar) n++; }); return n; })() };
          rg.dispose();
          // aero: the largest pressure is at the stagnation point on the nose
          const ae = HP.create(THREE, { role: 'cae.aero' });
          const af = ae.field;
          const aArg = af.settled.indexOf(Math.max(...af.settled));
          const xs = af.nodes.map((q) => q[0]);
          const ac = ae.contour(1.0, 'idle');
          // shoulder: the widest section (flow coordinate x = 0)
          const shoulder = Math.max(...af.settled.filter((v, i) => Math.abs(xs[i]) < 0.08));
          out.aero = { arg: aArg, nose: af.nose, noseX: af.nodes[af.nose][0], maxX: Math.max(...xs), max: ac.max, min: ac.min, quantity: ac.quantity, shoulder };
          ae.dispose();
          // handling: the chassis is metal
          const hm = HP.create(THREE, { role: 'cae.handling' });
          let metal = null;
          hm.group.traverse((o) => { if (o.userData.metal) metal = { metalness: o.material.metalness, roughness: o.material.roughness, body: !!o.userData.body }; });
          out.metal = metal;
          hm.dispose();
          // handling: ball joints, tyres on the ground, inner wheel lifts on mismatch
          const hd = HP.create(THREE, { role: 'cae.handling' });
          const res = {};
          for (const [t, s] of [[1.2, 'idle'], [1.37, 'working'], [1.2, 'mismatch']]) {
            hd.update(t, s);
            hd.group.updateMatrixWorld(true);
            let balls = 0;
            const tyres = [];
            hd.group.traverse((o) => {
              const g = o.geometry && o.geometry.parameters;
              if (!g) return;
              if (o.geometry.type === 'SphereGeometry' && Math.abs(g.radius - 0.055) < 1e-9) balls++;
              if (o.geometry.type === 'CylinderGeometry' && Math.abs(g.radiusTop - 0.39) < 1e-9) tyres.push(new THREE.Box3().setFromObject(o).min.y);
            });
            res[s] = { balls, tyres, c: hd.contour(t, s) };
          }
          out.handling = res;
          hd.dispose();
          // strength: argmax at the hole edge, top or bottom; far field small
          const st = HP.create(THREE, { role: 'cae.strength' });
          const f = st.field;
          const h = f.hole;
          const pol = f.nodes.map((q) => [Math.hypot(q[0] - h.center[0], q[1] - h.center[1]), Math.atan2(q[1] - h.center[1], q[0] - h.center[0])]);
          const arg = f.settled.indexOf(Math.max(...f.settled));
          const far = Math.max(...f.settled.filter((v, i) => pol[i][0] > 3 * h.radius));
          const side = Math.max(...f.settled.filter((v, i) => pol[i][0] < h.radius + 1e-6 && Math.abs(Math.cos(pol[i][1])) > 0.99));
          st.update(0, 'idle');
          st.group.updateMatrixWorld(true);
          const meshes = [];
          st.group.traverse((o) => { if (o.isMesh && o.material && o.material.userData && o.material.userData.hk) meshes.push(o); });
          const ray = new THREE.Raycaster();
          const arrows = [];
          for (const a of st.arrows) {
            const centre = a.frame.localToWorld(new THREE.Vector3(0, a.tail.y, 0));
            for (const k of [0.0, 0.1, 0.25, a.length]) {
              const p = a.frame.localToWorld(a.tail.clone().addScaledVector(a.dir, k));
              const dir = centre.clone().sub(p);
              ray.set(p, dir.clone().normalize());
              ray.far = dir.length();
              const hits = ray.intersectObjects(meshes, false);
              arrows.push({ k, hits: hits.length, first: hits.length ? hits[0].distance : null });
            }
          }
          out.strength = { max: f.settled[arg], r: pol[arg][0], th: pol[arg][1], a: h.radius, far, side,
                           mis: st.contour(1.2, 'mismatch').max, work0: st.contour(0, 'working').max, arrows, n: st.arrows.length };
          st.dispose();
          return out;
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 400, "height": 300})
                self.open(page, page_url)
                res = page.evaluate(js)
            finally:
                browser.close()
        de = res["designer"]
        self.assertEqual(de["hk"], 0, "no contour bands on the designer")
        self.assertGreaterEqual(de["cad"], 1)
        self.assertIsNone(de["quantity"])
        self.assertFalse(de["hasMax"])
        self.assertGreater(de["info"], 0)
        pk = res["packager"]
        self.assertEqual(pk["hk"], 0, "no contour or flow material on the packager")
        self.assertIsNone(pk["quantity"])
        self.assertFalse(pk["hasMax"])
        self.assertEqual(pk["style"], "burr")
        self.assertEqual(pk["counts"], {"bars": 6, "axes": 3})
        rest = pk["rest"]
        # six bars, two along each of the three axes; a bar is long along its
        # axis (2.0) and 2u = 0.56 across, and the pair sits side by side
        self.assertEqual(len(rest), 6)
        self.assertEqual(sorted(v["axis"] for v in rest.values()), ["x", "x", "y", "y", "z", "z"])
        for name, v in rest.items():
            size = [v["box"][q + 3] - v["box"][q] for q in range(3)]
            a = "xyz".index(v["axis"])
            self.assertAlmostEqual(size[a], 2.0, delta=0.03, msg=name)
            for q in range(3):
                if q != a:
                    self.assertAlmostEqual(size[q], 0.56, delta=0.03, msg=name)
            self.assertGreater(v["tris"], 500, name)
        # all bars cross the middle of the unit (a cross-shaped unit)
        for name, v in rest.items():
            for q in range(3):
                self.assertLess(v["box"][q], 0.01, name)
                self.assertGreater(v["box"][q + 3], -0.01, name)
        # interlocked without overlap: no cell centre of the middle cube is
        # inside two bars; all but the 8 corner cells are inside one
        self.assertEqual(pk["tried"], 64)
        self.assertEqual(pk["both"], 0)
        self.assertEqual(pk["one"], 56)
        # every bar has notches (cells it gives to the bars that cross it)
        for p in pk["parts"]:
            self.assertGreater(p["notches"], 0, p["name"])
        self.assertEqual(len({p["color"] for p in pk["parts"]}), 3)
        st = pk["states"]
        moved = lambda k: [n for n, d in st[k].items() if max(abs(x) for x in d) > 1e-6]
        # working: one bar at a time slides out along its own axis
        for k in ("working0.55", "working1.65"):
            m = moved(k)
            self.assertEqual(len(m), 1, (k, m))
            d = st[k][m[0]]
            a = "xyz".index(rest[m[0]]["axis"])
            self.assertGreater(abs(d[a]), 0.3, k)
            for q in range(3):
                if q != a:
                    self.assertLess(abs(d[q]), 1e-6, k)
        self.assertNotEqual(moved("working0.55"), moved("working1.65"))
        # mismatch: the upper X bar is stuck out to -x and lit red
        self.assertEqual(moved("mismatch1.2"), ["x-upper"])
        self.assertLess(st["mismatch1.2"]["x-upper"][0], -0.25)
        self.assertEqual(pk["lit"]["mismatch1.2"], 1)
        # pass: the last bar is snapping in at first, then everything is seated and lights up once
        self.assertEqual(moved("pass0.1"), ["y-front"])
        self.assertEqual(moved("pass0.8"), [])
        self.assertEqual(pk["lit"]["pass0.8"], 6)
        self.assertEqual(pk["lit"]["pass2"], 0)  # JS writes 2.0 as "2"
        # the rings: three metal rings that do not touch (linked, Borromean)
        rg = res["rings"]
        self.assertEqual(rg["style"], "rings")
        self.assertEqual(rg["n"], 3)
        self.assertEqual(rg["bars"], 0)
        self.assertGreater(rg["gap"], 2 * rg["tube"], "the tubes of two rings do not overlap")
        self.assertTrue(all(m >= 0.8 for m in rg["metal"]))
        self.assertEqual(rg["hk"], 0, "no contour bands on the rings")
        self.assertIsNone(rg["quantity"])
        self.assertFalse(rg["hasMax"])
        ae = res["aero"]
        self.assertEqual(ae["quantity"], "圧力 (相対)")
        self.assertEqual(ae["arg"], ae["nose"], "largest pressure at the stagnation point")
        self.assertAlmostEqual(ae["noseX"], ae["maxX"], places=6, msg="the nose is the front tip")
        self.assertAlmostEqual(ae["max"], 1.0, places=6)
        self.assertLess(ae["shoulder"], 0.15, "low pressure at the shoulder")
        self.assertLess(ae["min"], 0.05)
        m = res["metal"]
        self.assertIsNotNone(m)
        self.assertTrue(m["body"])
        self.assertGreaterEqual(m["metalness"], 0.8)
        self.assertLessEqual(m["roughness"], 0.35)
        hd = res["handling"]
        for s, v in hd.items():
            self.assertEqual(v["balls"], 8, s)
            self.assertEqual(v["c"]["quantity"], "ばねの力 (相対)")
        for y in hd["idle"]["tyres"] + hd["working"]["tyres"]:
            self.assertLess(abs(y), 0.02, "tyres stand on the ground")
        # mismatch: the inner wheel lifts
        self.assertGreater(max(hd["mismatch"]["tyres"]), 0.05)
        st = res["strength"]
        self.assertAlmostEqual(st["max"], 1.0, places=6)
        self.assertAlmostEqual(st["r"], st["a"], places=5, msg="largest value on the hole edge")
        self.assertAlmostEqual(abs(math.sin(st["th"])), 1.0, places=3, msg="at the top or bottom of the hole")
        self.assertLess(st["far"], 0.1)
        self.assertLess(st["side"], 0.01, "the hole edge on the load axis is not concentrated")
        self.assertGreater(st["mis"], 1.0)
        self.assertEqual(st["work0"], 0)
        self.assertEqual(st["n"], 4)
        for r in st["arrows"]:
            # every point of an arrow is outside the body: the side wall lies between it and the middle
            self.assertGreaterEqual(r["hits"], 1, r)
            self.assertGreater(r["first"], 0.02, r)

    def test_packager_box(self):
        # v8: the default packager is a clear box packed full of blocks; the
        # v7 burr and the rings are still there by packagerStyle; every pet
        # stays light (triangles drawn, instances counted)
        from playwright.sync_api import sync_playwright

        page_url = (PETS / "viewer.html").as_uri() + "?view=grid&t=0"
        js = """async () => {
          const THREE = await import('three');
          const HP = window.HirakuPets;
          const tris = (p) => {
            let n = 0;
            p.group.traverse((o) => {
              if (!o.isMesh || !o.geometry) return;
              for (let q = o; q; q = q.parent) if (!q.visible) return;
              const g = o.geometry;
              n += (g.index ? g.index.count : g.attributes.position.count) / 3 * (o.isInstancedMesh ? o.count : 1);
            });
            return n;
          };
          const count = {};
          for (const r of HP.ROLES) {
            for (const st of (r.id === 'packager' ? ['box', 'burr', 'rings'] : [null])) {
              const p = HP.create(THREE, st ? { role: r.id, packagerStyle: st } : { role: r.id });
              p.update(0, 'idle');
              count[st ? r.id + ':' + st : r.id] = tris(p);
              p.dispose();
            }
          }
          const pk = HP.create(THREE, { role: 'packager' });
          let body = null, glass = null;
          const blockMeshes = [];
          pk.group.traverse((o) => { if (o.userData.block) blockMeshes.push(o); if (o.userData.glass && o.userData.body) glass = o; });
          body = glass.parent;
          // boxes of the blocks and the glass walls in body coordinates
          const boxes = () => {
            pk.group.updateMatrixWorld(true);
            const inv = new THREE.Matrix4().copy(body.matrixWorld).invert();
            const per = {};
            for (const o of blockMeshes) {
              o.geometry.computeBoundingBox();
              const b = o.geometry.boundingBox.clone().applyMatrix4(new THREE.Matrix4().multiplyMatrices(inv, o.matrixWorld));
              (per[o.userData.block] = per[o.userData.block] || []).push([b.min.x, b.min.y, b.min.z, b.max.x, b.max.y, b.max.z]);
            }
            return per;
          };
          // the walls stop just inside the edge rods; the box is a cube whose
          // half size is the farthest wall plane
          glass.geometry.computeBoundingBox();
          const gm = Math.max(...glass.geometry.boundingBox.max.toArray());
          const gb = { min: new THREE.Vector3(-gm, -gm, -gm), max: new THREE.Vector3(gm, gm, gm) };
          const lit = () => { const s = new Set(); pk.group.traverse((o) => { if (o.userData.block && o.material.emissiveIntensity > 0.3) s.add(o.userData.block); }); return [...s]; };
          const red = () => { const s = new Set(); pk.group.traverse((o) => { if (o.userData.block && o.material.emissive.getHex() === 0xe0472a && o.material.emissiveIntensity > 0.3) s.add(o.userData.block); }); return [...s]; };
          const at = {};
          for (const [t, s] of [[0, 'idle'], [0.1, 'pass'], [1.2, 'pass'], [2.0, 'pass'], [2.9, 'pass'], [4.2, 'pass'], [0.75, 'working'], [2.25, 'working'], [3.75, 'working'], [1.2, 'mismatch'], [0.15, 'run']]) {
            pk.update(t, s);
            at[s + t] = { boxes: boxes(), lit: lit(), red: red() };
          }
          // the lid: closed at rest and after the pass, open while working
          const lidOff = {};
          for (const [t, s] of [[0, 'idle'], [0.2, 'pass'], [1.2, 'pass'], [0.75, 'working'], [1.2, 'mismatch']]) {
            pk.update(t, s);
            let lid = null;
            pk.group.traverse((o) => { if (o.userData.lid) lid = o.parent; });
            lidOff[s + t] = -lid.position.z;
          }
          let inst = 0, uniq = new Set();
          pk.group.traverse((o) => { if (o.isInstancedMesh) inst += o.count; if (o.userData.block) uniq.add(o.geometry.uuid); });
          const out = { count, style: pk.body.style, parts: pk.body.parts, quantity: pk.contour(1, 'idle').quantity, hasMax: 'max' in pk.contour(1, 'idle'),
                        glass: [gb.min.x, gb.min.y, gb.min.z, gb.max.x, gb.max.y, gb.max.z], at, lidOff, inst, uniq: uniq.size, meshes: blockMeshes.length };
          pk.dispose();
          const styles = {};
          for (const st of ['burr', 'rings']) {
            const p = HP.create(THREE, { role: 'packager', packagerStyle: st });
            let bars = 0, rings = 0, blocks = 0;
            p.group.traverse((o) => { if (o.userData.bar) bars++; if (o.userData.ring) rings++; if (o.userData.block) blocks++; });
            styles[st] = { style: p.body.style, bars, rings, blocks };
            p.dispose();
          }
          out.styles = styles;
          return out;
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 400, "height": 300})
                self.open(page, page_url)
                res = page.evaluate(js)
            finally:
                browser.close()
        # light: the box is under 30 000 triangles, like the other roles
        count = res["count"]
        self.assertLessEqual(count["packager:box"], 30000, count)
        for rid, n in count.items():
            if rid not in ("packager:burr", "packager:rings"):
                self.assertLessEqual(n, 30000, (rid, n))
        self.assertLess(count["packager:box"] * 10, count["packager:burr"], count)
        # the default is the box; no contour
        self.assertEqual(res["style"], "box")
        self.assertIsNone(res["quantity"])
        self.assertFalse(res["hasMax"])
        # 7 blocks fill the 27 cells of the 3 x 3 x 3 box, each cell once;
        # four kinds, three colours
        parts = res["parts"]
        self.assertEqual(len(parts), 7)
        cells = [c for p in parts for c in p["cells"]]
        self.assertEqual(len(cells), 27)
        self.assertEqual(len(set(cells)), 27)
        self.assertEqual({p["kind"] for p in parts}, {"cuboid", "L", "T", "bar"})
        self.assertEqual(len({p["color"] for p in parts}), 3)
        # rounded boxes of the same size share a geometry; rods are instanced
        self.assertLess(res["uniq"], res["meshes"])
        self.assertGreaterEqual(res["inst"], 12 + 8 + 4)
        g = res["glass"]
        half = 1.5 * 0.54

        def inside(boxes, eps=1e-6):
            return all(b[q] >= g[q] - eps and b[q + 3] <= g[q + 3] + eps for bs in boxes.values() for b in bs for q in range(3))

        # every cell centre lies in exactly one block (sub-boxes of one block may overlap)
        rest = res["at"]["idle0"]["boxes"]
        for i in range(3):
            for j in range(3):
                for k in range(3):
                    p = [-half + 0.54 * (n + 0.5) for n in (i, j, k)]
                    owners = {name for name, bs in rest.items() for b in bs if all(b[q] < p[q] < b[q + 3] for q in range(3))}
                    self.assertEqual(len(owners), 1, (i, j, k, owners))
        # at rest and after the pass the blocks are inside the glass box
        for k in ("idle0", "pass1.2", "pass2", "pass2.9", "pass4.2", "run0.15"):
            self.assertTrue(inside(res["at"][k]["boxes"]), k)
        # pass: the last block is still coming in at first, then it is all
        # seated, the lid shut, and the blocks light up once
        self.assertFalse(inside(res["at"]["pass0.1"]["boxes"]))
        self.assertEqual(len(res["at"]["pass1.2"]["lit"]), 7)
        self.assertEqual(res["at"]["pass2.9"]["lit"], [])
        self.assertGreater(res["lidOff"]["pass0.2"], 1.0)
        self.assertAlmostEqual(res["lidOff"]["pass1.2"], 0, places=6)
        self.assertAlmostEqual(res["lidOff"]["idle0"], 0, places=6)
        # working: the lid is open and one top block at a time is up out of the box
        self.assertGreater(res["lidOff"]["working0.75"], 1.0)
        ups = []
        for k in ("working0.75", "working2.25", "working3.75"):
            b = res["at"][k]["boxes"]
            up = [n for n, bs in b.items() if any(x[4] > g[4] + 0.2 for x in bs)]
            self.assertEqual(len(up), 1, (k, up))
            ups.append(up[0])
        self.assertEqual(len(set(ups)), 3, ups)
        # mismatch: one block sticks out of the box and is red
        mb = res["at"]["mismatch1.2"]
        out = [n for n, bs in mb["boxes"].items() if any(x[4] > g[4] + 0.2 for x in bs)]
        self.assertEqual(out, ["t-top"])
        self.assertEqual(mb["red"], ["t-top"])
        self.assertGreater(res["lidOff"]["mismatch1.2"], 0.5)
        # the v7 bodies by packagerStyle
        st = res["styles"]
        self.assertEqual(st["burr"], {"style": "burr", "bars": 6, "rings": 0, "blocks": 0})
        self.assertEqual(st["rings"], {"style": "rings", "bars": 0, "rings": 3, "blocks": 0})

    def test_run(self):
        # every pet runs: the feet swing in turn, the body bounces and leans
        # forward; across moves it to the right; handling turns its wheels
        from playwright.sync_api import sync_playwright

        page_url = (PETS / "viewer.html").as_uri() + "?view=grid&t=0"
        js = """async () => {
          const THREE = await import('three');
          const HP = window.HirakuPets;
          const T = 0.6;
          const feet = (p) => {
            const z = [];
            p.group.updateMatrixWorld(true);
            p.group.traverse((o) => {
              const g = o.geometry;
              if (o.isMesh && g && g.type === 'SphereGeometry' && g.parameters.radius === 1 && g.parameters.widthSegments === 8)
                z.push(o.getWorldPosition(new THREE.Vector3()).toArray());
            });
            return z;
          };
          const tip = (p) => {
            // the antenna tip: the icosahedron of radius 0.15
            let v = null;
            p.group.updateMatrixWorld(true);
            p.group.traverse((o) => { if (o.isMesh && o.geometry.type === 'IcosahedronGeometry' && o.geometry.parameters.radius === 0.15) v = o.getWorldPosition(new THREE.Vector3()).toArray(); });
            return v;
          };
          const out = {};
          for (const r of HP.ROLES) {
            const p = HP.create(THREE, { role: r.id });
            p.update(0, 'idle'); const idleTip = tip(p);
            p.update(0, 'run'); const f0 = feet(p), tip0 = tip(p);
            p.update(T / 4, 'run'); const f1 = feet(p), tip1 = tip(p);
            p.update(3 * T / 4, 'run'); const f3 = feet(p);
            p.update(T / 4 + 0.3, 'run', null, { across: true }); const a1 = feet(p);
            p.update(T / 4 + 1.5, 'run', null, { across: true }); const a2 = feet(p);
            out[r.id] = { f0, f1, f3, idleTip, tip0, tip1, a1, a2 };
            p.dispose();
          }
          const q = HP.create(THREE, { role: 'packager', run: 'across' });
          q.update(0.3, 'run'); const b1 = feet(q);
          q.update(1.5, 'run'); const b2 = feet(q);
          q.dispose();
          // handling: the wheels turn
          const h = HP.create(THREE, { role: 'cae.handling' });
          const rot = () => { const a = []; h.group.updateMatrixWorld(true); h.group.traverse((o) => { if (o.isMesh && o.geometry.type === 'CylinderGeometry' && Math.abs(o.geometry.parameters.radiusTop - 0.39) < 1e-9) a.push(o.getWorldQuaternion(new THREE.Quaternion()).toArray()); }); return a; };
          h.update(0.1, 'idle'); const w0 = rot();
          h.update(0.1, 'run'); const w1 = rot();
          h.dispose();
          return { out, opt: [b1, b2], wheels: [w0, w1], states: HP.STATES };
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 400, "height": 300})
                self.open(page, page_url)
                res = page.evaluate(js)
            finally:
                browser.close()
        self.assertIn("run", res["states"])
        for rid, v in res["out"].items():
            self.assertEqual(len(v["f0"]), 2, rid)
            # t = 0: legs straight down, feet level with each other
            self.assertAlmostEqual(v["f0"][0][2], v["f0"][1][2], places=5, msg=rid)
            # a quarter stride: one foot forward, one back; half a stride later the other way
            d1 = v["f1"][0][2] - v["f1"][1][2]
            d3 = v["f3"][0][2] - v["f3"][1][2]
            self.assertGreater(abs(d1), 0.4, rid)
            self.assertLess(d1 * d3, 0, rid)
            # the body is up in the air with the legs spread
            self.assertGreater(v["tip1"][1] - v["tip0"][1], 0.05, rid)
            # leaning forward (+z) compared with standing
            self.assertGreater(v["tip0"][2] - v["idleTip"][2], 0.15, rid)
            # in place: no sideways move; across: moves right 2.4 per second
            # (compared two strides apart, 1.2 s)
            self.assertAlmostEqual(v["tip0"][0], v["idleTip"][0], delta=0.05, msg=rid)
            mid = lambda f: (f[0][0] + f[1][0]) / 2
            self.assertAlmostEqual(mid(v["a2"]) - mid(v["a1"]), 2.88, delta=0.01, msg=rid)
        b1, b2 = res["opt"]
        self.assertAlmostEqual((b2[0][0] + b2[1][0]) / 2 - (b1[0][0] + b1[1][0]) / 2, 2.88, delta=0.01, msg="opts.run = across")
        w0, w1 = res["wheels"]
        self.assertEqual(len(w0), 2)
        self.assertNotEqual(w0, w1, "the wheels turn when running")

    def test_v9_waiting_blocked_lite(self):
        # waiting / blocked: last in STATES, poses from t (and o) only, their
        # marks, o.mark = false; lite: fewer triangles, calls and shadow
        # casters; without lite the drawn meshes are the v8 ones
        from playwright.sync_api import sync_playwright

        page_url = (PETS / "viewer.html").as_uri() + "?view=grid&t=0"
        js = """async () => {
          const THREE = await import('three');
          const HP = window.HirakuPets;
          const V = (o) => o.getWorldPosition(new THREE.Vector3()).toArray();
          const pose = (p) => {
            const a = [];
            p.group.updateMatrixWorld(true);
            p.group.traverseVisible((o) => { if (o.isMesh) a.push(...o.matrixWorld.elements.map((x) => Math.round(x * 1e6))); });
            return a.join(',');
          };
          const tip = (p) => {
            let v = null;
            p.group.updateMatrixWorld(true);
            p.group.traverse((o) => { if (o.isMesh && o.geometry.type === 'IcosahedronGeometry' && o.geometry.parameters.radius === 0.15) v = V(o); });
            return v;
          };
          const marks = (p) => {
            const r = { stop: 0, dots: 0, lit: 0, checks: 0 };
            p.group.traverseVisible((o) => {
              if (!o.isMesh || !o.material || !o.material.color) return;
              const g = o.geometry, hex = o.material.color.getHex();
              if (g.type === 'CylinderGeometry' && g.parameters.radiusTop === 0.27) {
                if (hex === 0xe0472a) r.stop++; else r.checks++;
              }
              if (g.type === 'SphereGeometry' && g.parameters.radius === 0.048) { r.dots++; if (hex === 0x1b1a17) r.lit++; }
            });
            return r;
          };
          const hopZ = (p) => p.group.children[0].children[0].position.z;
          const out = { states: HP.STATES, roles: {} };
          for (const r of HP.ROLES) {
            const a = HP.create(THREE, { role: r.id }), b = HP.create(THREE, { role: r.id });
            const o = {};
            for (const s of ['waiting', 'blocked']) {
              // the same t gives the same pose, whatever came before
              a.update(0.7, 'pass'); a.update(9.1, s); a.update(2.35, s);
              b.update(2.35, s);
              o[s + 'Same'] = pose(a) === pose(b);
            }
            // waiting: no sudden move of the antenna tip over 16 s at 30 frames/s
            // (compared with the same pet's idle; handling's body rolls by itself)
            const maxStep = (s) => {
              let prev = null, step = 0;
              for (let k = 0; k <= 480; k++) {
                a.update(k / 30, s);
                const v = tip(a);
                if (prev) step = Math.max(step, Math.hypot(v[0] - prev[0], v[1] - prev[1], v[2] - prev[2]));
                prev = v;
              }
              return step;
            };
            o.waitStep = maxStep('waiting');
            o.idleStep = maxStep('idle');
            // blocked: flinch at u = BLOCK.jolt, frozen later (with o.since and with t mod 3)
            a.update(10 + HP.BLOCK.jolt, 'blocked', null, { since: 10 }); o.backSince = hopZ(a);
            a.update(3 + HP.BLOCK.jolt, 'blocked'); o.backMod = hopZ(a);
            a.update(11.0, 'blocked', null, { since: 10 }); const f1 = pose(a);
            a.update(12.5, 'blocked', null, { since: 10 }); const f2 = pose(a);
            o.frozen = f1 === f2;
            a.update(10.0, 'blocked', null, { since: 10 }); const tip0 = tip(a);
            a.update(10.1, 'blocked', null, { since: 10 }); const tip1 = tip(a);
            o.flinch = Math.hypot(tip1[0] - tip0[0], tip1[1] - tip0[1], tip1[2] - tip0[2]);
            a.update(10.0, 'idle'); o.idleBack = hopZ(a);
            // marks
            a.update(0.45 * 2 + 0.01, 'waiting'); o.wait2 = marks(a);
            a.update(0.45 * 3 + 0.01, 'waiting'); o.wait3 = marks(a);
            a.update(0.45 * 4 + 0.01, 'waiting'); o.wait4 = marks(a);
            a.update(1.0, 'blocked'); o.stop = marks(a);
            a.update(1.0, 'blocked', null, { mark: false }); o.stopHidden = marks(a);
            a.update(1.0, 'waiting', null, { mark: false }); o.waitHidden = marks(a);
            a.update(0.3, 'pass', null, { mark: false }); o.passHidden = marks(a);
            a.update(0.3, 'pass'); o.pass = marks(a);
            a.update(1.0, 'idle'); o.idle = marks(a);
            // cost: full (v8 fingerprint), lite, and lite switched on and off
            for (const p of [a, b]) { p.setPixelScale(13.6); p.update(1.0, 'idle'); }
            o.full = a.cost();
            const l = HP.create(THREE, { role: r.id, lite: true });
            l.setPixelScale(13.6); l.update(1.0, 'idle');
            o.lite = l.cost();
            b.setOptions({ lite: true }); b.update(1.0, 'idle'); o.switched = b.cost();
            b.setOptions({ lite: false }); b.update(1.0, 'idle'); o.back = b.cost();
            o.sameGroup = b.group.children.length === 1;
            for (const p of [a, b, l]) p.dispose();
            out.roles[r.id] = o;
          }
          return out;
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 400, "height": 300})
                self.open(page, page_url)
                res = page.evaluate(js)
            finally:
                browser.close()
        self.assertEqual(res["states"][-2:], ["waiting", "blocked"])
        # v8: drawn meshes (calls) and triangles at idle t = 1.0 (13.6 px a unit)
        v8 = {"lead": (31, 7886), "designer": (31, 11426), "packager": (56, 16168), "cae.handling": (108, 23558),
              "cae.powertrain": (47, 11546), "cae.aero": (35, 25162), "cae.crash": (36, 12886),
              "cae.strength": (32, 11146), "cae.nvh": (34, 9818), "cae.thermal": (34, 13650)}
        # v10: the module designers cost no more than the designer (with and without lite)
        des = res["roles"]["designer"]
        mods = [r for r in res["roles"] if r not in v8]
        self.assertEqual(len(mods), 6)
        for rid in mods:
            for k in ("full", "lite"):
                self.assertLessEqual(res["roles"][rid][k]["calls"], des[k]["calls"], (rid, k))
                self.assertLessEqual(res["roles"][rid][k]["triangles"], des[k]["triangles"], (rid, k))
        for rid, o in res["roles"].items():
            self.assertTrue(o["waitingSame"], rid)
            self.assertTrue(o["blockedSame"], rid)
            self.assertLess(o["waitStep"], o["idleStep"] + 0.012, (rid, o["waitStep"], o["idleStep"]))
            self.assertAlmostEqual(o["backSince"], -0.13, places=6, msg=rid)
            self.assertAlmostEqual(o["backMod"], -0.13, places=6, msg=rid)
            self.assertEqual(o["idleBack"], 0, rid)
            self.assertTrue(o["frozen"], rid)
            self.assertGreater(o["flinch"], 4 * o["waitStep"], rid)
            self.assertEqual((o["wait2"]["dots"], o["wait2"]["lit"]), (3, 2), rid)
            self.assertEqual((o["wait3"]["dots"], o["wait3"]["lit"]), (3, 3), rid)
            self.assertEqual((o["wait4"]["dots"], o["wait4"]["lit"]), (3, 0), rid)
            self.assertEqual(o["wait3"]["stop"], 0, rid)
            self.assertEqual((o["stop"]["stop"], o["stop"]["dots"]), (1, 0), rid)
            for k in ("stopHidden", "waitHidden", "passHidden", "idle"):
                self.assertEqual(o[k], {"stop": 0, "dots": 0, "lit": 0, "checks": 0}, (rid, k))
            self.assertEqual(o["pass"]["checks"], 1, rid)
            full, lite = o["full"], o["lite"]
            if rid in v8:
                self.assertEqual((full["calls"], full["triangles"]), v8[rid], rid)
            self.assertFalse(full["lite"])
            self.assertTrue(lite["lite"])
            self.assertLess(lite["triangles"], 0.7 * full["triangles"], rid)
            self.assertLess(lite["calls"], full["calls"], rid)
            self.assertLess(lite["shadowCalls"], full["shadowCalls"], rid)
            self.assertLess(lite["shadowTriangles"], full["shadowTriangles"], rid)
            self.assertEqual(o["switched"], lite, rid)
            self.assertEqual(o["back"], full, rid)
            self.assertTrue(o["sameGroup"], rid)
        tot = lambda k, f: sum(o[k][f] for o in res["roles"].values())
        self.assertLess(tot("lite", "triangles"), 0.5 * tot("full", "triangles"))

    def test_anim_hop(self):
        # anim.hop: still before it starts; a crouch (squashed, on the ground);
        # a parabola in time (second differences the same), stretched while
        # fast; a squash on landing; small bounces; at rest at end
        from playwright.sync_api import sync_playwright

        page_url = (PETS / "viewer.html").as_uri() + "?view=one&role=lead&t=0"
        js = """() => {
          const A = window.HirakuPets.anim;
          const J = { crouch_s: 0.2, crouch: 0.2, air_s: 0.5, rise_px: 300, over_px: 40, stretch: 0.15,
                      land_sq: 0.3, land_s: 0.12, bounces: [[10, 0.16], [4, 0.1]], settle_s: 0.3,
                      lean_k: 0.8, antenna_k: 2, antenna_lag_s: 0.06 };
          const at = (x) => A.hop(x, J);
          const air = [];
          for (let i = 0; i <= 10; i++) air.push(at(0.2 + 0.05 * i).y);
          const end = at(0).end;
          return { before: at(-0.1), crouch: at(0.19), mid: at(0.2 + 0.25), land: at(0.7 + 0.04), air,
                   bounce: at(0.7 + 0.12 + 0.08), after: at(end + 0.01), end, landT: at(0).land,
                   back: [A.back(0, 0.2), A.back(1, 0.2), Math.max(...Array.from({ length: 99 }, (_, i) => A.back((i + 1) / 100, 0.2)))],
                   flat: A.hop(0.5, { air_s: 0.4, over_px: 20 }) };
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 400, "height": 300})
                self.open(page, page_url)
                r = page.evaluate(js)
            finally:
                browser.close()
        self.assertEqual((r["before"]["y"], r["before"]["squash"]), (0, 0))
        self.assertEqual(r["crouch"]["y"], 0)                                    # crouching on the ground
        self.assertGreater(r["crouch"]["squash"], 0.15)                          # squashed down (anticipation)
        self.assertGreater(r["crouch"]["lean"], 0)
        self.assertLess(r["mid"]["squash"], 0)                                   # stretched in the air
        self.assertTrue(r["mid"]["air"])
        self.assertAlmostEqual(r["landT"], 0.7)
        y = r["air"]
        d2 = [y[i + 1] - 2 * y[i] + y[i - 1] for i in range(1, len(y) - 1)]
        for v in d2:
            self.assertAlmostEqual(v, d2[0], places=6)                           # a parabola in time
        self.assertLess(d2[0], 0)
        self.assertAlmostEqual(y[-1], 300, places=6)                             # lands rise_px up
        self.assertAlmostEqual(max(y), 340, delta=2)                             # topping out over_px above
        self.assertGreater(r["land"]["squash"], 0.2)                             # squashed on landing
        self.assertGreater(r["bounce"]["bounce"], 0)                             # a small bounce after
        self.assertLess(r["bounce"]["bounce"], 10 + 1e-9)
        self.assertAlmostEqual(r["end"], 0.2 + 0.5 + 0.12 + 0.16 + 0.12 * (0.5 + 0.5 * 0.5) + 0.1
                               + 0.12 * (0.5 + 0.5 * (4 / 40) ** 0.5) + 0.3, places=6)
        a = r["after"]
        self.assertEqual((a["y"], a["squash"], a["bounce"], a["lean"]), (300, 0, 0, 0))   # at rest at the end
        self.assertEqual(r["back"][:2], [0, 1])
        self.assertAlmostEqual(r["back"][2], 1.2, delta=0.002)                   # overshoots by over
        self.assertAlmostEqual(r["flat"]["y"], 0)                                # no crouch, no air given: still

    def test_pose_terms(self):
        # update(t, state, camera, o): without o (or with o = {}) the pose is the
        # state's; o.squash squashes the torso, o.check shows the tick
        from playwright.sync_api import sync_playwright

        page_url = (PETS / "viewer.html").as_uri() + "?view=one&role=lead&t=0"
        js = """async () => {
          const THREE = await import('three');
          const HP = window.HirakuPets;
          const p = HP.create(THREE, { role: 'cae.nvh' });
          const snap = () => { const a = []; p.group.updateMatrixWorld(true);
            p.group.traverse((o) => a.push(o.matrixWorld.elements.map((v) => v.toFixed(6)).join(','), o.visible)); return a.join(';'); };
          const torsoTop = () => { let y = -1e9; p.group.updateMatrixWorld(true);
            p.group.traverse((o) => { if (o.isMesh && o.geometry.type === 'IcosahedronGeometry' && o.geometry.parameters.radius === 0.15) y = o.getWorldPosition(new THREE.Vector3()).y; }); return y; };
          p.update(1.3, 'idle'); const a = snap(), topA = torsoTop();
          p.update(1.3, 'idle', null, {}); const b = snap();
          p.update(1.3, 'report'); const c = snap();
          p.update(1.3, 'idle', null, { report: 1 }); const tiltR = snap();
          p.update(1.3, 'idle', null, { squash: 0.3 }); const topS = torsoTop();
          p.update(1.3, 'idle', null, { squash: -0.15 }); const topT = torsoTop();
          p.update(1.3, 'idle', null, { check: true });
          const vis = []; p.group.traverse((o) => vis.push(o.visible));
          p.update(1.3, 'idle'); const vis0 = []; p.group.traverse((o) => vis0.push(o.visible));
          p.dispose();
          return { same: a === b, reportDiffers: a !== c, reportMix: tiltR !== a, topA, topS, topT,
                   tickShown: vis.filter(Boolean).length > vis0.filter(Boolean).length };
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 400, "height": 300})
                self.open(page, page_url)
                r = page.evaluate(js)
            finally:
                browser.close()
        self.assertTrue(r["same"])                     # o = {} changes nothing
        self.assertTrue(r["reportDiffers"])
        self.assertTrue(r["reportMix"])
        self.assertLess(r["topS"], r["topA"] - 0.1)    # squashed: the antenna tip comes down
        self.assertGreater(r["topT"], r["topA"] + 0.05)  # stretched: it goes up
        self.assertTrue(r["tickShown"])

    def test_free_rotation_and_url(self):
        # drag in the one-pet view up to a view from straight above, then from
        # straight below; the URL the viewer writes gives the same picture
        from playwright.sync_api import sync_playwright

        base = (PETS / "viewer.html").as_uri() + "?view=one&role=cae.crash&state=idle&cam=front&t=1.2&ui=0"
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                for dy, want in ((158, 1), (-182, -1)):
                    page = browser.new_page(viewport={"width": 640, "height": 680})
                    self.open(page, base)
                    x0, y0 = 357, 340
                    page.mouse.move(x0, y0)
                    page.mouse.down()
                    for k in range(1, 13):
                        page.mouse.move(x0, y0 + dy * k / 12)
                        page.wait_for_timeout(40)
                    page.mouse.up()
                    page.wait_for_timeout(600)
                    orbit = page.evaluate("window.petViewer.state.orbit")
                    href = page.evaluate("location.href")
                    shot = page.screenshot()
                    page.close()
                    el = math.degrees(math.asin(orbit[1] / math.hypot(*orbit[:3])))
                    # beyond the 90 deg limit an orbit control would have
                    self.assertGreater(el * want, 80, orbit)
                    self.assertIn("orbit=", href)
                    page = browser.new_page(viewport={"width": 640, "height": 680})
                    self.open(page, href)
                    page.wait_for_timeout(300)
                    self.assertEqual(page.evaluate("window.petViewer.state.orbit"), orbit)
                    self.assertEqual(page.screenshot(), shot, "reopened URL gives the same picture")
                    page.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
