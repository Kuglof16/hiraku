"""Export a screen frame by frame and join the frames with ffmpeg.

The screen is opened in headless Chromium with a 1920x1080 layout at device
scale 2 (3840x2160 pixels), moved to each frame time, and captured as PNG.
A screen is a promo page (``window.promo.seek(t)``, see screens/timeline.js)
or the app's replay at an http URL (``window.hiraku.seek(t)``, web/js/app.js).
The export runs from 0 to the screen's own length (``window.promo.duration``
or ``window.hiraku.player.duration``) unless --end is given. The app replays on
paced display time by default (short gaps stretched, long ones cut); add
``&pace=0`` to its URL to export on the recorded times instead.

    py promo/export.py stills promo/screens/n18.html OUT_DIR --at 0.5 3 8
    py promo/export.py frames promo/screens/n18.html OUT_DIR --fps 30
    py promo/export.py frames "http://127.0.0.1:8765/?run=sample&video=1" OUT_DIR --gpu
    py promo/export.py frames "http://127.0.0.1:8765/?run=sample&video=1&pace=0" OUT_DIR --gpu
    py promo/export.py encode OUT_DIR clip.mp4 --fps 30 --height 1080
    py promo/export.py clip   promo/screens/n18.html clip.mp4 --frames-dir OUT_DIR

``--gpu`` draws WebGL on the graphics card (ANGLE on Direct3D 11); without
it headless Chromium draws WebGL in software (SwiftShader), about half as fast.

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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DESIGN_W, DESIGN_H = 1920, 1080
FRAME_PATTERN = "%05d.png"
GPU_ARGS = ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"]

# Video encoders. auto: h264_nvenc (the graphics card) when ffmpeg has it and a trial
# encode works, else libx264. NVENC runs in constant-quality VBR; -cq = x264 CRF +
# NVENC_CQ_OFFSET gave an SSIM/PSNR to the lossless source at least as high as libx264
# on the measured clips (promo/ASSEMBLE.md, "GPU").
ENCODERS = ("auto", "nvenc", "x264")
NVENC_PRESET = "p7"
NVENC_LOOKAHEAD = 32
NVENC_CQ_OFFSET = 0
# Without a maxrate, VBR with -b:v 0 held the grain cuts to about 17 Mb/s whatever the -cq
# (cq 8-14 gave the same 11.9 MB file). 240 Mb/s is a ceiling well above the measured
# grain cut at cq 10 (59.3 MB over 5.53 s, about 86 Mb/s); its relation to H.264 level
# limits has not been checked.
NVENC_MAXRATE = "240M"
NVENC_BUFSIZE = "480M"

# One interface over the two kinds of screen, installed as window.__export.
ADAPTER = """() => {
  if (window.promo) window.__export = {
    ready: () => window.promo.ready(),
    seek: (t) => window.promo.seek(t),
    duration: () => window.promo.duration,
  };
  else window.__export = {
    ready: () => window.hiraku.ready,
    seek: (t) => window.hiraku.seek(t),
    duration: () => window.hiraku.player.duration,
  };
}"""


def frame_times(start: float, end: float, fps: int) -> list[float]:
    """Times of the frames in [start, end): frame i is at start + i / fps."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    if end < start:
        raise ValueError("end is before start")
    count = round((end - start) * fps)
    return [start + i / fps for i in range(count)]


def is_url(screen: str | Path) -> bool:
    return str(screen).startswith(("http://", "https://"))


def screen_url(screen: str | Path, t: float | None = None, layer: str | None = None,
               guides: bool = False) -> str:
    """A file path or an http URL, with the export parameters added to its query."""
    query = {}
    if t is not None:
        query["t"] = f"{t:.6f}"
    if layer:
        query["layer"] = layer
    if guides:
        query["guides"] = "1"
    if is_url(screen):
        parts = urlsplit(str(screen))
        merged = dict(parse_qsl(parts.query, keep_blank_values=True)) | query
        return urlunsplit(parts._replace(query=urlencode(merged)))
    url = Path(screen).resolve().as_uri()
    return url + ("?" + urlencode(query) if query else "")


def screen_name(screen: str | Path) -> str:
    """Short name for file names: the page's stem, or 'app' for the app's root URL."""
    path = urlsplit(str(screen)).path if is_url(screen) else str(screen)
    return Path(path).stem or "app"


def frame_name(index: int) -> str:
    return FRAME_PATTERN % index


def ffmpeg_path() -> str:
    found = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not found:
        raise SystemExit("ffmpeg not found: put it on PATH or set FFMPEG")
    return found


def nvenc_available(ffmpeg: str = "ffmpeg") -> bool:
    """True when ffmpeg lists h264_nvenc and a short trial encode with it succeeds."""
    if ffmpeg in _NVENC_OK:
        return _NVENC_OK[ffmpeg]
    ok = False
    try:
        listed = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                                text=True, timeout=60).stdout
        if " h264_nvenc " in listed:
            trial = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "testsrc2=s=256x144:r=30:d=0.2", *nvenc_args(20), "-f", "null", "-"],
                capture_output=True, timeout=60)
            ok = trial.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    _NVENC_OK[ffmpeg] = ok
    return ok


_NVENC_OK: dict[str, bool] = {}


def pick_encoder(choice: str, ffmpeg: str = "ffmpeg", available=nvenc_available) -> str:
    """'nvenc' or 'x264' for an --encoder choice. auto takes nvenc when it works, else x264."""
    if choice not in ENCODERS:
        raise ValueError(f"unknown encoder {choice!r}: use one of {', '.join(ENCODERS)}")
    if choice == "x264":
        return "x264"
    if available(ffmpeg):
        return "nvenc"
    if choice == "nvenc":
        raise SystemExit("h264_nvenc is not usable with this ffmpeg or GPU: use --encoder x264 or auto")
    return "x264"


def nvenc_args(cq: float) -> list[str]:
    """h264_nvenc in constant-quality VBR mode (see NVENC_* above for how the values were chosen)."""
    return ["-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-tune", "hq", "-rc", "vbr",
            "-cq", f"{cq:g}", "-b:v", "0", "-maxrate", NVENC_MAXRATE, "-bufsize", NVENC_BUFSIZE,
            "-spatial-aq", "1", "-temporal-aq", "1",
            "-rc-lookahead", str(NVENC_LOOKAHEAD), "-bf", "3", "-b_ref_mode", "middle",
            "-profile:v", "high"]


def nvenc_cq(crf: float) -> float:
    """The NVENC -cq that matched libx264 at this CRF on the measured clips (promo/ASSEMBLE.md)."""
    return max(1.0, crf + NVENC_CQ_OFFSET)


def video_args(encoder: str, crf: float, x264_preset: str, tune: str | None = None) -> list[str]:
    """Codec arguments: libx264 at crf/preset (tune), or h264_nvenc at the matching -cq."""
    if encoder == "nvenc":
        return nvenc_args(nvenc_cq(crf))
    out = ["-c:v", "libx264", "-preset", x264_preset, "-crf", str(crf)]
    return out + (["-tune", tune] if tune else [])


def encode_command(frames_dir: Path, out: Path, fps: int, height: int,
                   crf: int = 14, ffmpeg: str = "ffmpeg", encoder: str = "x264") -> list[str]:
    """ffmpeg arguments that join numbered PNG frames into an H.264 file.

    Frames are scaled to ``height`` (16:9) with Lanczos, so frames captured
    at 3840x2160 give a supersampled 1080p clip or a 1:1 4K clip.
    ``encoder`` is 'x264' or 'nvenc' (resolved already, see pick_encoder).
    """
    width = height * DESIGN_W // DESIGN_H
    vf = (f"scale={width}:{height}:flags=lanczos+accurate_rnd+full_chroma_int"
          ":out_color_matrix=bt709:out_range=tv,format=yuv420p")
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / FRAME_PATTERN),
        "-vf", vf,
        *video_args(encoder, crf, "slow", "animation"),
        *(["-pix_fmt", "yuv420p"] if encoder == "nvenc" else []),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-movflags", "+faststart",
        str(out),
    ]


class Capture:
    """One headless Chromium page showing one screen."""

    def __init__(self, screen: str | Path, scale: float = 2.0, size: tuple[int, int] = (DESIGN_W, DESIGN_H),
                 layer: str | None = None, guides: bool = False, gpu: bool = False, inject: Path | None = None):
        from playwright.sync_api import sync_playwright  # only needed here

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(args=GPU_ARGS if gpu else [])
        self.page = self._browser.new_page(
            viewport={"width": size[0], "height": size[1]},
            device_scale_factor=scale,
        )
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.transparent = layer == "fg"
        self.page.goto(screen_url(screen, t=0, layer=layer, guides=guides))
        self.page.wait_for_function("window.promo !== undefined || window.hiraku !== undefined",
                                    timeout=60_000)
        self.page.evaluate(ADAPTER)
        self.page.evaluate("window.__export.ready()")
        self.duration = float(self.page.evaluate("window.__export.duration()"))
        # filming-side changes (CSS/JS); window.__inject(t) runs after every seek
        self.inject = bool(inject)
        if inject:
            self.page.evaluate(Path(inject).read_text(encoding="utf-8"))

    def shoot(self, t: float, path: Path) -> None:
        self.page.evaluate("t => window.__export.seek(t)", t)
        if self.inject:
            self.page.evaluate("t => window.__inject && window.__inject(t)", t)
        self.page.screenshot(path=str(path), type="png", omit_background=self.transparent)
        if self.errors:
            raise RuntimeError(f"page error at t={t:.3f}: {self.errors[0]}")

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()


def export_frames(screen: str | Path, out_dir: Path, fps: int, scale: float, start: float,
                  end: float | None, layer: str | None, size: tuple[int, int], gpu: bool = False,
                  inject: Path | None = None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    cap = Capture(screen, scale=scale, size=size, layer=layer, gpu=gpu, inject=inject)
    try:
        stop = cap.duration if end is None else end
        times = frame_times(start, stop, fps)
        print(f"{screen_name(screen)}: lasts {cap.duration:.2f} s; exporting {start:.2f}-{stop:.2f} s", flush=True)
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


def export_stills(screen: str | Path, out_dir: Path, at: list[float], scale: float,
                  guides: bool, size: tuple[int, int], gpu: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = Capture(screen, scale=scale, size=size, guides=guides, gpu=gpu)
    paths = []
    try:
        for t in at:
            p = out_dir / f"{screen_name(screen)}_t{t:06.2f}.png"
            cap.shoot(t, p)
            paths.append(p)
    finally:
        cap.close()
    return paths


def encode(frames_dir: Path, out: Path, fps: int, height: int, crf: int, encoder: str = "auto") -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg_path()
    enc = pick_encoder(encoder, ff)
    cmd = encode_command(frames_dir, out, fps, height, crf, ffmpeg=ff, encoder=enc)
    subprocess.run(cmd, check=True)
    print(f"wrote {out} ({enc})")


def encoder_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--encoder", choices=ENCODERS, default="auto",
                   help="auto: h264_nvenc when it works, else libx264")


def parse_size(text: str) -> tuple[int, int]:
    w, h = text.lower().split("x")
    return int(w), int(h)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def capture_args(p):
        p.add_argument("--scale", type=float, default=2.0, help="device scale factor (2 -> 3840x2160)")
        p.add_argument("--size", type=parse_size, default=(DESIGN_W, DESIGN_H), help="window size in CSS px")
        p.add_argument("--gpu", action="store_true", help="draw WebGL on the graphics card")

    def screen_arg(text: str) -> str | Path:
        return text if is_url(text) else Path(text)

    p = sub.add_parser("stills", help="capture a few frames")
    p.add_argument("screen", type=screen_arg)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--at", type=float, nargs="+", required=True)
    p.add_argument("--guides", action="store_true")
    capture_args(p)

    p = sub.add_parser("frames", help="capture every frame")
    p.add_argument("screen", type=screen_arg)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--end", type=float, default=None)
    p.add_argument("--layer", choices=["fg"], default=None, help="fg: transparent ground")
    p.add_argument("--inject", type=Path, default=None, help="JS run after load; window.__inject(t) after each seek")
    capture_args(p)

    p = sub.add_parser("encode", help="join frames into an mp4")
    p.add_argument("frames_dir", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--crf", type=int, default=14)
    encoder_arg(p)

    p = sub.add_parser("clip", help="frames + encode")
    p.add_argument("screen", type=screen_arg)
    p.add_argument("out", type=Path)
    p.add_argument("--frames-dir", type=Path, required=True)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--crf", type=int, default=14)
    encoder_arg(p)
    capture_args(p)

    a = ap.parse_args(argv)
    if a.cmd == "stills":
        for path in export_stills(a.screen, a.out_dir, a.at, a.scale, a.guides, a.size, a.gpu):
            print(path)
    elif a.cmd == "frames":
        export_frames(a.screen, a.out_dir, a.fps, a.scale, a.start, a.end, a.layer, a.size, a.gpu, a.inject)
    elif a.cmd == "encode":
        encode(a.frames_dir, a.out, a.fps, a.height, a.crf, a.encoder)
    elif a.cmd == "clip":
        export_frames(a.screen, a.frames_dir, a.fps, a.scale, 0.0, None, None, a.size, a.gpu)
        encode(a.frames_dir, a.out, a.fps, a.height, a.crf, a.encoder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
