"""Moving-picture material of a case: lights running along the real streamlines over a still of the opaque vehicle.

    python -m hiraku.cfd.render_video <case folder> --design <design.json> --out <folder> [--seconds 5] [--rate 0.2]
    ... --also <second case folder>      # the two side by side: the same camera, the same colour scale

Blender renders the still once (hiraku.cfd.render_blender, background mode). A Cycles render of every frame takes
minutes per frame on the CPU, so the lights are drawn onto that still frame by frame, by the same camera as Blender's.
Each light is where the stored polyline's travel time puts it: t = the integral of ds / |u| along the line, so it runs
only on real streamlines, at the local speed times --rate. A point behind a solid shape, seen from the camera, is not
drawn. The camera stands still.

The clip loops without a seam. Lights enter each line one per `spacing` seconds of flow time and leave at its end, so the
pattern repeats every `spacing`. The spacing is set to a whole share of the flow time the clip covers (seconds x rate),
so the frame after the last is the first; each light still moves at its computed speed.

Writes <out>/frames/f0001.png ... and <out>/flow.mp4 (ffmpeg).
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

FULL_FRAME_FOCAL_PX = 42.0 / 36.0 * 1920.0  # the default lens at 1920 wide: the lights' brightness is set for this picture scale


@dataclass
class Camera:
    """A pinhole camera in Blender's convention: it looks along its -z, and the sensor's width fits the picture's longer side."""

    world: np.ndarray  # 4 x 4, camera to world
    focal_px: float
    size: tuple[int, int]

    @classmethod
    def from_json(cls, path: Path) -> "Camera":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        W, H = d["resolution"]
        return cls(np.array(d["matrix_world"], float), d["lens"] / d["sensor_width"] * max(W, H), (W, H))

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


def loop_spacing(flow_time: float, wanted: float) -> float:
    """The spacing of the lights nearest `wanted` (s of flow time) that repeats exactly over the clip's flow time."""
    return flow_time / max(1, int(round(flow_time / wanted)))


def light_samples(lines: Lines, clock: float, spacing: float, tail: float, n_tail: int, air: float) -> tuple[np.ndarray, ...]:
    """Where the lights are at flow time `clock`. One light per `spacing` seconds enters each line at its start, runs along
    it by its stored time and leaves at its end, so clock + spacing shows the same lights. Its tail runs back along the
    line: `tail` metres at the air speed `air` or faster, shorter where the air is slower (down to 30 %), so slow air shows
    short lights and not a pile-up on one spot. Returns the n_tail samples' points, the speed there, how far along the
    tail (0 at the head, 1 at its end), the index of the nearest stored point, and the light's brightness (its length /
    tail: the same light per pixel)."""
    pts, spd, frac, near, gain = [], [], [], [], []
    along = np.arange(n_tail) / n_tail
    for n in range(len(lines)):
        a, b = int(lines.offsets[n]), int(lines.offsets[n + 1])
        t, v = lines.time[a:b], lines.speed[a:b]
        total = float(t[-1])
        if total <= 0.0:
            continue
        s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(lines.points[a:b], axis=0), axis=1))])
        v_end = max(float(v[-1]), 1e-3)
        leaving = min(tail * float(np.clip(v_end / air, 0.3, 1.0)) / v_end, 5.0)  # its tail still runs in after the head has left
        first = (clock + ((n * 0.618034) % 1.0) * spacing) % spacing  # lights of neighbouring lines do not run side by side
        heads = first + spacing * np.arange(max(int((total + leaving - first) / spacing) + 1, 0))
        if not heads.size:
            continue
        s_head = np.where(heads <= total, np.interp(heads, t, s), s[-1] + v_end * (heads - total))
        length = tail * np.clip(np.interp(np.minimum(heads, total), t, v) / air, 0.3, 1.0)
        at = (s_head[:, None] - length[:, None] * along[None, :]).ravel()
        keep = (at >= 0.0) & (at <= s[-1])
        at = at[keep]
        pts.append(np.column_stack([np.interp(at, s, lines.points[a:b, c]) for c in range(3)]))
        spd.append(np.interp(at, s, v))
        frac.append(np.tile(along, len(heads))[keep])
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


def lit(base: np.ndarray, camera: Camera, lines: Lines, seen: np.ndarray, vmax: float, clock: float, spacing: float, tail: float, n_tail: int, air: float) -> np.ndarray:
    """The still with the lights added onto it, 0..1."""
    from PIL import Image

    p, speed, frac, near, gain = light_samples(lines, clock, spacing, tail, n_tail, air)
    visible = seen[near]
    x, y, depth = camera.project(p[visible])
    fade = (1.0 - frac[visible]) ** 1.6 * gain[visible] * min(1.0, camera.focal_px / FULL_FRAME_FOCAL_PX)  # a smaller picture packs the same lights closer
    colour = rb.ramp(speed[visible] / vmax)
    head = (frac[visible] < 0.1)[:, None]
    colour = np.where(head, 0.45 * colour + 0.55, colour)  # the head of a light is nearly white
    colour = colour * fade[:, None]
    in_front = depth > 0.0
    W, H = camera.size
    core = splat(camera.size, x[in_front], y[in_front], colour[in_front] * 0.55, 1.1)
    small = (W // 4, H // 4)
    glow_small = splat(small, x[in_front] / 4.0, y[in_front] / 4.0, colour[in_front] * 0.06, 1.3)
    glow = np.stack([np.asarray(Image.fromarray(glow_small[:, :, c], mode="F").resize((W, H), Image.BICUBIC)) for c in range(3)], axis=2)
    return 1.0 - (1.0 - base) * np.exp(-(core + glow))  # where lights pile up they brighten softly and do not burn out flat


def covered(frame: np.ndarray, overlay: np.ndarray) -> np.ndarray:
    """The caption over the frame."""
    a = overlay[:, :, 3:4]
    return frame * (1.0 - a) + overlay[:, :, :3] * a


def seam_report(frames_dir: Path, count: int) -> tuple[float, float]:
    """The mean change per pixel (of 255) from the last frame to the first, and between two frames in the middle."""
    from PIL import Image

    def load(k: int) -> np.ndarray:
        return np.asarray(Image.open(frames_dir / f"f{k:04d}.png").convert("RGB"), np.float32)

    return float(np.abs(load(count) - load(1)).mean()), float(np.abs(load(count // 2) - load(count // 2 + 1)).mean())


def encode(frames_dir: Path, mp4: Path, fps: int, threads: int) -> None:
    """The frames as an mp4. Every frame at the same quality (no better first frame, no lookahead), so the jump from the
    last frame to the first does not show as a change of quality when the clip loops."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg is not on PATH")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(Path(frames_dir) / "f%04d.png"), "-c:v", "libx264", "-threads", str(threads), "-crf", "12", "-preset", "medium",
                    "-x264-params", "ipratio=1.0:pbratio=1.0:mbtree=0", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True)


def main(argv: list[str] | None = None) -> Path:
    from PIL import Image

    ap = argparse.ArgumentParser(prog="python -m hiraku.cfd.render_video", description="Lights along a case's real streamlines, as frames and an mp4")
    rb.add_arguments(ap)
    ap.add_argument("--also", type=Path, help="a second case: the two side by side, the same camera and colour scale")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--rate", type=float, default=0.2, help="flow time per second of film")
    ap.add_argument("--every", type=float, default=0.6, help="about one light per this much flow time on a line, s (set to what loops)")
    ap.add_argument("--tail", type=float, default=0.5, help="a light's tail where the air runs at the air speed or faster, m")
    ap.add_argument("--line-strength", type=float, default=0.55, help="brightness of the still's lines under the lights")
    ap.add_argument("--reuse-still", action="store_true", help="use the stills already in the folder")
    ap.add_argument("--frames-only", action="store_true")
    ap.add_argument("--encode-only", action="store_true", help="only make the mp4 from the frames already in the folder")
    a = ap.parse_args(argv)
    rb.wait_for_ram(a.min_free_gb)  # also when the stills are reused: the frames are a render job too
    t0 = time.perf_counter()
    out = Path(a.out)
    if a.encode_only:
        encode(out / "frames", out / "flow.mp4", a.fps, a.threads)
        return out / "flow.mp4"
    (out / "frames").mkdir(parents=True, exist_ok=True)
    cases = [a.case] + ([a.also] if a.also else [])
    W, H = a.resolution
    panels = []
    for case in cases:
        one = argparse.Namespace(**vars(a))
        one.case, one.resolution = case, (W // len(cases), H)
        panels.append((one, rb.prepare(one)))
    vmax, tick = rb.nice_scale(a.vmax if a.vmax else max(float(np.percentile(p["lines"].speed, 99.8)) for _, p in panels))
    bases, blocks, drawn = [], [], []
    for n, (one, p) in enumerate(panels):
        p["vmax"], p["tick"] = vmax, tick
        still = out / f"still{n}.png"
        if not (a.reuse_still and still.is_file()):
            rb.render(one, p, still, a.line_strength)
        print(f"still {n} ready, {time.perf_counter() - t0:.0f} s", flush=True)
        camera = Camera.from_json(still.with_suffix(".camera.json"))
        seen = seen_from(camera.eye, p["lines"].points, p["shapes"])
        bases.append(np.asarray(Image.open(still).convert("RGB"), np.float32) / 255.0)
        drawn.append((camera, p["lines"], seen, float(p["summary"]["air_speed_ms"])))
        s = p["summary"]
        words = ([f"横風 {rb.yaw_words(float(s['yaw_deg']))}", f"v{s['design_version']} {s['speed_ms'] * 3.6:.0f} km/h 格子 {s['dx'] * 1000:.0f} mm"] if len(cases) > 1
                 else [*p["text"], f"光の動きは計算した速さの {a.rate:g} 倍"])
        blocks.append((words, a.status))
    overlay = np.asarray(rb.caption_overlay((W, H), vmax, tick, blocks), np.float32) / 255.0
    frames = int(round(a.seconds * a.fps))
    flow_time = frames / a.fps * a.rate
    spacing = loop_spacing(flow_time, a.every)
    print(f"{frames} frames, {flow_time:g} s of flow time, a light per {spacing:g} s of it", flush=True)
    for f in range(frames):
        clock = f / frames * flow_time
        parts = [lit(base, camera, lines, seen, vmax, clock, spacing, a.tail, 40, air) for base, (camera, lines, seen, air) in zip(bases, drawn)]
        im = covered(np.hstack(parts), overlay)
        Image.fromarray((im * 255.0 + 0.5).astype(np.uint8)).save(out / "frames" / f"f{f + 1:04d}.png", compress_level=1)
        if f % 30 == 0:
            print(f"frame {f + 1} / {frames}, {time.perf_counter() - t0:.0f} s", flush=True)
    seam, step = seam_report(out / "frames", frames)
    print(f"mean change per pixel: last frame to first {seam:.3f}, two frames in the middle {step:.3f}", flush=True)
    mp4 = out / "flow.mp4"
    if not a.frames_only:
        encode(out / "frames", mp4, a.fps, a.threads)
    print(f"wrote {frames} frames and {mp4.name} in {time.perf_counter() - t0:.0f} s")
    return mp4


if __name__ == "__main__":
    main()
