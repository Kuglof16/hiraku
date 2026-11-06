"""A picture of a case: the opaque vehicle and streamlines of the real mean field, as thin tubes coloured by speed, on a
dark background. The streamlines come from hiraku.cfd.streamlines; nothing is drawn that the field does not hold.

Two parts in one file. In plain Python the driver computes the scene (meshes as numpy arrays) and starts Blender as a
separate process in background mode with factory settings, so no open Blender scene is touched:

    python -m hiraku.cfd.render_blender <case folder> --design <design.json> --out <picture.png>

Inside Blender the same file builds that scene and renders it. Blender runs on the CPU here (Cycles), with --threads
threads. Blender is found by --blender, the HIRAKU_BLENDER variable, PATH, or the installer's usual folder.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

try:
    import bpy  # only inside Blender
except ImportError:
    bpy = None

MATERIALS = {   # category -> base colour (linear), metallic, roughness
    "body": ((0.56, 0.58, 0.60), 0.0, 0.42),
    "frame": ((0.13, 0.14, 0.16), 0.75, 0.42),
    "wheels": ((0.018, 0.018, 0.02), 0.0, 0.72),
    "occupants": ((0.30, 0.31, 0.34), 0.0, 0.75),
    "cargo": ((0.20, 0.25, 0.32), 0.0, 0.6),
    "other": ((0.20, 0.205, 0.22), 0.35, 0.5),
    "nets": ((0.45, 0.46, 0.48), 0.8, 0.45),
}
BODY_KINDS = ("body", "fender", "rollbar")
MM = 1e-3


# ---------------------------------------------------------------- colour

STOPS = np.array([[0x2b, 0x59, 0xc3], [0x2f, 0xa7, 0xd6], [0x4c, 0xbf, 0x7a], [0xe8, 0xc5, 0x47], [0xe9, 0x89, 0x3a]]) / 255.0  # the viewer's contour colours (js/core.js): no red, red is for NG


def ramp(x: Any) -> np.ndarray:
    """The viewer's contour colours, continuous; display values 0..1."""
    x = np.clip(np.asarray(x, float), 0.0, 1.0) * (len(STOPS) - 1)
    i = np.minimum(x.astype(np.int64), len(STOPS) - 2)
    f = (x - i)[..., None]
    return STOPS[i] * (1.0 - f) + STOPS[i + 1] * f


def to_linear(c: np.ndarray) -> np.ndarray:
    """Display (sRGB) values to the linear values Blender renders in; with the Standard view they come back unchanged."""
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def nice_scale(v: float, ticks: int = 4) -> tuple[float, float]:
    """(top, tick step) of a colour bar reaching v: a round step, about `ticks` of them."""
    raw = v / ticks
    power = 10.0 ** math.floor(math.log10(raw))
    step = next(k * power for k in (1.0, 2.0, 2.5, 4.0, 5.0, 10.0) if k * power >= raw * 0.999)
    return math.ceil(v / step - 1e-9) * step, step


# ---------------------------------------------------------------- meshes (plain numpy)

class MeshBuilder:
    """Vertices and polygons of one material; polygons of any size."""

    def __init__(self) -> None:
        self.verts: list[np.ndarray] = []
        self.loops: list[np.ndarray] = []
        self.starts: list[np.ndarray] = []
        self.colours: list[np.ndarray] = []
        self.nv = self.nl = 0

    def add(self, verts: np.ndarray, faces: list[np.ndarray] | np.ndarray, colours: np.ndarray | None = None) -> None:
        if isinstance(faces, np.ndarray):  # (nf, k) of one size
            starts = self.nl + np.arange(len(faces)) * faces.shape[1]
            loops = faces.ravel() + self.nv
        else:
            sizes = np.array([len(f) for f in faces])
            starts = self.nl + np.concatenate([[0], np.cumsum(sizes)[:-1]])
            loops = np.concatenate(faces) + self.nv
        self.verts.append(verts)
        self.loops.append(loops)
        self.starts.append(starts)
        if colours is not None:
            self.colours.append(colours)
        self.nv += len(verts)
        self.nl += len(loops)

    def arrays(self, name: str) -> dict[str, np.ndarray]:
        out = {f"{name}_v": np.concatenate(self.verts).astype(np.float32), f"{name}_l": np.concatenate(self.loops).astype(np.int32), f"{name}_s": np.concatenate(self.starts).astype(np.int32)}
        if self.colours:
            out[f"{name}_c"] = np.concatenate(self.colours).astype(np.float32)
        return out


def polytope_mesh(s: Any) -> tuple[np.ndarray, list[np.ndarray]]:
    """The faces of a convex shape from its planes and vertices, each counter-clockwise seen from outside."""
    v = s.vertices
    faces = []
    for n, off in zip(s.normals, s.offsets):
        on = np.nonzero(np.abs(v @ n - off) < 1e-7)[0]
        if len(on) < 3:
            continue
        r = v[on] - v[on].mean(axis=0)
        e1 = r[0] / np.linalg.norm(r[0])
        e2 = np.cross(n, e1)
        faces.append(on[np.argsort(np.arctan2(r @ e2, r @ e1))])
    return v, faces


def cylinder_mesh(c: Any, sides: int) -> tuple[np.ndarray, list[np.ndarray]]:
    a = c.axis
    ref = np.array([0.0, 0.0, 1.0]) if abs(a[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = np.cross(a, ref)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(a, e1)
    th = 2.0 * np.pi * np.arange(sides) / sides
    ring = c.radius * (np.outer(np.cos(th), e1) + np.outer(np.sin(th), e2))
    verts = np.vstack([c.center - c.half * a + ring, c.center + c.half * a + ring])
    i = np.arange(sides)
    j = (i + 1) % sides
    faces = list(np.column_stack([i, j, j + sides, i + sides])) + [i[::-1], i + sides]
    return verts, faces


def net_mesh(s: Any, wire: float, pitch: float) -> tuple[np.ndarray, np.ndarray]:
    """A side net as its wires: two sets of square bars at the design's pitch, in the box's plane. s: a box from
    geometry.box, whose vertices run (x-, y-, z-), (x-, y-, z+), (x-, y+, z-), ... in its own axes."""
    v = s.vertices
    edges = [v[4] - v[0], v[2] - v[0], v[1] - v[0]]
    lengths = [float(np.linalg.norm(e)) for e in edges]
    thin = int(np.argmin(lengths))
    a, b = [k for k in range(3) if k != thin]
    u, w, t = edges[a] / lengths[a], edges[b] / lengths[b], edges[thin] / lengths[thin]
    corner = v[0] + 0.5 * edges[thin]
    verts, faces = [], []
    h = 0.5 * wire

    def bar(p: np.ndarray, d: np.ndarray, length: float) -> None:
        side = np.cross(d, t)
        side = side / np.linalg.norm(side)
        q = np.array([p + sx * h * side + st * h * t for sx, st in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
        n = len(verts) * 8
        verts.append(np.vstack([q, q + length * d]))
        for i in range(4):
            j = (i + 1) % 4
            faces.append([n + i, n + j, n + j + 4, n + i + 4])

    for k in range(int(lengths[a] / pitch) + 1):
        bar(corner + k * pitch * u, w, lengths[b])
    for k in range(int(lengths[b] / pitch) + 1):
        bar(corner + k * pitch * w, u, lengths[a])
    return np.vstack(verts), np.array(faces)


def tube_mesh(points: np.ndarray, radius: float, sides: int) -> tuple[np.ndarray, np.ndarray]:
    """A tube along a polyline: rings of `sides` vertices (n x sides x 3) and quads."""
    n = len(points)
    t = np.gradient(points, axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    ref = np.tile((0.0, 0.0, 1.0), (n, 1))
    ref[np.abs(t[:, 2]) > 0.9] = (0.0, 1.0, 0.0)
    e1 = np.cross(t, ref)
    e1 /= np.maximum(np.linalg.norm(e1, axis=1, keepdims=True), 1e-12)
    e2 = np.cross(t, e1)
    th = 2.0 * np.pi * np.arange(sides) / sides
    rings = points[:, None, :] + radius * (e1[:, None, :] * np.cos(th)[None, :, None] + e2[:, None, :] * np.sin(th)[None, :, None])
    a = np.arange(n - 1)[:, None] * sides + np.arange(sides)
    b = np.arange(n - 1)[:, None] * sides + (np.arange(sides) + 1) % sides
    return rings.reshape(-1, 3), np.stack([a, b, b + sides, a + sides], axis=2).reshape(-1, 4)


def category_of(shape: Any, kinds: dict[str, str]) -> str:
    if shape.reflect < 1.0:
        return "nets"
    if shape.group == "wheels":
        return "wheels"
    if kinds.get(shape.name) in BODY_KINDS:
        return "body"
    if shape.group == "frame":
        return "frame"
    if shape.group in ("occupants", "cargo"):
        return shape.group
    return "other"


def vehicle_meshes(design: dict[str, Any], shapes: list[Any]) -> dict[str, MeshBuilder]:
    kinds = {p["id"]: p.get("kind", "") for p in design.get("parts", [])}
    variables = design.get("variables") or {}
    wire = (variables.get("side_net_wire_d") or {}).get("value", 3.0) * MM
    pitch = (variables.get("side_net_pitch") or {}).get("value", 50.0) * MM
    out: dict[str, MeshBuilder] = {}
    for s in shapes:
        cat = category_of(s, kinds)
        m = out.setdefault(cat, MeshBuilder())
        if cat == "nets":
            m.add(*net_mesh(s, wire, pitch))
        elif hasattr(s, "normals"):
            m.add(*polytope_mesh(s))
        else:
            m.add(*cylinder_mesh(s, 72 if cat == "wheels" else 14))
    return out


def line_meshes(lines: Any, radius: float, sides: int, vmax: float, per_point: int = 1) -> MeshBuilder:
    """Tubes of the streamlines; colour per vertex from the speed there, 0 .. vmax on the contour colours."""
    m = MeshBuilder()
    L = lines.thinned(per_point)
    for n in range(len(L)):
        line = L.line(n)
        if len(line["points"]) < 2:
            continue
        v, f = tube_mesh(line["points"], radius, sides)
        m.add(v, f, np.repeat(to_linear(ramp(line["speed"] / vmax)), sides, axis=0))
    return m


# ---------------------------------------------------------------- inside Blender

def _mesh_object(name: str, verts: np.ndarray, loops: np.ndarray, starts: np.ndarray, colours: np.ndarray | None = None):
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(verts))
    me.vertices.foreach_set("co", verts.ravel())
    me.loops.add(len(loops))
    me.loops.foreach_set("vertex_index", loops)
    me.polygons.add(len(starts))
    me.polygons.foreach_set("loop_start", starts)
    me.update(calc_edges=True)
    if colours is not None:
        attr = me.color_attributes.new("speed", "FLOAT_COLOR", "POINT")
        rgba = np.ones((len(colours), 4), np.float32)
        rgba[:, :3] = colours
        attr.data.foreach_set("color", rgba.ravel())
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _principled(name: str, colour: Any, metallic: float, rough: float):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*colour, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = rough
    return mat


def _emission_by_attribute(name: str, strength: float):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    attr = nodes.new("ShaderNodeVertexColor")
    attr.layer_name = "speed"
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = strength
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(attr.outputs["Color"], emit.inputs["Color"])
    links.new(emit.outputs[0], out.inputs["Surface"])
    return mat


def _area_light(name: str, location: Any, target: Any, size: float, watts: float, colour: Any = (1.0, 1.0, 1.0)):
    light = bpy.data.lights.new(name, "AREA")
    light.size = size
    light.energy = watts
    light.color = colour
    ob = bpy.data.objects.new(name, light)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = location
    ob.visible_glossy = False  # no bright patches of the lamps mirrored in the dark floor
    _aim(ob, target)
    return ob


def _aim(ob: Any, target: Any) -> None:
    from mathutils import Vector

    d = Vector(target) - Vector(ob.location)
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def blender_main(argv: list[str]) -> None:
    scene_file, png = argv[0], argv[1]
    d = np.load(scene_file)
    opt = json.loads(str(d["options"]))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = int(opt["samples"])
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 4
    scene.render.threads_mode = "FIXED"
    scene.render.threads = int(opt["threads"])
    scene.render.resolution_x, scene.render.resolution_y = opt["resolution"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = png
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    world = bpy.data.worlds.new("dark")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (*opt["background"], 1.0)
    world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
    scene.world = world

    for name, (colour, metallic, rough) in MATERIALS.items():
        key = f"mesh_{name}_v"
        if key in d:
            ob = _mesh_object(name, d[key], d[f"mesh_{name}_l"], d[f"mesh_{name}_s"])
            ob.data.materials.append(_principled(name, colour, metallic, rough))
    lines = _mesh_object("streamlines", d["lines_v"], d["lines_l"], d["lines_s"], d["lines_c"])
    lines.data.materials.append(_emission_by_attribute("streamlines", float(opt["line_strength"])))

    ground = opt["ground"]
    gv = np.array([[ground[0], ground[1], 0.0], [ground[2], ground[1], 0.0], [ground[2], ground[3], 0.0], [ground[0], ground[3], 0.0]], np.float32)
    plane = _mesh_object("ground", gv, np.array([0, 1, 2, 3], np.int32), np.array([0], np.int32))
    plane.data.materials.append(_principled("ground", (0.012, 0.013, 0.016), 0.0, 0.32))

    target = opt["target"]
    _area_light("key", (target[0] - 3.0, target[1] - 4.5, 5.0), target, 6.0, 700.0, (1.0, 0.97, 0.93))
    _area_light("fill", (target[0] - 5.5, target[1] + 3.0, 2.5), target, 5.0, 260.0, (0.75, 0.85, 1.0))
    _area_light("rim", (target[0] + 4.5, target[1] - 1.5, 3.5), target, 4.0, 300.0, (0.85, 0.92, 1.0))

    cam = bpy.data.cameras.new("camera")
    cam.lens = float(opt["lens"])
    cam.clip_end = 200.0
    cam_ob = bpy.data.objects.new("camera", cam)
    scene.collection.objects.link(cam_ob)
    cam_ob.location = opt["camera"]
    _aim(cam_ob, target)
    scene.camera = cam_ob
    bpy.context.view_layer.update()
    Path(png).with_suffix(".camera.json").write_text(json.dumps({"matrix_world": [list(r) for r in cam_ob.matrix_world], "lens": cam.lens, "sensor_width": cam.sensor_width, "resolution": opt["resolution"]}), encoding="utf-8")
    bpy.ops.render.render(write_still=True)


# ---------------------------------------------------------------- the driver

def find_blender(given: str | None = None) -> str:
    for c in (given, os.environ.get("HIRAKU_BLENDER"), shutil.which("blender")):
        if c:
            return c
    root = Path(os.environ.get("ProgramFiles", "")) / "Blender Foundation"
    found = sorted(root.glob("Blender */blender.exe"))
    if found:
        return str(found[-1])
    raise FileNotFoundError("Blender is not found: give --blender or set HIRAKU_BLENDER")


def run_blender(blender: str, scene_file: Path, png: Path, log: Path | None = None) -> None:
    """Blender in background mode with factory settings, as its own process; no open scene is touched."""
    cmd = [blender, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(Path(__file__).resolve()), "--", str(scene_file), str(png)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if log:
        log.write_text(r.stdout + r.stderr, encoding="utf-8")
    if r.returncode != 0 or not png.is_file():
        raise RuntimeError(f"Blender failed ({r.returncode}):\n{(r.stdout + r.stderr)[-3000:]}")


def _font(size: int):
    from PIL import ImageFont

    root = Path(os.environ.get("WINDIR", "")) / "Fonts"
    for name in ("YuGothM.ttc", "meiryo.ttc", "msgothic.ttc"):
        if (root / name).is_file():
            return ImageFont.truetype(str(root / name), size)
    return ImageFont.load_default(size)


def caption_overlay(size: tuple[int, int], vmax: float, step: float, lines_text: list[str], status: str) -> Any:
    """The colour bar and the plain words as a see-through layer: what the picture is and how far it has been checked.
    Dark bands under them keep them readable over the lines."""
    from PIL import Image, ImageDraw

    W, H = size
    s = H / 1080.0
    fs, fb = _font(int(26 * s)), _font(int(30 * s))
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    g = ImageDraw.Draw(im)
    width = int(max([g.textlength(t, font=fs) for t in lines_text] + [g.textlength(status, font=fb)]))
    band = (6, 7, 10, 215)
    pad, x_band = int(20 * s), int(28 * s)
    g.rectangle((x_band, int(24 * s), x_band + width + 2 * pad, int(24 * s) + int((38 * len(lines_text) + 60) * s)), fill=band)
    g.rectangle((x_band, H - int(152 * s), x_band + int(460 * s) + 2 * pad, H - int(16 * s)), fill=band)
    x0, y0 = int(48 * s), int(H - 56 * s)
    bw, bh = int(460 * s), int(16 * s)
    for i in range(bw):
        c = tuple(int(round(255 * v)) for v in ramp(i / (bw - 1)))
        g.line([(x0 + i, y0), (x0 + i, y0 + bh)], fill=c)
    for k in range(int(round(vmax / step)) + 1):
        v = k * step
        x = x0 + int((bw - 1) * v / vmax)
        g.line([(x, y0 - int(6 * s)), (x, y0)], fill=(225, 228, 232), width=max(1, int(2 * s)))
        text = f"{v:g}"
        tw = g.textlength(text, font=fs)
        g.text((x - tw / 2, y0 - int(40 * s)), text, font=fs, fill=(225, 228, 232))
    g.text((x0, y0 - int(82 * s)), "空気の速さ [m/s]", font=fs, fill=(225, 228, 232))
    y = int(40 * s)
    for t in lines_text:
        g.text((x0, y), t, font=fs, fill=(225, 228, 232))
        y += int(38 * s)
    g.text((x0, y), status, font=fb, fill=(255, 214, 120))
    return im


def caption_picture(png: Path, vmax: float, step: float, lines_text: list[str], status: str) -> None:
    from PIL import Image

    base = Image.open(png).convert("RGBA")
    Image.alpha_composite(base, caption_overlay(base.size, vmax, step, lines_text, status)).convert("RGB").save(png)


def near_side_of(lines: Any, lo: np.ndarray, hi: np.ndarray, gap: float = 0.15) -> np.ndarray:
    """The lines that pass between a camera on the y- side and the vehicle: below its top, beside it, off its y- flank.
    Whole lines are left out, none is cut or moved, so what remains is still only real streamlines."""
    p = lines.points
    between = (p[:, 0] > lo[0] - 0.8) & (p[:, 0] < hi[0] + 0.8) & (p[:, 1] < lo[1] - gap) & (p[:, 2] < hi[2])
    return np.add.reduceat(between.astype(np.int64), lines.offsets[:-1]) > 0 if len(lines) else np.zeros(0, bool)


def scene_arrays(design: dict[str, Any], summary: dict[str, Any], lines: Any, vmax: float, radius: float, per_point: int, shapes: list[Any]) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    for cat, m in vehicle_meshes(design, shapes).items():
        arrays.update(m.arrays(f"mesh_{cat}"))
    arrays.update(line_meshes(lines, radius, 6, vmax, per_point).arrays("lines"))
    return arrays


def add_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("case", type=Path)
    ap.add_argument("--design", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--blender")
    ap.add_argument("--resolution", type=int, nargs=2, default=(1920, 1080))
    ap.add_argument("--samples", type=int, default=48)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--n", type=int, nargs=2, default=(26, 18), help="seeds across and up the upstream plane")
    ap.add_argument("--across", type=float, nargs=2, default=(-0.3, 1.2), help="the plane's range in grid y, m (the camera looks from y-, so the near side stays clear)")
    ap.add_argument("--up", type=float, nargs=2, default=(0.08, 2.3), help="the plane's range in z, m")
    ap.add_argument("--radius", type=float, default=0.005, help="tube radius, m")
    ap.add_argument("--downstream", type=float, default=7.0, help="lines end this far behind the moment centre, m")
    ap.add_argument("--camera", type=float, nargs=3, default=(-3.6, -7.2, 2.3))
    ap.add_argument("--target", type=float, nargs=3, default=(1.1, 0.0, 0.8))
    ap.add_argument("--lens", type=float, default=42.0)
    ap.add_argument("--status", default="精度は確かめていない")
    ap.add_argument("--keep-scene", action="store_true")


def prepare(a: argparse.Namespace) -> dict[str, Any]:
    """The design, its shapes and the streamlines of the picture."""
    from hiraku.cfd.streamlines import MeanField, ShapeStop, plane_seeds, trace
    from hiraku.cfd.vehicle import design_shapes

    t0 = time.perf_counter()
    summary = json.loads((a.case / "case.json").read_text(encoding="utf-8"))
    design = json.loads(a.design.read_text(encoding="utf-8"))
    shapes, _ = design_shapes(design, summary["yaw_deg"], summary["speed_ms"], u_lattice=summary["numerics"]["u_lattice"], dx=summary["dx"])
    field = MeanField.from_case(a.case)
    stop = ShapeStop(shapes)
    lo, hi = stop.box_lo, stop.box_hi
    x_plane = lo[0] - 1.6
    seeds = plane_seeds((x_plane, a.across[0], a.up[0]), (0.0, a.across[1] - a.across[0], 0.0), (0.0, 0.0, a.up[1] - a.up[0]), a.n[0], a.n[1])
    region = (field.lo, (a.downstream, field.hi[1], field.hi[2]))
    lines = trace(field, seeds, stop=stop, region=region, max_length_m=25.0)
    lines = lines.select(~near_side_of(lines, lo, hi))
    vmax, tick = nice_scale(float(np.percentile(lines.speed, 99.8)))
    print(f"{len(lines)} lines, {len(lines.points)} points, ends {lines.stop_counts()}, speed {lines.speed.min():.2f}..{lines.speed.max():.2f} m/s, colour top {vmax:g} m/s, {time.perf_counter() - t0:.1f} s")
    text = [f"v{summary['design_version']}  {summary['speed_ms'] * 3.6:.0f} km/h  横風の角度 {summary['yaw_deg']:g}°  格子 {summary['dx'] * 1000:.0f} mm",
            "計算した平均の流れ場から求めた流線。色は速さ。"]
    return {"summary": summary, "design": design, "shapes": shapes, "field": field, "lines": lines, "vmax": vmax, "tick": tick, "text": text}


def render(a: argparse.Namespace, p: dict[str, Any], png: Path, line_strength: float = 1.0) -> None:
    """The still picture in Blender, without its caption; beside it <png stem>.camera.json, the camera Blender used."""
    per_point = max(1, int(round(0.06 / (p["field"].dx / 4.0))))
    arrays = scene_arrays(p["design"], p["summary"], p["lines"], p["vmax"], a.radius, per_point, p["shapes"])
    options = {"resolution": list(a.resolution), "samples": a.samples, "threads": a.threads, "background": [0.0035, 0.004, 0.0065], "line_strength": line_strength, "camera": list(a.camera),
               "target": list(a.target), "lens": a.lens, "ground": [-12.0, -12.0, 16.0, 12.0]}
    png.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=png.parent) as tmp:
        scene_file = Path(tmp) / "scene.npz"
        np.savez(scene_file, options=json.dumps(options), **arrays)
        if a.keep_scene:
            shutil.copy(scene_file, png.with_suffix(".scene.npz"))
        run_blender(find_blender(a.blender), scene_file, png, png.with_suffix(".log"))


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(prog="python -m hiraku.cfd.render_blender", description="A picture of a case's streamlines (Blender, background)")
    add_arguments(ap)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    p = prepare(a)
    render(a, p, a.out)
    caption_picture(a.out, p["vmax"], p["tick"], p["text"], a.status)
    print(f"wrote {a.out} in {time.perf_counter() - t0:.0f} s")
    return a.out


if __name__ == "__main__":
    if bpy is not None:
        blender_main(sys.argv[sys.argv.index("--") + 1:])
    else:
        main()
