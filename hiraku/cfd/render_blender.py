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

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, "4")  # numpy's matrix products would otherwise take every core

import numpy as np

try:
    import bpy  # only inside Blender
except ImportError:
    bpy = None


def hex_linear(h: str) -> tuple[float, float, float]:
    """A display colour #rrggbb as the linear values Blender renders in."""
    c = [int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c)


MATERIALS = {   # category -> base colour (linear), metallic, roughness
    "body": ((0.56, 0.58, 0.60), 0.0, 0.42),
    "frame": ((0.13, 0.14, 0.16), 0.75, 0.42),
    "wheels": ((0.042, 0.042, 0.046), 0.0, 0.78),
    "tread": ((0.003, 0.003, 0.0035), 0.0, 0.9),  # drawn on the tyre only as appearance, as are the rim and the hub
    "rim": ((0.62, 0.64, 0.67), 0.8, 0.34),
    "hub": ((0.05, 0.05, 0.06), 0.6, 0.4),
    "dummy": (hex_linear("#e8ae0e"), 0.0, 0.45),  # the screen's crash test dummy: web/js/look.js dummy_body, dummy_joint
    "dummy_joint": (hex_linear("#1c1c1c"), 0.0, 0.5),
    "occupants": ((0.30, 0.31, 0.34), 0.0, 0.75),  # what is not the person: the seats, belts and clips
    "cargo": ((0.20, 0.25, 0.32), 0.0, 0.6),
    "other": ((0.20, 0.205, 0.22), 0.35, 0.5),
    "nets": ((0.45, 0.46, 0.48), 0.8, 0.45),
}
BODY_KINDS = ("body", "fender", "rollbar")
MM = 1e-3
DUMMY_JOINTS = ("neck", "hand", "foot")  # the screen's occupantDummyPieces (web/js/shapes.js): these are dark, the rest yellow
DUMMY_LIMBS = ("arm", "forearm", "thigh", "shank")  # dark at both ends, where the screen draws the joints' balls
DUMMY_WAIST = (0.5, 0.62)  # the torso's dark waist band, shares of its height from the top (web/js/shapes.js DUMMY.chest, .waist)
SUBTITLE_TOP = 910.0 / 1080.0  # the film's subtitles cover the picture below this share of its height


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
    if kinds.get(shape.name) == "occupant":
        return "dummy_joint" if occupant_segment(shape.name) in DUMMY_JOINTS else "dummy"
    if kinds.get(shape.name) in BODY_KINDS:
        return "body"
    if shape.group == "frame":
        return "frame"
    if shape.group in ("occupants", "cargo"):
        return shape.group
    return "other"


def occupant_segment(part_id: str) -> str:
    """The body segment of an occupant part: occupant_R.thigh_L is a thigh, occupant_R the torso."""
    return part_id.split(".", 1)[1].split("_")[0] if "." in part_id else "torso"


def box_slice(s: Any, f0: float, f1: float, grow: float) -> tuple[np.ndarray, np.ndarray]:
    """A box around a stretch of a box shape's longest side, f0 .. f1 of its length, `grow` outside its other sides.
    For drawing only: the shape itself stays as computed."""
    v = s.vertices
    edges = [v[4] - v[0], v[2] - v[0], v[1] - v[0]]
    lengths = [float(np.linalg.norm(e)) for e in edges]
    long = int(np.argmax(lengths))
    a, b = [k for k in range(3) if k != long]
    ua, ub = edges[a] / lengths[a], edges[b] / lengths[b]
    verts = np.array([v[0] + f * edges[long] + sa * ua + sb * ub for f in (f0, f1) for sa in (-grow, lengths[a] + grow) for sb in (-grow, lengths[b] + grow)])
    return verts, np.array([[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]])


def dummy_joints(s: Any) -> list[tuple[np.ndarray, np.ndarray]]:
    """The dark bands of an occupant part, where the screen's dummy has its joints: the ends of a limb, the torso's waist.
    Drawn just outside the part's box; the box stays as computed."""
    segment = occupant_segment(s.name)
    v = s.vertices
    edges = [v[4] - v[0], v[2] - v[0], v[1] - v[0]]
    lengths = sorted(float(np.linalg.norm(e)) for e in edges)
    long = max(edges, key=lambda e: float(np.linalg.norm(e)))
    if segment in DUMMY_LIMBS:
        share = min(0.5 * 0.5 * (lengths[0] + lengths[1]) / lengths[2], 0.25)
        return [box_slice(s, 0.0, share, 0.0015), box_slice(s, 1.0 - share, 1.0, 0.0015)]
    if segment == "torso":
        upward = long[2] >= 0.0
        f0, f1 = (1.0 - DUMMY_WAIST[1], 1.0 - DUMMY_WAIST[0]) if upward else DUMMY_WAIST
        return [box_slice(s, f0, f1, 0.0015)]
    return []


def wheel_look(c: Any, grooves: int = 40, lift: float = 0.0015) -> dict[str, list[tuple[np.ndarray, Any]]]:
    """What makes a tyre read as a wheel, drawn just off its surface: a rim disc and a hub on each side, tread grooves
    across it and two around it. For drawing only: the cylinder the air sees stays as computed."""
    a = c.axis
    ref = np.array([0.0, 0.0, 1.0]) if abs(a[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = np.cross(a, ref)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(a, e1)
    out: dict[str, list[tuple[np.ndarray, Any]]] = {"rim": [], "hub": [], "tread": []}

    def disc(centre: np.ndarray, radius: float, facing: float) -> tuple[np.ndarray, list[np.ndarray]]:
        th = 2.0 * np.pi * np.arange(48) / 48
        ring = centre + radius * (np.outer(np.cos(th), e1) + np.outer(np.sin(th), e2))
        return ring, [np.arange(48)[::-1 if facing < 0 else 1]]

    for side in (-1.0, 1.0):
        out["rim"].append(disc(c.center + side * (c.half + lift) * a, 0.62 * c.radius, side))
        out["hub"].append(disc(c.center + side * (c.half + 2.0 * lift) * a, 0.22 * c.radius, side))
    radius = c.radius + lift
    pitch = 2.0 * np.pi / grooves
    quads, verts = [], []
    for k in range(grooves):  # across the tread, leaving its shoulders
        th0, th1 = k * pitch, k * pitch + 0.26 * pitch
        corners = [c.center + radius * (np.cos(th) * e1 + np.sin(th) * e2) + w * c.half * a for th, w in ((th0, -0.86), (th1, -0.86), (th1, 0.86), (th0, 0.86))]
        quads.append(np.arange(4) + len(verts) * 4)
        verts.append(np.array(corners))
    for w0 in (-0.36, 0.36):  # around the tread
        for k in range(96):
            th0, th1 = 2.0 * np.pi * k / 96, 2.0 * np.pi * (k + 1) / 96
            corners = [c.center + radius * (np.cos(th) * e1 + np.sin(th) * e2) + w * c.half * a for th, w in ((th0, w0 - 0.05), (th1, w0 - 0.05), (th1, w0 + 0.05), (th0, w0 + 0.05))]
            quads.append(np.arange(4) + len(verts) * 4)
            verts.append(np.array(corners))
    out["tread"].append((np.vstack(verts), np.array(quads)))
    return out


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
        if cat == "wheels" and hasattr(s, "axis"):
            for look, meshes in wheel_look(s).items():
                for mesh in meshes:
                    out.setdefault(look, MeshBuilder()).add(*mesh)
        elif cat == "dummy":
            for mesh in dummy_joints(s):
                out.setdefault("dummy_joint", MeshBuilder()).add(*mesh)
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
    view = np.array(opt["view"])  # the lamps stay where they are for the vehicle, whatever its yaw on the grid

    def lamp(dx: float, dy: float, z: float) -> tuple[float, float, float]:
        d = view @ np.array((dx, dy, 0.0))
        return (float(target[0] + d[0]), float(target[1] + d[1]), z)

    _area_light("key", lamp(-3.0, -4.5, 5.0), target, 6.0, 700.0, (1.0, 0.97, 0.93))
    _area_light("fill", lamp(-5.5, 3.0, 2.5), target, 5.0, 260.0, (0.75, 0.85, 1.0))
    _area_light("rim", lamp(4.5, -1.5, 3.5), target, 4.0, 300.0, (0.85, 0.92, 1.0))

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


def free_ram_gb() -> float | None:
    """Free physical memory in GB; None where it cannot be read."""
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32), ("total", ctypes.c_uint64), ("free", ctypes.c_uint64), ("page_total", ctypes.c_uint64), ("page_free", ctypes.c_uint64),
                    ("virtual_total", ctypes.c_uint64), ("virtual_free", ctypes.c_uint64), ("extended", ctypes.c_uint64)]

    try:
        s = Status()
        s.length = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):
            return s.free / 1024.0 ** 3
    except AttributeError:  # not Windows
        pass
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024.0 ** 3
    except (AttributeError, ValueError, OSError):
        return None


def wait_for_ram(min_gb: float, poll_s: float = 30.0) -> None:
    """Rendering shares the PC with other jobs: wait until this much memory is free before starting."""
    while True:
        free = free_ram_gb()
        if free is None or free >= min_gb:
            return
        print(f"waiting: {free:.1f} GB free, {min_gb:g} GB wanted", flush=True)
        time.sleep(poll_s)


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


def _fitting_font(g: Any, texts: list[str], px: int, width: float, least: int) -> Any:
    """The font at px, or smaller (not under least) until every text fits the width."""
    while px > least and max(g.textlength(t, font=_font(px)) for t in texts) > width:
        px -= 2
    return _font(px)


def caption_overlay(size: tuple[int, int], vmax: float, step: float, blocks: list[tuple[list[str], str]]) -> Any:
    """The words and the colour bar as a see-through layer. blocks: per panel of the picture, the lines of what it shows and
    how far the accuracy has been checked. The words top left, large; the colour bar bottom right, above the band the film's
    subtitles cover. Dark bands under them keep them readable over the lines."""
    from PIL import Image, ImageDraw

    W, H = size
    s = H / 1080.0
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    g = ImageDraw.Draw(im)
    band = (6, 7, 10, 215)
    ink, amber = (232, 235, 238), (255, 214, 120)
    pad, margin = int(20 * s), int(28 * s)
    panel = W / len(blocks)
    for n, (lines_text, status) in enumerate(blocks):
        left = int(n * panel) + margin
        font = _fitting_font(g, [*lines_text, status], int(52 * s), panel - 2 * (margin + pad), int(26 * s))
        row = int(font.size * 1.24)
        width = int(max(g.textlength(t, font=font) for t in [*lines_text, status]))
        top = int(24 * s)
        g.rectangle((left, top, left + width + 2 * pad, top + row * (len(lines_text) + 1) + 2 * pad), fill=band)
        y = top + pad
        for t in lines_text:
            g.text((left + pad, y), t, font=font, fill=ink)
            y += row
        g.text((left + pad, y), status, font=font, fill=amber)
        if n:
            g.line([(int(n * panel), 0), (int(n * panel), H)], fill=(120, 124, 130, 160), width=max(1, int(2 * s)))
    # the colour bar, right, ending above the subtitles' band
    small = _font(int(30 * s))
    bw, bh = int(460 * s), int(16 * s)
    x0 = W - margin - pad - bw
    bottom = int(SUBTITLE_TOP * H) - int(14 * s)
    y0 = bottom - pad - bh
    g.rectangle((x0 - pad - int(10 * s), y0 - int(104 * s), W - margin + int(10 * s), bottom), fill=band)
    for i in range(bw):
        c = tuple(int(round(255 * v)) for v in ramp(i / (bw - 1)))
        g.line([(x0 + i, y0), (x0 + i, y0 + bh)], fill=c)
    for k in range(int(round(vmax / step)) + 1):
        v = k * step
        x = x0 + int((bw - 1) * v / vmax)
        g.line([(x, y0 - int(6 * s)), (x, y0)], fill=ink, width=max(1, int(2 * s)))
        text = f"{v:g}"
        tw = g.textlength(text, font=small)
        g.text((x - tw / 2, y0 - int(46 * s)), text, font=small, fill=ink)
    g.text((x0, y0 - int(94 * s)), "空気の速さ [m/s]", font=small, fill=ink)
    return im


def caption_picture(png: Path, vmax: float, step: float, blocks: list[tuple[list[str], str]]) -> None:
    from PIL import Image

    base = Image.open(png).convert("RGBA")
    Image.alpha_composite(base, caption_overlay(base.size, vmax, step, blocks)).convert("RGB").save(png)


def vehicle_outline(shapes: list[Any], view: np.ndarray) -> Any:
    """The box around the whole vehicle, turned with it: view turns the grid at yaw 0 to the grid of this case. The solid
    shapes only (the nets let air through)."""
    from hiraku.cfd.geometry import box

    corners = []
    for s in shapes:
        if s.reflect < 1.0:
            continue
        if hasattr(s, "vertices"):
            corners.append(s.vertices)
        else:
            lo, hi = s.bounds()
            corners.append(np.array([lo, hi]))
    upright = np.vstack(corners) @ view  # each row: view^T p, the yaw 0 grid
    lo, hi = upright.min(axis=0), upright.max(axis=0)
    return box(view @ (0.5 * (lo + hi)), hi - lo, view)


def in_front_of_vehicle(lines: Any, outline: Any, eye: np.ndarray, limit: float = 0.4, reach: float = 12.0, stride: int = 4, chunk: int = 4000) -> np.ndarray:
    """The lines that run outside the vehicle's outline but between the camera and it for more than `limit` metres: they
    would hide the vehicle. The test is on the outline and not on each part, since most sight lines pass through the open
    frame between parts. Whole lines are left out, none is cut or moved, so what remains is still only real streamlines.
    A line through the open frame stays: inside the outline it is part of what the picture shows."""
    from hiraku.cfd.streamlines import ShapeStop

    n = len(lines)
    if not n:
        return np.zeros(0, bool)
    stop = ShapeStop([outline])
    at = np.arange(0, len(lines.points), stride)
    p = lines.points[at]
    idx = np.nonzero(~outline.inside(p))[0]
    hit = np.zeros(len(p), bool)
    for s in range(0, len(idx), chunk):
        some = idx[s:s + chunk]
        d = p[some] - eye
        d /= np.linalg.norm(d, axis=1, keepdims=True)
        hit[some] = stop(p[some], p[some] + reach * d)
    step = float(np.median(np.linalg.norm(np.diff(lines.points[lines.offsets[0]:lines.offsets[1]], axis=0), axis=1))) * stride
    line_of = np.searchsorted(lines.offsets, at, side="right") - 1
    return np.bincount(line_of[hit], minlength=n) * step > limit


def yaw_words(yaw: float) -> str:
    return f"{yaw:+g}°" if yaw else "0°"


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
    ap.add_argument("--across", type=float, nargs=2, help="the plane's range in grid y, m (default: as wide as the vehicle, moved away from the camera's y- side)")
    ap.add_argument("--up", type=float, nargs=2, default=(0.08, 2.3), help="the plane's range in z, m")
    ap.add_argument("--radius", type=float, default=0.005, help="tube radius, m")
    ap.add_argument("--downstream", type=float, default=7.0, help="lines end this far behind the moment centre, m")
    ap.add_argument("--camera", type=float, nargs=3, default=(-3.6, -7.2, 2.3), help="the camera, m, as the grid has it at yaw 0: it stays put for the vehicle at any yaw")
    ap.add_argument("--target", type=float, nargs=3, default=(1.1, 0.0, 0.8), help="where it looks, the same way")
    ap.add_argument("--lens", type=float, default=42.0)
    ap.add_argument("--vmax", type=float, help="the colour scale's top, m/s (default: from the lines' speeds)")
    ap.add_argument("--status", default="精度は確かめていない")
    ap.add_argument("--keep-scene", action="store_true")
    ap.add_argument("--min-free-gb", type=float, default=15.0, help="wait until this much memory is free before rendering")


def prepare(a: argparse.Namespace) -> dict[str, Any]:
    """The design, its shapes and the streamlines of the picture."""
    from hiraku.cfd.streamlines import MeanField, ShapeStop, plane_seeds, trace
    from hiraku.cfd.vehicle import design_shapes, to_grid

    t0 = time.perf_counter()
    summary = json.loads((a.case / "case.json").read_text(encoding="utf-8"))
    design = json.loads(a.design.read_text(encoding="utf-8"))
    shapes, _ = design_shapes(design, summary["yaw_deg"], summary["speed_ms"], u_lattice=summary["numerics"]["u_lattice"], dx=summary["dx"])
    field = MeanField.from_case(a.case)
    stop = ShapeStop(shapes)
    lo, hi = stop.box_lo, stop.box_hi
    # the same camera for the vehicle at any yaw: the grid's air stays along +x, so the camera turns with the vehicle
    view = to_grid(float(summary["yaw_deg"])) @ to_grid(0.0).T
    eye, target = view @ np.array(a.camera, float), view @ np.array(a.target, float)
    x_plane = lo[0] - 1.6
    across = a.across or (lo[1] + 0.45, hi[1] + 0.45)
    n_across = a.n[0] if a.across else max(2, int(round(a.n[0] * (across[1] - across[0]) / 1.5)))  # the same spacing at any yaw
    seeds = plane_seeds((x_plane, across[0], a.up[0]), (0.0, across[1] - across[0], 0.0), (0.0, 0.0, a.up[1] - a.up[0]), n_across, a.n[1])
    region = (field.lo, (a.downstream, field.hi[1], field.hi[2]))
    lines = trace(field, seeds, stop=stop, region=region, max_length_m=25.0)
    traced = len(lines)
    lines = lines.select(~in_front_of_vehicle(lines, vehicle_outline(shapes, view), eye))
    vmax, tick = nice_scale(a.vmax if a.vmax else float(np.percentile(lines.speed, 99.8)))
    print(f"{len(lines)} of {traced} lines, {len(lines.points)} points, ends {lines.stop_counts()}, speed {lines.speed.min():.2f}..{lines.speed.max():.2f} m/s, colour top {vmax:g} m/s, {time.perf_counter() - t0:.1f} s")
    text = [f"v{summary['design_version']} {summary['speed_ms'] * 3.6:.0f} km/h 横風 {yaw_words(float(summary['yaw_deg']))} 格子 {summary['dx'] * 1000:.0f} mm",
            "平均の流れの流線。色は速さ"]
    return {"summary": summary, "design": design, "shapes": shapes, "field": field, "lines": lines, "vmax": vmax, "tick": tick, "text": text, "eye": eye, "target": target, "view": view}


def render(a: argparse.Namespace, p: dict[str, Any], png: Path, line_strength: float = 1.0) -> None:
    """The still picture in Blender, without its caption; beside it <png stem>.camera.json, the camera Blender used."""
    wait_for_ram(a.min_free_gb)
    per_point = max(1, int(round(0.06 / (p["field"].dx / 4.0))))
    arrays = scene_arrays(p["design"], p["summary"], p["lines"], p["vmax"], a.radius, per_point, p["shapes"])
    options = {"resolution": list(a.resolution), "samples": a.samples, "threads": a.threads, "background": [0.0035, 0.004, 0.0065], "line_strength": line_strength, "camera": p["eye"].tolist(),
               "target": p["target"].tolist(), "view": p["view"].tolist(), "lens": a.lens, "ground": [-22.0, -22.0, 26.0, 22.0]}
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
    caption_picture(a.out, p["vmax"], p["tick"], [(p["text"], a.status)])
    print(f"wrote {a.out} in {time.perf_counter() - t0:.0f} s")
    return a.out


if __name__ == "__main__":
    if bpy is not None:
        blender_main(sys.argv[sys.argv.index("--") + 1:])
    else:
        main()
