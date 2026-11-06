"""Moving-picture material of a case: lights running along the real streamlines over a still of the opaque vehicle.

    python -m hiraku.cfd.render_video <case folder> --design <design.json> --out <folder> [--seconds 8] [--rate 0.2]

Blender renders the still once (hiraku.cfd.render_blender, background mode). A Cycles render of every frame takes
minutes per frame on the CPU, so the lights are drawn onto that still frame by frame, by the same camera as Blender's.
Each light is where the stored polyline's travel time puts it: t = the integral of ds / |u| along the line, so it runs
only on real streamlines, at the local speed times --rate. A point behind a solid shape, seen from the camera, is not
drawn. The camera stands still. Writes <out>/frames/f0001.png ... and <out>/flow.mp4 (ffmpeg).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hiraku.cfd import render_blender as rb  # first: it caps numpy's threads before numpy loads

import numpy as np

from hiraku.cfd.streamlines import Lines, ShapeStop


@dataclass
class Camera:
    """A pinhole camera in Blender's convention: it looks along its -z, and the sensor's width fits the picture's."""

    world: np.ndarray  # 4 x 4, camera to world
    focal_px: float
    size: tuple[int, int]

    @classmethod
    def from_json(cls, path: Path) -> "Camera":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        W, H = d["resolution"]
        return cls(np.array(d["matrix_world"], float), d["lens"] / d["sensor_width"] * W, (W, H))

    @property
    def eye(self) -> np.ndarray:
        return self.world[:3, 3]

    def project(self, p: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Pixel x, pixel y (from the top left) and the distance in front of the camera."""
        q = (p - self.eye) @ self.world[:3, :3]
        depth = -q[:, 2]
        safe = np.where(depth > 1e-6, depth, 1e-6)
        W, H = self.size
        return W / 2.0 + self.focal_px * q[:, 0] / safe, H / 2.0 - self.focal_px * q[:, 1] / safe, depth


def seen_from(eye: np.ndarray, points: np.ndarray, shapes: list[Any], chunk: int = 4000) -> np.ndarray:
    """Whether each point can be seen from the eye: the straight line to it meets no solid shape."""
    stop = ShapeStop(shapes)
    out = np.ones(len(points), bool)
    for s in range(0, len(points), chunk):
        p = points[s:s + chunk]
        out[s:s + chunk] = ~stop(np.tile(eye, (len(p), 1)), p)
    return out


def light_samples(lines: Lines, clock: float, every: float, tail: float, n_tail: int, air: float) -> tuple[np.ndarray, ...]:
    """Where the lights are at flow time `clock`: per line one light per `every` seconds of its travel time. The head is
    where the line's stored time puts it. Its tail runs back along the line: `tail` metres at the air speed `air` or
    faster, shorter where the air is slower (down to 30 %), so slow air shows short lights and not a pile-up on one spot.
    Returns the n_tail samples' points, the speed there, how far along the tail (0 at the head, 1 at its end), the
    index of the nearest stored point, and the light's brightness (its length / tail: the same light per pixel)."""
    pts, spd, frac, near, gain = [], [], [], [], []
    along = np.arange(n_tail) / n_tail
    for n in range(len(lines)):
        a, b = int(lines.offsets[n]), int(lines.offsets[n + 1])
        t = lines.time[a:b]
        total = float(t[-1])
        if total <= 0.0:
            continue
        s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(lines.points[a:b], axis=0), axis=1))])
        k = max(1, int(total / every))
        phase = (n * 0.618034) % 1.0  # lights of neighbouring lines do not run side by side
        heads = (clock + (phase + np.arange(k)) / k * total) % total
        length = tail * np.clip(np.interp(heads, t, lines.speed[a:b]) / air, 0.3, 1.0)
        at = (np.interp(heads, t, s)[:, None] - length[:, None] * along[None, :]).ravel()
        keep = at >= 0.0
        at = at[keep]
        pts.append(np.column_stack([np.interp(at, s, lines.points[a:b, c]) for c in range(3)]))
        spd.append(np.interp(at, s, lines.speed[a:b]))
        frac.append(np.tile(along, k)[keep])
        near.append(a + np.minimum(np.searchsorted(s, at), b - a - 1))
        gain.append(np.repeat(length / tail, n_tail)[keep])
    if not pts:
        return np.zeros((0, 3)), np.zeros(0), np.zeros(0), np.zeros(0, np.int64), np.zeros(0)
    return np.concatenate(pts), np.concatenate(spd), np.concatenate(frac), np.concatenate(near), np.concatenate(gain)


def splat(size: tuple[int, int], x: np.ndarray, y: np.ndarray, colour: np.ndarray, sigma: float) -> np.ndarray:
    """A layer (H, W, 3) with a small Gaussian spot of each colour at each (x, y); spots add up."""
    W, H = size
    layer = np.zeros((H, W, 3), np.float32)
    r = int(np.ceil(2.0 * sigma))
    inside = (x > -r) & (x < W + r) & (y > -r) & (y < H + r)
    x, y, colour = x[inside], y[inside], colour[inside]
    if not len(x):
        return layer
    grid = np.arange(-r, r + 1)
    ix, iy = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    gx = np.exp(-((ix[:, None] + grid[None, :] - x[:, None]) ** 2) / (2.0 * sigma ** 2))
    gy = np.exp(-((iy[:, None] + grid[None, :] - y[:, None]) ** 2) / (2.0 * sigma ** 2))
    u = (ix[:, None, None] + grid[None, None, :]).repeat(len(grid), axis=1)
    v = (iy[:, None, None] + grid[None, :, None]).repeat(len(grid), axis=2)
    w = gy[:, :, None] * gx[:, None, :]
    ok = (u >= 0) & (u < W) & (v >= 0) & (v < H)
    flat, wts = (v[ok] * W + u[ok]), w[ok]
    colour_of = np.broadcast_to(colour[:, None, None, :], (*w.shape, 3))[ok]
    for c in range(3):
        layer[:, :, c] = np.bincount(flat, weights=wts * colour_of[:, c], minlength=W * H).reshape(H, W)
    return layer


def frame_of(base: np.ndarray, overlay: np.ndarray, camera: Camera, lines: Lines, seen: np.ndarray, vmax: float, clock: float, every: float, tail: float, n_tail: int, air: float) -> np.ndarray:
    """One frame, 0..1: the still, the lights added onto it, the caption over all."""
    from PIL import Image

    p, speed, frac, near, gain = light_samples(lines, clock, every, tail, n_tail, air)
    visible = seen[near]
    x, y, depth = camera.project(p[visible])
    fade = (1.0 - frac[visible]) ** 1.6 * gain[visible]
    colour = rb.ramp(speed[visible] / vmax)
    head = (frac[visible] < 0.1)[:, None]
    colour = np.where(head, 0.45 * colour + 0.55, colour)  # the head of a light is nearly white
    colour = colour * fade[:, None]
    in_front = depth > 0.0
    W, H = camera.size
    core = splat(camera.size, x[in_front], y[in_front], colour[in_front] * 0.42, 1.1)
    small = (W // 4, H // 4)
    glow_small = splat(small, x[in_front] / 4.0, y[in_front] / 4.0, colour[in_front] * 0.05, 1.3)
    glow = np.stack([np.asarray(Image.fromarray(glow_small[:, :, c], mode="F").resize((W, H), Image.BICUBIC)) for c in range(3)], axis=2)
    out = np.clip(base + core + glow, 0.0, 1.0)
    a = overlay[:, :, 3:4]
    return out * (1.0 - a) + overlay[:, :, :3] * a


def main(argv: list[str] | None = None) -> Path:
    from PIL import Image

    ap = argparse.ArgumentParser(prog="python -m hiraku.cfd.render_video", description="Lights along a case's real streamlines, as frames and an mp4")
    rb.add_arguments(ap)
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--rate", type=float, default=0.2, help="flow time per second of film")
    ap.add_argument("--every", type=float, default=0.6, help="a light per this much travel time of a line, s")
    ap.add_argument("--tail", type=float, default=0.5, help="a light's tail where the air runs at the air speed or faster, m")
    ap.add_argument("--line-strength", type=float, default=0.55, help="brightness of the still's lines under the lights")
    ap.add_argument("--reuse-still", action="store_true", help="use the still already in the folder")
    ap.add_argument("--frames-only", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    out = Path(a.out)
    (out / "frames").mkdir(parents=True, exist_ok=True)
    still = out / "still.png"
    rb.wait_for_ram(a.min_free_gb)
    p = rb.prepare(a)
    if not (a.reuse_still and still.is_file()):
        rb.render(a, p, still, a.line_strength)
    print(f"still ready, {time.perf_counter() - t0:.0f} s")
    camera = Camera.from_json(still.with_suffix(".camera.json"))
    lines: Lines = p["lines"]
    seen = seen_from(camera.eye, lines.points, p["shapes"])
    print(f"{(~seen).sum()} of {len(seen)} line points are behind the vehicle, {time.perf_counter() - t0:.0f} s")
    base = np.asarray(Image.open(still).convert("RGB"), np.float32) / 255.0
    words = [*p["text"], f"光は流線の上を、各点の速さから求めた到達時間で動く (実時間の {a.rate:g} 倍)"]
    overlay = np.asarray(rb.caption_overlay(camera.size, p["vmax"], p["tick"], words, a.status), np.float32) / 255.0
    frames = int(round(a.seconds * a.fps))
    n_tail = 40
    for f in range(frames):
        clock = f / a.fps * a.rate
        im = frame_of(base, overlay, camera, lines, seen, p["vmax"], clock, a.every, a.tail, n_tail, float(p["summary"]["air_speed_ms"]))
        Image.fromarray((im * 255.0 + 0.5).astype(np.uint8)).save(out / "frames" / f"f{f + 1:04d}.png", compress_level=1)
        if f % 30 == 0:
            print(f"frame {f + 1} / {frames}, {time.perf_counter() - t0:.0f} s", flush=True)
    mp4 = out / "flow.mp4"
    if not a.frames_only:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise FileNotFoundError("ffmpeg is not on PATH")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(a.fps), "-i", str(out / "frames" / "f%04d.png"), "-c:v", "libx264", "-threads", str(a.threads), "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(mp4)], check=True)
    print(f"wrote {frames} frames and {mp4.name} in {time.perf_counter() - t0:.0f} s")
    return mp4


if __name__ == "__main__":
    main()
