"""Pictures of a flooded-road case: the water surface from the particles and the opaque vehicle, on a dark background.

Two parts in one file. In plain Python the driver computes meshes as numpy arrays (the water surface by density splatting and
surface nets, the vehicle from the same shape objects the water collides with) and starts Blender as a separate process in
background mode with factory settings, so no open Blender scene is touched:

    python -m hiraku.cfd.render_water_blender <case folder> --design <design.json> --frames 30 --out <folder>

Inside Blender the same file builds the scene and renders the frames on the CPU (Cycles), with at most 4 threads.
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
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Iterator

import numpy as np

try:
    import bpy  # only inside Blender
except ImportError:
    bpy = None

WATER_THREADS = 4
MIN_FREE_RAM_GB = 15.0  # meshing and rendering start only with this much free: the PC is shared with other heavy jobs
STATEMENT ="見せるための計算で、走破の判定ではない"
BLENDER_DEFAULT = "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
MATERIALS = {  # group -> base colour (linear), metallic, roughness
    "body": ((0.62, 0.64, 0.66), 0.0, 0.45),
    "frame": ((0.16, 0.17, 0.19), 0.7, 0.45),
    "suspension": ((0.22, 0.22, 0.24), 0.6, 0.5),
    "steering": ((0.2, 0.21, 0.23), 0.4, 0.5),
    "powertrain": ((0.26, 0.27, 0.30), 0.3, 0.5),
    "wheels": ((0.02, 0.02, 0.022), 0.0, 0.75),
    "occupants": ((0.34, 0.35, 0.38), 0.0, 0.8),
    "cargo": ((0.22, 0.28, 0.36), 0.0, 0.6),
    "side_nets": ((0.45, 0.46, 0.48), 0.8, 0.45),
}


# ---------------------------------------------------------------- the water surface (plain numpy)

EDGES = np.array([(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)])
CORNERS = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1)], float)  # x y z


def surface_nets(field: np.ndarray, iso: float) -> tuple[np.ndarray, np.ndarray]:
    """Surface nets of field[x, y, z]: vertices in index coordinates and quads, wound so the normal points to lower values."""
    inside = field > iso
    nx, ny, nz = field.shape
    count = np.zeros((nx - 1, ny - 1, nz - 1), np.uint8)
    for c in CORNERS.astype(int):
        count += inside[c[0]:nx - 1 + c[0], c[1]:ny - 1 + c[1], c[2]:nz - 1 + c[2]]
    active = (count > 0) & (count < 8)
    del count
    cx, cy, cz = np.nonzero(active)
    if not len(cx):
        return np.zeros((0, 3)), np.zeros((0, 4), np.int64)
    index = np.full(active.shape, -1, np.int32)
    index[cx, cy, cz] = np.arange(len(cx), dtype=np.int32)
    total = np.zeros((len(cx), 3))
    hits = np.zeros(len(cx))
    values = [field[cx + int(c[0]), cy + int(c[1]), cz + int(c[2])] for c in CORNERS]
    for a, b in EDGES:
        fa, fb = values[a], values[b]
        cross = (fa > iso) != (fb > iso)
        t = (iso - fa[cross]) / (fb[cross] - fa[cross])
        total[cross] += CORNERS[a] + t[:, None] * (CORNERS[b] - CORNERS[a])
        hits[cross] += 1.0
    verts = np.column_stack([cx, cy, cz]) + total / hits[:, None]
    quads = []
    # an edge of the grid along each axis that crosses the surface makes a quad of the four cells around it
    for axis in range(3):
        u, v = (axis + 1) % 3, (axis + 2) % 3
        low = [slice(None)] * 3
        high = [slice(None)] * 3
        low[axis] = slice(0, -1)
        high[axis] = slice(1, None)
        low[u] = high[u] = slice(1, -1)
        low[v] = high[v] = slice(1, -1)
        a, b = inside[tuple(low)], inside[tuple(high)]
        change = a != b
        idx = np.nonzero(change)
        pos = [idx[0], idx[1], idx[2]]
        pos[u] = pos[u] + 1
        pos[v] = pos[v] + 1
        outward = a[idx]  # inside at the low end: the normal points along +axis
        def cell(du: int, dv: int) -> np.ndarray:
            p = list(pos)
            p[u] = p[u] - du
            p[v] = p[v] - dv
            return index[p[0], p[1], p[2]]
        q = np.column_stack([cell(1, 1), cell(0, 1), cell(0, 0), cell(1, 0)])
        q[~outward] = q[~outward][:, ::-1]
        quads.append(q)
    quads = np.concatenate(quads)
    quads = quads[(quads >= 0).all(axis=1)]
    return verts, quads


def splat(points: np.ndarray, weights: np.ndarray | None, lo: np.ndarray, h: float, shape: tuple[int, int, int]) -> np.ndarray:
    """Trilinear deposit on nodes at lo + index * h. Points outside are dropped."""
    f = (points - lo) / h
    b = np.floor(f).astype(np.int64)
    keep = np.all((b >= 0) & (b < np.array(shape) - 1), axis=1)
    b, f = b[keep], (f - b)[keep]
    w = weights[keep] if weights is not None else np.ones(len(b))
    out = np.zeros(int(np.prod(shape)))
    sx, sy = shape[1] * shape[2], shape[2]
    base = b[:, 0] * sx + b[:, 1] * sy + b[:, 2]
    for dx in (0, 1):
        wx = f[:, 0] if dx else 1.0 - f[:, 0]
        for dy in (0, 1):
            wy = f[:, 1] if dy else 1.0 - f[:, 1]
            for dz in (0, 1):
                wz = f[:, 2] if dz else 1.0 - f[:, 2]
                out += np.bincount(base + dx * sx + dy * sy + dz, weights=w * wx * wy * wz, minlength=out.size)
    return out.reshape(shape)


def water_mesh(world: np.ndarray, speed: np.ndarray, road_height: Any, width: float, dx: float, box_x: tuple[float, float], fine: float = 0.5,
               iso: float = 0.42, blur_cells: float = 0.8, spray_max: int = 150000) -> dict[str, np.ndarray]:
    """The surface of the particles: density on a grid of fine * dx, mirrored across the road and the side walls so the surface
    meets them, then surface nets. Particles too far from the rest to reach the iso level become small balls of their own volume."""
    from scipy import ndimage

    h = fine * dx
    ground = road_height(world[:, 0])
    near_road = world[:, 2] - ground < 3.0 * h
    below = world[near_road].copy()
    below[:, 2] = 2.0 * ground[near_road] - below[:, 2]
    pts = [world, below]
    sp = [speed, speed[near_road]]
    for sign in (-1.0, 1.0):
        wall = sign * 0.5 * width
        near = sign * (wall - world[:, 1]) < 3.0 * h
        m = world[near].copy()
        m[:, 1] = 2.0 * wall - m[:, 1]
        pts.append(m)
        sp.append(speed[near])
    p = np.concatenate(pts)
    s = np.concatenate(sp)
    z_top = float(world[:, 2].max()) + 4.0 * h
    lo = np.array([box_x[0] - 4.0 * h, -0.5 * width - 4.0 * h, float(ground.min()) - 6.0 * h])
    shape = (int((box_x[1] - box_x[0]) / h) + 9, int(width / h) + 9, int((z_top - lo[2]) / h) + 3)
    cell_particles = (0.5 * dx / h) ** 3  # one particle's share of a fine cell
    mass = (splat(p, None, lo, h, shape) * cell_particles).astype(np.float32)
    momentum = (splat(p, s, lo, h, shape) * cell_particles).astype(np.float32)
    del p, s, pts, sp
    density = ndimage.gaussian_filter(mass, blur_cells, mode="constant", truncate=2.5, output=np.float32)
    del mass
    weighted = ndimage.gaussian_filter(momentum, blur_cells, mode="constant", truncate=2.5, output=np.float32)
    del momentum
    verts_i, quads = surface_nets(density, iso)
    verts = lo + verts_i * h
    # the part under the road is never seen
    keep_v = verts[:, 2] > road_height(verts[:, 0]) - 1.5 * h
    keep_q = keep_v[quads].any(axis=1)
    quads = quads[keep_q]
    used = np.unique(quads)
    remap = np.full(len(verts), -1, np.int64)
    remap[used] = np.arange(len(used))
    quads = remap[quads]
    idx = np.clip(np.rint(verts_i[used]).astype(np.int64), 0, np.array(shape) - 1)
    verts = verts[used]
    speed_v = weighted[idx[:, 0], idx[:, 1], idx[:, 2]] / np.maximum(density[idx[:, 0], idx[:, 1], idx[:, 2]], 1e-3)
    # the flying ones: particles where the field stays under the level
    g = np.clip(np.rint((world - lo) / h).astype(np.int64), 0, np.array(shape) - 1)
    alone = density[g[:, 0], g[:, 1], g[:, 2]] < iso * 1.05
    # a thin slow film on the road is not flying water: it stays out of the balls (and under the level, out of the surface)
    alone &= (speed > 0.5) | (world[:, 2] - ground > 3.0 * h)
    fly = world[alone][:spray_max]
    fly_speed = speed[alone][:spray_max]
    return {"verts": verts.astype(np.float32), "quads": quads.astype(np.int32), "speed": speed_v.astype(np.float32), "fly": fly.astype(np.float32), "fly_speed": fly_speed.astype(np.float32)}


def load_frame(folder: Path, frame: int) -> tuple[np.ndarray, np.ndarray]:
    with np.load(folder / "frames" / f"p{frame:04d}.npz") as d:
        return d["xyz"].astype(np.float64) * d["scale"] + d["lo"], d["speed"].astype(np.float64) / 10.0


def icosphere(radius: float) -> tuple[np.ndarray, np.ndarray]:
    t = (1.0 + math.sqrt(5.0)) / 2.0
    v = np.array([(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)], float)
    f = np.array([(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)])
    return radius * v / np.linalg.norm(v[0]), f


# ---------------------------------------------------------------- the vehicle and the road (plain numpy)

def polytope_faces(s: Any) -> tuple[np.ndarray, list[np.ndarray]]:
    """Faces of a convex polytope from its own planes and vertices."""
    v = np.unique(np.round(s.vertices, 9), axis=0)
    faces = []
    for n, off in zip(s.normals, s.offsets):
        on = np.flatnonzero(np.abs(v @ n - off) < 1e-6)
        if len(on) < 3:
            continue
        r = v[on] - v[on].mean(axis=0)
        e1 = r[0] / np.linalg.norm(r[0])
        e2 = np.cross(n, e1)
        faces.append(on[np.argsort(np.arctan2(r @ e2, r @ e1))])
    return v, faces


def cylinder_faces(c: Any, sides: int = 48) -> tuple[np.ndarray, list[np.ndarray]]:
    a = c.axis
    ref = np.array([0.0, 0.0, 1.0]) if abs(a[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = np.cross(a, ref)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(a, e1)
    th = 2.0 * np.pi * np.arange(sides) / sides
    ring = c.radius * (np.outer(np.cos(th), e1) + np.outer(np.sin(th), e2))
    v = np.vstack([c.center - c.half * a + ring, c.center + c.half * a + ring])
    i = np.arange(sides)
    j = (i + 1) % sides
    return v, list(np.column_stack([i, j, j + sides, i + sides])) + [i[::-1], i + sides]


def vehicle_arrays(shapes: list[Any]) -> dict[str, np.ndarray]:
    """One mesh per group, in vehicle axes, from the same shape objects the water collides with. Nets are left out of the
    solid picture because water goes through them."""
    from hiraku.cfd.geometry import Cylinder

    out: dict[str, np.ndarray] = {}
    groups: dict[str, list[tuple[np.ndarray, list[np.ndarray]]]] = {}
    for s in shapes:
        if s.reflect < 1.0:
            continue
        groups.setdefault(s.group, []).append(cylinder_faces(s) if isinstance(s, Cylinder) else polytope_faces(s))
    for g, parts in groups.items():
        verts, loops, starts = [], [], []
        nv = nl = 0
        for v, faces in parts:
            verts.append(v)
            for f in faces:
                starts.append(nl)
                loops.append(np.asarray(f) + nv)
                nl += len(f)
            nv += len(v)
        out[f"veh_{g}_v"] = np.vstack(verts).astype(np.float32)
        out[f"veh_{g}_l"] = np.concatenate(loops).astype(np.int32)
        out[f"veh_{g}_s"] = np.array(starts, np.int32)
    return out


def road_arrays(road: Any, length: float, width: float, level: float) -> dict[str, np.ndarray]:
    """The road's profile extruded across, wide enough for the picture, and a low wall along each side."""
    margin = 2.5
    xs = np.linspace(-0.5 * length - margin, 0.5 * length + margin, 601)
    z = road.height(np.clip(xs, -0.5 * length, 0.5 * length))
    y = np.array([-0.5 * width - margin, -0.5 * width, 0.5 * width, 0.5 * width + margin])
    verts = np.array([(x, yy, zz) for x, zz in zip(xs, z) for yy in y], np.float32)
    n = len(y)
    faces = []
    for i in range(len(xs) - 1):
        for j in range(n - 1):
            a = i * n + j
            faces.append((a, a + n, a + n + 1, a + 1))
    wall_h = level + 0.1
    w, f = [], []
    for side in (-1.0, 1.0):
        y0, y1 = side * 0.5 * width, side * (0.5 * width + 0.14)
        b = len(w)
        w += [(-0.5 * length, y0, 0.0), (0.5 * length, y0, 0.0), (0.5 * length, y0, wall_h), (-0.5 * length, y0, wall_h),
              (-0.5 * length, y1, 0.0), (0.5 * length, y1, 0.0), (0.5 * length, y1, wall_h), (-0.5 * length, y1, wall_h)]
        q = [(0, 1, 2, 3), (5, 4, 7, 6), (3, 2, 6, 7), (1, 5, 6, 2), (4, 0, 3, 7)]
        f += [tuple(b + k for k in face) for face in (q if side > 0 else [fc[::-1] for fc in q])]
    # tick marks on the road, one per metre, so the eye can measure the water
    tv, tf = [], []
    for x in np.arange(-math.floor(road.half_length_m), math.floor(road.half_length_m) + 1.0):
        g = float(road.height(x))
        b = len(tv)
        hw = 0.02
        yb = -0.5 * width + 0.02
        tv += [(x - hw, yb, g + 0.002), (x + hw, yb, g + 0.002), (x + hw, yb + 0.4, g + 0.002), (x - hw, yb + 0.4, g + 0.002)]
        tf.append((b, b + 1, b + 2, b + 3))
    return {"road_v": verts, "road_f": np.array(faces, np.int32), "wall_v": np.array(w, np.float32), "wall_f": np.array(f, np.int32),
            "tick_v": np.array(tv, np.float32), "tick_f": np.array(tf, np.int32)}


CAMERAS = {
    # name: (offset from the vehicle or a fixed place, target, lens mm, follow)
    "above": ((-1.0, -7.0, 3.27), (-0.5, 0.0, 0.30), 32.0, True),  # about 23 degrees down
    "wide": ((0.0, -11.5, 3.2), (0.0, 0.0, 0.2), 30.0, False),
    "side": ((-0.9, -5.6, 1.15), (-0.5, 0.0, 0.62), 32.0, True),
    "follow": ((-4.3, -4.6, 2.0), (-0.3, 0.0, 0.35), 40.0, True),
    "rear": ((4.6, -3.4, 1.5), (0.2, 0.0, 0.3), 38.0, True),
}


# ---------------------------------------------------------------- driver

def free_ram_gb() -> float | None:
    """Free physical memory in GB, or None when this PC cannot say."""
    if sys.platform == "win32":
        import ctypes as ct

        class MemoryStatus(ct.Structure):
            _fields_ = [("dwLength", ct.c_ulong), ("dwMemoryLoad", ct.c_ulong), ("ullTotalPhys", ct.c_ulonglong), ("ullAvailPhys", ct.c_ulonglong), ("ullTotalPageFile", ct.c_ulonglong),
                        ("ullAvailPageFile", ct.c_ulonglong), ("ullTotalVirtual", ct.c_ulonglong), ("ullAvailVirtual", ct.c_ulonglong), ("ullAvailExtendedVirtual", ct.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ct.sizeof(MemoryStatus)
        return status.ullAvailPhys / 2 ** 30 if ct.windll.kernel32.GlobalMemoryStatusEx(ct.byref(status)) else None
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2 ** 30
    except (OSError, ValueError, AttributeError):
        return None


@contextmanager
def render_turn(min_free_gb: float = MIN_FREE_RAM_GB, wait_s: float = 86400.0, poll_s: float = 15.0, lock_poll_s: float = 0.5, gap_s: float = 1.0) -> Iterator[None]:
    """One meshing or rendering job at a time across processes, started only when enough memory is free. After its turn a job
    waits gap_s, longer than others poll the lock, so a long render that takes turn after turn leaves room for them."""
    path = Path(tempfile.gettempdir()) / "hiraku-render.lock"
    f = open(path, "a+b")
    t0 = time.monotonic()
    try:
        while True:
            try:
                f.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() - t0 > wait_s:
                    raise TimeoutError("絵を作る順番が回ってきませんでした") from None
                time.sleep(lock_poll_s)
        while True:
            free = free_ram_gb()
            if free is None or free >= min_free_gb:
                break
            if time.monotonic() - t0 > wait_s:
                raise TimeoutError(f"メモリの空きが {min_free_gb:.0f} GB になりませんでした (いま {free:.1f} GB)")
            print(f"メモリの空きを待っています: いま {free:.1f} GB、要るのは {min_free_gb:.0f} GB", flush=True)
            time.sleep(poll_s)
        yield
    finally:
        try:
            f.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()
        time.sleep(gap_s)


def find_blender(given: str | None = None) -> str:
    for c in (given, os.environ.get("HIRAKU_BLENDER"), shutil.which("blender"), BLENDER_DEFAULT):
        if c and Path(c).exists():
            return str(c)
    raise FileNotFoundError("Blender が見つかりません (--blender か HIRAKU_BLENDER で指せます)")


def prepare_frames(case_folder: Path, work: Path, frames: list[int], dx: float, road: Any, width: float, length: float, fine: float) -> None:
    for frame in frames:
        target = work / f"w{frame:04d}.npz"
        if target.exists():
            continue
        t0 = time.perf_counter()
        world, speed = load_frame(case_folder, frame)
        mesh = water_mesh(world, speed, road.height, width, dx, (-0.5 * length, 0.5 * length), fine=fine)
        np.savez(target, **mesh)
        print(f"mesh {frame}: {len(mesh['verts'])} verts, {len(mesh['fly'])} flying, {time.perf_counter() - t0:.1f} s", flush=True)


def build_scene(case_folder: Path, design_path: Path, work: Path) -> dict[str, Any]:
    from hiraku.cfd.vehicle import design_shapes
    from hiraku.cfd.water import Road

    case = json.loads((case_folder / "case.json").read_text(encoding="utf-8"))
    design = json.loads(design_path.read_text(encoding="utf-8"))
    inp = case["inputs"]
    shapes, _ = design_shapes(design, 0.0, inp["speed_ms"], inp["loading"], rotate_wheels=inp["rotate_wheels"], dx=0.0)
    box = case["road"]["box_m"]
    road = Road(inp["depth_m"], 0.5 * case["road"]["water_length_m"], case["road"]["slope"])
    wheel_centres = {}
    if "vehicle" in case["records"][0]:  # a coupled run: each wheel is its own object, moved by the suspension
        for s in shapes:
            if s.group == "wheels":
                wheel_centres[s.name] = [float(v) for v in s.center]
                s.group = s.name
    arrays = {**vehicle_arrays(shapes), **road_arrays(road, box[0], box[1], case["road"]["level_m"])}
    work.mkdir(parents=True, exist_ok=True)
    np.savez(work / "scene.npz", **arrays)
    return {"case": case, "road": road, "box": box, "dx": inp["dx_m"], "wheel_centres": wheel_centres}


def wheel_offsets(record: dict[str, Any], wheel_centres: dict[str, list[float]]) -> dict[str, list[float]]:
    """How far each wheel is from its place in the design, in the vehicle's axes, at one frame of a coupled run."""
    now = record.get("vehicle", {}).get("wheel_centres")
    if not now:
        return {}
    return {name: [float(a - b) for a, b in zip(now[k], wheel_centres[name])] for k, name in enumerate(f"wheel_{w}" for w in ("FL", "FR", "RL", "RR"))}


def render(case_folder: Path, design_path: Path, out_folder: Path, frames: list[int], camera: str = "follow", samples: int = 48, size: tuple[int, int] = (1920, 1080),
           blender: str | None = None, threads: int = WATER_THREADS, fine: float = 0.5, mesh_only: bool = False, prefix: str = "", batch: int = 4, gpu: bool = False) -> list[Path]:
    """Meshes and pictures of the frames, saved to disk one frame at a time. Each batch takes its own render turn and checks the
    free memory again, so a long render leaves room for other jobs. A picture that already exists is not made again. gpu: Cycles on
    the NVIDIA GPU when Blender finds one, in the GPU's turn too."""
    from hiraku.cfd.cuda import gpu_turn

    out_folder.mkdir(parents=True, exist_ok=True)
    work = case_folder / "meshes"
    info = build_scene(case_folder, design_path, work)
    records = info["case"]["records"]
    exe = None if mesh_only else find_blender(blender)
    pngs = [out_folder / f"{prefix}{f:04d}.png" for f in frames]
    todo = frames if mesh_only else [f for f, p in zip(frames, pngs) if not p.exists()]
    for start in range(0, len(todo), batch):
        part = todo[start:start + batch]
        job = {"scene": str(work / "scene.npz"), "frames": [{"frame": f, "mesh": str(work / f"w{f:04d}.npz"), "pose": records[f]["pose"], "wheels": wheel_offsets(records[f], info["wheel_centres"]), "png": str(out_folder / f"{prefix}{f:04d}.png")} for f in part],
               "camera": CAMERAS[camera], "samples": samples, "size": size, "threads": min(threads, WATER_THREADS), "dx": info["dx"], "gpu": gpu}
        job_file = work / f"job_{camera}_{prefix}{part[0]}.json"
        job_file.write_text(json.dumps(job), encoding="utf-8")
        with render_turn():
            prepare_frames(case_folder, work, part, info["dx"], info["road"], info["box"][1], info["box"][0], fine)
            if mesh_only:
                continue
            cmd = [exe, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(Path(__file__).resolve()), "--", str(job_file)]
            with open(work / "blender.log", "a", encoding="utf-8", errors="replace") as log, (gpu_turn() if gpu else nullcontext()):
                code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
        if code != 0:
            raise RuntimeError(f"Blender failed ({code}): see {work / 'blender.log'}")
        print(f"pictures up to frame {part[-1]} done", flush=True)
    return [] if mesh_only else pngs


def font(size: int) -> Any:
    from PIL import ImageFont

    for name in ("C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/meiryo.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def overlay_lines(case: dict[str, Any], frame: int) -> list[str]:
    rec = case["records"][frame]
    inp = case["inputs"]
    uf = case["underfloor"]
    wheel = "回す" if inp["rotate_wheels"] else "回さない"
    return [f"水深 {case['road']['level_m'] * 1000:.0f} mm  時速 {inp['speed_kmh']:.0f} km  車輪 {wheel}  時刻 {rec['t']:.2f} 秒",
            f"床下の水の深さ  平均 {rec['depth_mean_mm']:.0f} mm  最大 {rec['depth_max_mm']:.0f} mm  (粒子から計算)",
            f"いちばん低い部品 {uf['lowest_part_m'] * 1000:.0f} mm  水が届いた床下の列 {rec['reached_share'] * 100:.0f} %"]


def annotate(png: Path, out: Path, lines: list[str], statement: str = STATEMENT) -> None:
    from PIL import Image, ImageDraw

    im = Image.open(png).convert("RGB")
    d = ImageDraw.Draw(im)
    w, h = im.size
    size = max(16, h // 34)
    f, fb = font(size), font(int(size * 1.1))
    y = int(h * 0.03)
    for line in lines:
        d.text((int(w * 0.025), y), line, font=f, fill=(235, 238, 242))
        y += int(size * 1.5)
    d.text((int(w * 0.025), int(h * 0.93)), statement, font=fb, fill=(255, 214, 120))
    im.save(out)


def make_video(case_folder: Path, png_folder: Path, mp4: Path, prefix: str = "", repeat_slow: int = 0) -> Path:
    """Numbers and the statement on every frame, then an mp4 at 30 frames a second. With repeat_slow = n, the pass is shown
    again with each frame held n times."""
    case = json.loads((case_folder / "case.json").read_text(encoding="utf-8"))
    done = png_folder / f"annotated{prefix}"
    done.mkdir(parents=True, exist_ok=True)
    frames = sorted(int(p.stem[len(prefix):]) for p in png_folder.glob(f"{prefix}[0-9][0-9][0-9][0-9].png"))
    if not frames:
        raise FileNotFoundError(f"絵がありません: {png_folder}")
    order = list(frames) + [f for f in frames for _ in range(repeat_slow)]
    for f in frames:
        annotate(png_folder / f"{prefix}{f:04d}.png", done / f"{f:04d}.png", overlay_lines(case, f))
    # with only every second frame rendered, each is held twice as long, so the pass still plays at its true speed
    step = min((b - a for a, b in zip(frames, frames[1:])), default=1)
    return encode(done, order, mp4, step / 30.0)


def compare_video(case_a: Path, png_a: Path, case_b: Path, png_b: Path, mp4: Path, repeat_slow: int = 3) -> Path:
    """Two passes side by side at the same instant, each with its own numbers from its own record. Numbers only: no chart."""
    from PIL import Image, ImageDraw

    a = json.loads((case_a / "case.json").read_text(encoding="utf-8"))
    b = json.loads((case_b / "case.json").read_text(encoding="utf-8"))
    done = mp4.with_suffix("")
    done.mkdir(parents=True, exist_ok=True)
    count = min(len(a["records"]), len(b["records"]))
    width, height = 960, 540
    size = 25
    f = font(size)

    def block(d: Any, x: int, y: int, case: dict[str, Any], frame: int) -> None:
        rec = case["records"][frame]
        wheel = "車輪を回す" if case["inputs"]["rotate_wheels"] else "車輪を回さない"
        lines = [wheel, f"床下の水の深さ  平均 {rec['depth_mean_mm']:.0f} mm  最大 {rec['depth_max_mm']:.0f} mm", f"水が届いた床下の列  {rec['reached_share'] * 100:.0f} %",
                 f"路面から 0.3 m より上にある水  {rec['share_above_0_3m'] * 100:.1f} %"]
        for k, line in enumerate(lines):
            d.text((x + 24, y + k * int(size * 1.55)), line, font=f, fill=(235, 238, 242))

    for frame in range(count):
        sheet = Image.new("RGB", (1920, 1080), (12, 13, 16))
        for x, folder in ((0, png_a), (width, png_b)):
            sheet.paste(Image.open(folder / f"{frame:04d}.png").convert("RGB").resize((width, height), Image.LANCZOS), (x, 110))
        d = ImageDraw.Draw(sheet)
        inp = a["inputs"]
        d.text((24, 34), f"水深 {a['road']['level_m'] * 1000:.0f} mm  時速 {inp['speed_kmh']:.0f} km  時刻 {a['records'][frame]['t']:.2f} 秒  粒子から計算", font=font(30), fill=(235, 238, 242))
        block(d, 0, 670, a, frame)
        block(d, width, 670, b, frame)
        d.text((24, 1000), STATEMENT, font=font(32), fill=(255, 214, 120))
        sheet.save(done / f"{frame:04d}.png")
    return encode(done, list(range(count)) + [k for k in range(count) for _ in range(repeat_slow)], mp4)


def encode(folder: Path, order: list[int], mp4: Path, seconds_each: float = 1.0 / 30.0) -> Path:
    """An mp4 at 30 frames a second from numbered pictures, in the given order."""
    listing = folder / "list.txt"
    listing.write_text("".join(f"file '{(folder / f'{f:04d}.png').as_posix()}'\nduration {seconds_each:.6f}\n" for f in order), encoding="utf-8")
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise FileNotFoundError("ffmpeg が見つかりません")
    cmd = [exe, "-y", "-loglevel", "error", "-threads", str(WATER_THREADS), "-f", "concat", "-safe", "0", "-i", str(listing), "-vf", "fps=30,scale=1920:1080:flags=lanczos", "-c:v", "libx264", "-preset", "slow",
           "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)]
    with render_turn():
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-1500:]}")
    return mp4


def pass_order(frames: int, real_time_every: int) -> tuple[list[int], list[int]]:
    """The two parts of a pass at 30 frames a second, with nothing repeated inside either: real time from every n-th computed
    frame, then all of them, slowed by that same n."""
    return list(range(0, frames, real_time_every)), list(range(frames))


def encode_pictures(pictures: list[Path], mp4: Path) -> Path:
    """One video frame per picture at 30 frames a second, in the given order, with nothing dropped or repeated: the pictures are
    read as a numbered image sequence, with no frame rate filter that could add or remove any."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise FileNotFoundError("ffmpeg が見つかりません")
    mp4.parent.mkdir(parents=True, exist_ok=True)
    seq = mp4.parent / (mp4.stem + "_seq")
    shutil.rmtree(seq, ignore_errors=True)
    seq.mkdir()
    try:
        for i, p in enumerate(pictures):
            os.link(p, seq / f"{i:06d}.png")
        cmd = [exe, "-y", "-loglevel", "error", "-threads", str(WATER_THREADS), "-framerate", "30", "-start_number", "0", "-i", str(seq / "%06d.png"), "-vf", "scale=1920:1080:flags=lanczos",
               "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)]
        with render_turn():
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(seq, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-1500:]}")
    return mp4


def annotated_pictures(case_folder: Path, png_folder: Path, frames: list[int]) -> list[Path]:
    """The rendered frames with their numbers and the statement drawn on, made once and kept next to the pictures."""
    case = json.loads((case_folder / "case.json").read_text(encoding="utf-8"))
    done = png_folder / "annotated"
    done.mkdir(parents=True, exist_ok=True)
    out = []
    for f in frames:
        target = done / f"{f:04d}.png"
        if not target.exists():
            annotate(png_folder / f"{f:04d}.png", target, overlay_lines(case, f))
        out.append(target)
    return out


def overlay_picture(solids: list[Any], local: np.ndarray, dx: float, out: Path, slices: tuple[float, ...] = (0.0, -0.61), x_range: tuple[float, float] = (-1.6, 1.6), z_range: tuple[float, float] = (0.0, 0.7),
                    title: str = "") -> dict[str, int]:
    """Check (c) as a picture: cuts along the vehicle through the shape objects the picture is drawn from (grey), with the
    settled particles of that cut in blue and any inside a shape in red. Returns the counts drawn."""
    from PIL import Image, ImageDraw

    from hiraku.cfd.water import particles_inside

    h = 0.002
    nx, nz = int((x_range[1] - x_range[0]) / h), int((z_range[1] - z_range[0]) / h)
    panels, counts = [], {}
    inside_all = particles_inside(solids, local)
    for y in slices:
        xs = x_range[0] + (np.arange(nx) + 0.5) * h
        zs = z_range[0] + (np.arange(nz) + 0.5) * h
        xx, zz = np.meshgrid(xs, zs, indexing="ij")
        solid = particles_inside(solids, np.column_stack([xx.ravel(), np.full(xx.size, y), zz.ravel()])).reshape(xx.shape)
        rgb = np.full((nz, nx, 3), 16, np.uint8)
        rgb[solid.T[::-1]] = (120, 124, 130)
        im = Image.fromarray(rgb)
        d = ImageDraw.Draw(im)
        band = np.abs(local[:, 1] - y) < 0.25 * dx
        px = ((local[band, 0] - x_range[0]) / h).astype(int)
        pz = nz - 1 - ((local[band, 2] - z_range[0]) / h).astype(int)
        for a, b, red in zip(px, pz, inside_all[band]):
            d.ellipse((a - 3, b - 3, a + 3, b + 3), fill=(240, 60, 50) if red else (70, 170, 240))
        f = font(30)
        counts[f"y={y:+.2f}"] = int(inside_all[band].sum())
        d.text((12, 10), f"車体の中心線から y = {y:+.2f} m の断面  断面の粒子 {int(band.sum())} 個  形の中の粒子 {int(inside_all[band].sum())} 個", font=f, fill=(235, 238, 242))
        panels.append(im)
    sheet = Image.new("RGB", (nx, nz * len(panels) + 80), (10, 10, 12))
    for i, im in enumerate(panels):
        sheet.paste(im, (0, i * nz))
    d = ImageDraw.Draw(sheet)
    d.text((12, nz * len(panels) + 6), title, font=font(30), fill=(235, 238, 242))
    d.text((12, nz * len(panels) + 42), STATEMENT, font=font(30), fill=(255, 214, 120))
    sheet.save(out)
    counts["all"] = int(inside_all.sum())
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("case")
    ap.add_argument("--design", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", default="", help="a, or a:b, or a:b:step; empty: all")
    ap.add_argument("--camera", default="follow", choices=list(CAMERAS))
    ap.add_argument("--samples", type=int, default=48)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--blender")
    ap.add_argument("--threads", type=int, default=WATER_THREADS)
    ap.add_argument("--fine", type=float, default=0.5)
    ap.add_argument("--mesh-only", action="store_true")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--batch", type=int, default=4)
    a = ap.parse_args()
    folder = Path(a.case)
    case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    total = len(case["records"])
    if a.frames:
        bits = [int(b) for b in a.frames.split(":")]
        frames = [bits[0]] if len(bits) == 1 else list(range(bits[0], bits[1], bits[2] if len(bits) > 2 else 1))
    else:
        frames = list(range(total))
    w, h = (int(v) for v in a.size.split("x"))
    pngs = render(folder, Path(a.design), Path(a.out), frames, a.camera, a.samples, (w, h), a.blender, a.threads, a.fine, a.mesh_only, a.prefix, a.batch)
    for p in pngs:
        print(p)


# ---------------------------------------------------------------- inside Blender

def _mesh(name: str, verts: np.ndarray, loops: np.ndarray, starts: np.ndarray, smooth: bool = False) -> Any:
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(verts))
    me.vertices.foreach_set("co", verts.astype(np.float32).ravel())
    me.loops.add(len(loops))
    me.loops.foreach_set("vertex_index", loops.astype(np.int32))
    me.polygons.add(len(starts))
    me.polygons.foreach_set("loop_start", starts.astype(np.int32))
    me.update(calc_edges=True)
    me.validate(clean_customdata=False)
    if smooth:
        me.shade_smooth()
    return me


def _object(name: str, me: Any, mat: Any) -> Any:
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    me.materials.append(mat)
    return ob


def _pbr(name: str, colour: tuple[float, float, float], metallic: float, rough: float) -> Any:
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*colour, 1.0)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    return m


def _water_material() -> Any:
    """Clear blue water that turns white where it moves fast, by the colour attribute `foam`."""
    m = bpy.data.materials.new("water")
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (0.10, 0.34, 0.50, 1.0)
    b.inputs["Metallic"].default_value = 0.0
    b.inputs["Roughness"].default_value = 0.09
    b.inputs["IOR"].default_value = 1.33
    b.inputs["Transmission Weight"].default_value = 0.72
    attr = nt.nodes.new("ShaderNodeVertexColor")
    attr.layer_name = "foam"
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value = (0.10, 0.34, 0.50, 1.0)
    mix.inputs["B"].default_value = (0.92, 0.95, 0.98, 1.0)
    nt.links.new(attr.outputs["Color"], mix.inputs["Factor"])
    nt.links.new(mix.outputs["Result"], b.inputs["Base Color"])
    inv = nt.nodes.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 0.72
    inv.use_clamp = True
    nt.links.new(attr.outputs["Color"], inv.inputs[1])
    nt.links.new(inv.outputs[0], b.inputs["Transmission Weight"])
    return m


def _world() -> None:
    scene = bpy.context.scene
    world = bpy.data.worlds.new("dark")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    bg.inputs[0].default_value = (0.52, 0.60, 0.72, 1.0)
    bg.inputs[1].default_value = 0.55
    dark = nt.nodes.new("ShaderNodeBackground")
    dark.inputs[0].default_value = (0.012, 0.014, 0.018, 1.0)
    dark.inputs[1].default_value = 1.0
    path = nt.nodes.new("ShaderNodeLightPath")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(path.outputs["Is Camera Ray"], mix.inputs[0])
    nt.links.new(bg.outputs[0], mix.inputs[1])
    nt.links.new(dark.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], nt.nodes["World Output"].inputs[0])


def _light(name: str, loc: tuple[float, float, float], size: float, energy: float) -> None:
    li = bpy.data.lights.new(name, "AREA")
    li.shape = "RECTANGLE"
    li.size = size
    li.size_y = size * 0.6
    li.energy = energy
    ob = bpy.data.objects.new(name, li)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    target = bpy.data.objects.new(name + "_t", None)
    bpy.context.scene.collection.objects.link(target)
    target.location = (0.0, 0.0, 0.0)
    con = ob.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"


def _use_optix() -> bool:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "OPTIX"
    prefs.get_devices()
    found = False
    for device in prefs.devices:
        device.use = device.type == "OPTIX"
        found = found or device.use
    return found


def run_in_blender(job_file: str) -> None:
    job = json.loads(Path(job_file).read_text(encoding="utf-8"))
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    s = np.load(job["scene"])
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU" if job.get("gpu") and _use_optix() else "CPU"
    scene.cycles.samples = int(job["samples"])
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.03
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 6
    scene.cycles.transmission_bounces = 4
    scene.cycles.glossy_bounces = 3
    scene.cycles.diffuse_bounces = 2
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = int(job["threads"])
    scene.render.resolution_x, scene.render.resolution_y = job["size"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    _world()
    _light("key", (-4.0, -5.0, 8.0), 9.0, 900.0)
    _light("rim", (6.0, 5.0, 5.0), 6.0, 500.0)

    road_mat = _pbr("road", (0.012, 0.013, 0.015), 0.0, 0.85)
    wall_mat = _pbr("wall", (0.022, 0.023, 0.026), 0.0, 0.8)
    tick_mat = _pbr("tick", (0.55, 0.56, 0.58), 0.0, 0.6)
    for name, mat in (("road", road_mat), ("wall", wall_mat), ("tick", tick_mat)):
        v, f = s[f"{name}_v"], s[f"{name}_f"]
        _object(name, _mesh(name, v, f.ravel(), np.arange(len(f)) * f.shape[1]), mat)

    car = bpy.data.objects.new("vehicle", None)
    scene.collection.objects.link(car)
    parts: dict[str, Any] = {}
    for key in s.files:
        if key.startswith("veh_") and key.endswith("_v"):
            g = key[4:-2]
            colour, metal, rough = MATERIALS.get("wheels" if g.startswith("wheel_") else g, MATERIALS["frame"])
            ob = _object(g, _mesh(g, s[key], s[f"veh_{g}_l"], s[f"veh_{g}_s"]), _pbr(g, colour, metal, rough))
            ob.parent = car
            parts[g] = ob

    water_mat = _water_material()
    water_ob = None
    fly_ob = None
    ball_r = 0.4 * 0.5 * float(job["dx"])  # for the picture: smaller than a particle's volume, so a cloud reads as droplets
    ball_v, ball_f = icosphere(ball_r)
    # the camera
    offset, target, lens, follow = job["camera"]
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = lens
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    aim = bpy.data.objects.new("aim", None)
    scene.collection.objects.link(aim)
    con = cam.constraints.new("TRACK_TO")
    con.target = aim
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"

    for item in job["frames"]:
        t0 = time.time()
        x, z, pitch = item["pose"]
        car.location = (x, 0.0, z)
        car.rotation_euler = (0.0, pitch, 0.0)
        for name, offset_w in item.get("wheels", {}).items():
            parts[name].location = offset_w
        if follow:
            cam.location = (x + offset[0], offset[1], offset[2] + z * 0.3)
            aim.location = (x + target[0], target[1], target[2] + z * 0.3)
        else:
            cam.location = offset
            aim.location = target
        for old in (water_ob, fly_ob):
            if old is not None:
                me = old.data
                bpy.data.objects.remove(old)
                bpy.data.meshes.remove(me)
        d = np.load(item["mesh"])
        q = d["quads"]
        foam = np.clip((d["speed"] - 2.0) / 7.0, 0.0, 1.0) ** 1.2
        me = _mesh("water", d["verts"], q.ravel(), np.arange(len(q)) * 4, smooth=True)
        attr = me.color_attributes.new("foam", "FLOAT_COLOR", "POINT")
        attr.data.foreach_set("color", np.column_stack([foam, foam, foam, np.ones(len(foam))]).astype(np.float32).ravel())
        water_ob = _object("water", me, water_mat)
        fly = d["fly"]
        fly_ob = None
        if len(fly):
            nv, nf = len(ball_v), len(ball_f)
            v = (fly[:, None, :] + ball_v[None, :, :]).reshape(-1, 3)
            f = (np.arange(len(fly))[:, None, None] * nv + ball_f[None, :, :]).reshape(-1, 3)
            me2 = _mesh("fly", v, f.ravel(), np.arange(len(f)) * 3, smooth=True)
            foam2 = np.clip((d["fly_speed"] - 1.0) / 8.0, 0.15, 0.7)
            attr2 = me2.color_attributes.new("foam", "FLOAT_COLOR", "POINT")
            c = np.repeat(foam2, nv)
            attr2.data.foreach_set("color", np.column_stack([c, c, c, np.ones(len(c))]).astype(np.float32).ravel())
            fly_ob = _object("fly", me2, water_mat)
        scene.render.filepath = item["png"]
        bpy.ops.render.render(write_still=True)
        print(f"frame {item['frame']} rendered in {time.time() - t0:.1f} s", flush=True)


if __name__ == "__main__":
    if bpy is not None:
        run_in_blender(sys.argv[sys.argv.index("--") + 1])
    else:
        main()
