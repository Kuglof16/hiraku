"""Export a promo screen frame by frame and join the frames with ffmpeg.

The screen is opened in headless Chromium with a 1920x1080 layout at device
scale 2 (3840x2160 pixels), moved to each frame time with
``window.promo.seek(t)`` (see screens/timeline.js), and captured as PNG.

    py promo/export.py stills promo/screens/n18.html OUT_DIR --at 0.5 3 8
    py promo/export.py frames promo/screens/n18.html OUT_DIR --fps 30
    py promo/export.py encode OUT_DIR clip.mp4 --fps 30 --height 1080
    py promo/export.py clip   promo/screens/n18.html clip.mp4 --frames-dir OUT_DIR

Needs the ``playwright`` package with its Chromium (frames, stills, clip)
and ffmpeg on PATH or in the FFMPEG environment variable (encode, clip).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

DESIGN_W, DESIGN_H = 1920, 1080
FRAME_PATTERN = "%05d.png"


def frame_times(start: float, end: float, fps: int) -> list[float]:
    """Times of the frames in [start, end): frame i is at start + i / fps."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    if end < start:
        raise ValueError("end is before start")
    count = round((end - start) * fps)
    return [start + i / fps for i in range(count)]


def screen_url(screen: Path, t: float | None = None, layer: str | None = None,
               guides: bool = False) -> str:
    query = {}
    if t is not None:
        query["t"] = f"{t:.6f}"
    if layer:
        query["layer"] = layer
    if guides:
        query["guides"] = "1"
    url = screen.resolve().as_uri()
    return url + ("?" + urlencode(query) if query else "")


def frame_name(index: int) -> str:
    return FRAME_PATTERN % index


def ffmpeg_path() -> str:
    found = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not found:
        raise SystemExit("ffmpeg not found: put it on PATH or set FFMPEG")
    return found


def encode_command(frames_dir: Path, out: Path, fps: int, height: int,
                   crf: int = 14, ffmpeg: str = "ffmpeg") -> list[str]:
    """ffmpeg arguments that join numbered PNG frames into an H.264 file.

    Frames are scaled to ``height`` (16:9) with Lanczos, so frames captured
    at 3840x2160 give a supersampled 1080p clip or a 1:1 4K clip.
    """
    width = height * DESIGN_W // DESIGN_H
    vf = (f"scale={width}:{height}:flags=lanczos+accurate_rnd+full_chroma_int"
          ":out_color_matrix=bt709:out_range=tv,format=yuv420p")
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / FRAME_PATTERN),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
        "-tune", "animation",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-movflags", "+faststart",
        str(out),
    ]


class Capture:
    """One headless Chromium page showing one screen."""

    def __init__(self, screen: Path, scale: float = 2.0, size: tuple[int, int] = (DESIGN_W, DESIGN_H),
                 layer: str | None = None, guides: bool = False):
        from playwright.sync_api import sync_playwright  # only needed here

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self.page = self._browser.new_page(
            viewport={"width": size[0], "height": size[1]},
            device_scale_factor=scale,
        )
        self.transparent = layer == "fg"
        self.page.goto(screen_url(screen, t=0, layer=layer, guides=guides))
        self.page.wait_for_function("window.promo !== undefined")
        self.page.evaluate("window.promo.ready()")
        self.duration = float(self.page.evaluate("window.promo.duration"))

    def shoot(self, t: float, path: Path) -> None:
        self.page.evaluate("t => window.promo.seek(t)", t)
        self.page.screenshot(path=str(path), type="png", omit_background=self.transparent)

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()


def export_frames(screen: Path, out_dir: Path, fps: int, scale: float, start: float,
                  end: float | None, layer: str | None, size: tuple[int, int]) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    cap = Capture(screen, scale=scale, size=size, layer=layer)
    try:
        stop = cap.duration if end is None else end
        times = frame_times(start, stop, fps)
        began = time.perf_counter()
        for i, t in enumerate(times):
            cap.shoot(t, out_dir / frame_name(i))
            if i % fps == 0:
                print(f"  frame {i}/{len(times)}  t={t:.3f}s", flush=True)
        spent = time.perf_counter() - began
        print(f"{len(times)} frames in {spent:.1f} s ({spent / max(1, len(times)):.3f} s/frame)")
        return len(times)
    finally:
        cap.close()


def export_stills(screen: Path, out_dir: Path, at: list[float], scale: float,
                  guides: bool, size: tuple[int, int]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = Capture(screen, scale=scale, size=size, guides=guides)
    paths = []
    try:
        for t in at:
            p = out_dir / f"{screen.stem}_t{t:06.2f}.png"
            cap.shoot(t, p)
            paths.append(p)
    finally:
        cap.close()
    return paths


def encode(frames_dir: Path, out: Path, fps: int, height: int, crf: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = encode_command(frames_dir, out, fps, height, crf, ffmpeg=ffmpeg_path())
    subprocess.run(cmd, check=True)
    print(f"wrote {out}")


def parse_size(text: str) -> tuple[int, int]:
    w, h = text.lower().split("x")
    return int(w), int(h)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def capture_args(p):
        p.add_argument("--scale", type=float, default=2.0, help="device scale factor (2 -> 3840x2160)")
        p.add_argument("--size", type=parse_size, default=(DESIGN_W, DESIGN_H), help="window size in CSS px")

    p = sub.add_parser("stills", help="capture a few frames")
    p.add_argument("screen", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--at", type=float, nargs="+", required=True)
    p.add_argument("--guides", action="store_true")
    capture_args(p)

    p = sub.add_parser("frames", help="capture every frame")
    p.add_argument("screen", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--end", type=float, default=None)
    p.add_argument("--layer", choices=["fg"], default=None, help="fg: transparent ground")
    capture_args(p)

    p = sub.add_parser("encode", help="join frames into an mp4")
    p.add_argument("frames_dir", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--crf", type=int, default=14)

    p = sub.add_parser("clip", help="frames + encode")
    p.add_argument("screen", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--frames-dir", type=Path, required=True)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--crf", type=int, default=14)
    capture_args(p)

    a = ap.parse_args(argv)
    if a.cmd == "stills":
        for path in export_stills(a.screen, a.out_dir, a.at, a.scale, a.guides, a.size):
            print(path)
    elif a.cmd == "frames":
        export_frames(a.screen, a.out_dir, a.fps, a.scale, a.start, a.end, a.layer, a.size)
    elif a.cmd == "encode":
        encode(a.frames_dir, a.out, a.fps, a.height, a.crf)
    elif a.cmd == "clip":
        export_frames(a.screen, a.frames_dir, a.fps, a.scale, 0.0, None, None, a.size)
        encode(a.frames_dir, a.out, a.fps, a.height, a.crf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
