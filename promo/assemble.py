"""Join the demo video's cuts into one film, with soft joins and a film look.

The narration script's slots are the timeline. An assembly table (JSON, see
promo/ASSEMBLE.md) gives each cut its picture (a made clip, a still, or a text
card), the join into the next cut, and the look layers of the cut.

    py promo/assemble.py build TABLE.json OUT.mp4 --work DIR [--height 1080|2160]
                               [--subs SUBS.ass] [--audio MIX.wav]
    py promo/assemble.py plan  TABLE.json --work DIR      # print the timeline only

Joins (each boundary): ``cut`` (hard), ``dissolve`` (cross-fade), ``light``
(warm light swells, the next cut comes out of it), ``grain`` (grain rises, the
picture breaks up like sand and the next one assembles from it). A join of
length d is centred on the slot boundary: it starts d/2 before and ends d/2
after, so the total length and the time of every voice line and subtitle stay
where the script puts them. d is rounded to an even number of frames.

Look layers (each cut): ``grain`` (film grain, seeded from the cut's first
frame number, so the same input gives the same pixels) and ``bloom`` (bright
parts glow).

Each cut is rendered to its own file first (with the padding its joins need),
the files are joined with ``xfade``/``concat``, then the subtitles and the audio
go on with ``promo/subs.py``'s burn command. Needs ffmpeg on PATH or in FFMPEG.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from promo.export import ffmpeg_path
    from promo.subs import burn_command
    from promo.voice import cuts_in_order, parse_script
except ImportError:  # run as a script: promo/ is on sys.path
    from export import ffmpeg_path
    from subs import burn_command
    from voice import cuts_in_order, parse_script

DESIGN_W, DESIGN_H = 1920, 1080
JOIN_KINDS = ("cut", "dissolve", "light", "grain")
LIGHT_RGB = "f6ead0"          # warm light of the light join
GRAY = "808080"               # neutral layer the grain is drawn on

# Look constants, in 1080p pixels (scaled with the output height).
GRAIN_SIGMA = 0.7             # blur of the grain: film grain is a little larger than a pixel
GRAIN_NOISE = 70              # noise filter strength on the gray layer
GRAIN_LOOK = 0.30             # grain opacity of the grain look layer
GRAIN_JOIN = 0.22             # extra grain opacity at the middle of a grain join
GRAIN_JOIN_NOISE = 80         # the join's grain is coarser: its own layer, blurred more
GRAIN_JOIN_SIGMA = 1.0
SAND_PATCH_PX = 90            # patches in which the grain join's break-up spreads
SAND_PX = 2                   # size of a sand grain at the edge of a patch
SAND_SOFT = 0.25              # part of the join over which one pixel fades
BLOOM_THRESHOLD = 150         # 0-255: what is bright enough to glow
BLOOM_SIGMA = 18
LIGHT_GLOW_SIGMA = 40         # glow that grows before the light fills the frame
SEGMENT_CRF = 10              # intermediate files: near lossless
PLACEHOLDER_RE = re.compile(r"\$\{(\w+)\}")


# --- table ----------------------------------------------------------------------


@dataclass
class Join:
    kind: str = "cut"
    seconds: float = 0.0

    def frames(self, fps: int) -> int:
        """Join length in frames, even so each side gets a whole half; 0 for a cut."""
        if self.kind == "cut":
            return 0
        half = int(self.seconds * fps / 2 + 0.5)
        return 2 * max(half, 1)


@dataclass
class Segment:
    cuts: list[str]
    kind: str                       # video | still | card
    source: str | dict | None = None
    card: dict | None = None
    paint_band: bool = False
    grain: bool = False
    bloom: float = 0.0
    join: Join = field(default_factory=Join)   # into the next segment
    # filled by plan()
    start: float = 0.0
    end: float = 0.0


@dataclass
class Table:
    script: Path
    fps: int
    band_top: int
    band_rgb: str
    segments: list[Segment]


def substitute(value, names: dict[str, str]):
    """Replace ${NAME} in every string of a JSON value."""
    if isinstance(value, str):
        def one(m):
            if m.group(1) not in names:
                raise ValueError(f"no value for ${{{m.group(1)}}}: give it with --work or --set")
            return names[m.group(1)]
        return PLACEHOLDER_RE.sub(one, value)
    if isinstance(value, list):
        return [substitute(v, names) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, names) for k, v in value.items()}
    return value


def parse_join(raw, default: Join) -> Join:
    if raw is None:
        return Join(default.kind, default.seconds)
    if isinstance(raw, str):
        raw = {"kind": raw}
    kind = raw.get("kind", default.kind)
    if kind not in JOIN_KINDS:
        raise ValueError(f"unknown join {kind!r}: use one of {', '.join(JOIN_KINDS)}")
    seconds = float(raw.get("seconds", default.seconds if kind == default.kind else 0.4))
    if kind != "cut" and seconds <= 0:
        raise ValueError(f"join {kind} needs a positive length")
    return Join(kind, 0.0 if kind == "cut" else seconds)


def load_table(data: dict, names: dict[str, str]) -> Table:
    data = substitute(data, names)
    dj = data.get("default_join", {"kind": "cut"})
    default = Join(dj.get("kind", "cut"), float(dj.get("seconds", 0.0)))
    band = data.get("band", {})
    segs = []
    for n, raw in enumerate(data["segments"]):
        kinds = [k for k in ("video", "still", "card") if k in raw]
        if len(kinds) != 1:
            raise ValueError(f"segment {n} ({raw.get('cuts')}): give exactly one of video, still, card")
        kind = kinds[0]
        look = raw.get("look", {})
        cuts = raw["cuts"] if isinstance(raw["cuts"], list) else [raw["cuts"]]
        segs.append(Segment(
            cuts=cuts, kind=kind,
            source=None if kind == "card" else raw[kind],
            card=raw["card"] if kind == "card" else None,
            paint_band=bool(raw.get("paint_band", False)),
            grain=bool(look.get("grain", False)),
            bloom=float(look.get("bloom", 0.0)),
            join=parse_join(raw.get("join"), default),
        ))
    if segs:
        segs[-1].join = Join()   # nothing follows the last segment
    return Table(Path(data["script"]), int(data.get("fps", 30)), int(band.get("top", 910)),
                 band.get("color", "#1b1a17").lstrip("#"), segs)


def slot_table(script_text: str) -> list[tuple[str, float, float]]:
    """(cut, start, end) in script order; a cut's slot spans all its rows."""
    lines = parse_script(script_text)
    out = []
    for cut in cuts_in_order(lines):
        rows = [ln for ln in lines if ln.cut == cut]
        out.append((cut, min(r.start for r in rows), max(r.end for r in rows)))
    return out


def plan(table: Table, slots: list[tuple[str, float, float]]) -> Table:
    """Give each segment the slot of the cuts it covers; check they tile the script."""
    covered = [c for s in table.segments for c in s.cuts]
    script_cuts = [c for c, _, _ in slots]
    if covered != script_cuts:
        raise ValueError(f"table cuts {covered} do not match the script's {script_cuts}")
    by_cut = {c: (a, b) for c, a, b in slots}
    t = 0.0
    for s in table.segments:
        s.start, s.end = by_cut[s.cuts[0]][0], by_cut[s.cuts[-1]][1]
        if abs(s.start - t) > 1e-6:
            raise ValueError(f"{s.cuts[0]} starts at {s.start}, the previous slot ends at {t}")
        for c in s.cuts[1:]:
            if by_cut[c][0] < s.start:
                raise ValueError(f"{c} is out of order")
        t = s.end
    for a, b in zip(table.segments, table.segments[1:]):
        d = a.join.frames(table.fps) / table.fps
        if d / 2 > min(a.end - a.start, b.end - b.start) / 2:
            raise ValueError(f"join {a.cuts[-1]}->{b.cuts[0]} is longer than half a slot")
    return table


def frames(t: float, fps: int) -> int:
    n = round(t * fps)
    if abs(n - t * fps) > 1e-6:
        raise ValueError(f"{t} s is not a whole number of frames at {fps} fps")
    return n


@dataclass
class Span:
    """One segment as rendered: its slot plus the halves of its joins."""
    name: str
    pre: int          # frames before the slot (half of the join in)
    slot: int         # frames of the slot
    post: int         # frames after the slot (half of the join out)
    join_in: Join
    join_out: Join
    first_frame: int  # frame number of the slot's start in the film

    @property
    def total(self) -> int:
        return self.pre + self.slot + self.post


def spans(table: Table) -> list[Span]:
    fps, segs = table.fps, table.segments
    out = []
    for i, s in enumerate(segs):
        jin = segs[i - 1].join if i else Join()
        jout = s.join
        out.append(Span("+".join(s.cuts), jin.frames(fps) // 2, frames(s.end, fps) - frames(s.start, fps),
                        jout.frames(fps) // 2, jin, jout, frames(s.start, fps)))
    return out


def join_points(sp: list[Span], fps: int) -> list[dict]:
    """Where each join starts and ends in the film, and the xfade offset it needs."""
    out = []
    acc = sp[0].total
    for a, b in zip(sp, sp[1:]):
        d = a.join_out.frames(fps)
        boundary = b.first_frame
        out.append({"from": a.name, "to": b.name, "kind": a.join_out.kind,
                    "start": boundary - d // 2, "boundary": boundary, "end": boundary + d // 2,
                    "offset": acc - d})
        acc = acc - d + b.total
    return out


def film_frames(sp: list[Span], fps: int) -> int:
    return sum(s.total for s in sp) - sum(s.join_out.frames(fps) for s in sp)


# --- filters ----------------------------------------------------------------------


def escape_path(p: str | Path) -> str:
    """A path as a filter option value (drive colons escaped, quoted)."""
    return "'" + str(p).replace("\\", "/").replace(":", "\\:") + "'"


def smooth(x: str) -> str:
    """smoothstep of x clipped to [0, 1], as an ffmpeg expression."""
    c = f"clip({x},0,1)"
    return f"({c}*{c}*(3-2*{c}))"


def ramps(span: Span, fps: int, kind: str) -> str | None:
    """Expression in T (seconds into the segment file) for the strength of a join effect.

    Out: rises over the first half of the join and holds to the end of the file.
    In: holds from the start and falls over the second half. Both peak at the boundary.
    For grain the rise and fall span the whole join, so the sand is densest at the boundary.
    """
    parts = []
    L = span.total / fps
    if span.join_in.kind == kind:
        d = span.join_in.frames(fps) / fps
        if kind == "grain":
            parts.append(f"(1-{smooth(f'T/{d:.6f}')})")
        else:
            parts.append(f"(1-{smooth(f'(T-{d / 2:.6f})/{d / 2:.6f}')})")
    if span.join_out.kind == kind:
        d = span.join_out.frames(fps) / fps
        if kind == "grain":
            parts.append(smooth(f"(T-{L - d:.6f})/{d:.6f}"))
        else:
            parts.append(smooth(f"(T-{L - d:.6f})/{d / 2:.6f}"))
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else f"max({parts[0]},{parts[1]})"


def drawtext_lines(card: dict, k: float, text_files: list[str]) -> list[str]:
    out = []
    for line, tf in zip(card["lines"], text_files):
        out.append(
            f"drawtext=fontfile={escape_path(line['font'])}:textfile={tf}"
            f":fontsize={round(line['size'] * k)}:fontcolor=0x{line['color'].lstrip('#')}"
            f":x={round(line['x'] * k)}:y={round(line['y'] * k)}")
    return out


def segment_graph(seg: Segment, span: Span, fps: int, width: int, height: int,
                  band_top: int, band_rgb: str, text_files: list[str] | None = None) -> str:
    """filter_complex for one segment file. Input 0 is the picture (none for a card)."""
    k = height / DESIGN_H
    n = span.total
    dur = n / fps
    size = f"{width}x{height}"
    chains = []
    if seg.kind == "card":
        base = [f"color=c=0x{seg.card.get('background', '#131311').lstrip('#')}:s={size}:r={fps}:d={dur:.6f}",
                *drawtext_lines(seg.card, k, text_files or [])]
    else:
        base = [f"[0:v]scale={width}:{height}:flags=lanczos", f"fps={fps}", "setpts=PTS-STARTPTS"]
        if span.pre:
            base.append(f"tpad=start={span.pre}:start_mode=clone")
        base += [f"tpad=stop={n}:stop_mode=clone", f"trim=end_frame={n}"]
    if seg.paint_band:
        top = round(band_top * k)
        base.append(f"drawbox=x=0:y={top}:w=iw:h={height - top}:color=0x{band_rgb}@1:t=fill")
    base += ["setpts=N/FRAME_RATE/TB", "format=gbrp"]
    chains.append(",".join(base) + "[p0]")
    cur = "p0"

    def src(color: str, label: str, extra: str = "") -> str:
        return f"color=c=0x{color}:s={size}:r={fps}:d={dur:.6f},format=gbrp{extra}[{label}]"

    if seg.bloom > 0:
        th = BLOOM_THRESHOLD
        lut = f"clip((val-{th})*255/(255-{th}),0,255)"
        chains.append(f"[{cur}]split[bA][bB]")
        chains.append(f"[bB]lutrgb=r='{lut}':g='{lut}':b='{lut}',gblur=sigma={BLOOM_SIGMA * k:.2f}[bG]")
        chains.append(f"[bA][bG]blend=all_mode=screen:all_opacity={seg.bloom:.3f}[pb]")
        cur = "pb"

    light = ramps(span, fps, "light")
    if light:
        # glow from the picture's own light first, then the warm light fills the frame
        chains.append(f"[{cur}]split[lA][lB]")
        chains.append(f"[lB]gblur=sigma={LIGHT_GLOW_SIGMA * k:.2f}[lG]")
        chains.append(f"[lA][lG]blend=all_expr='255-(255-A)*(255-B*{light})/255'[lS]")
        chains.append(src(LIGHT_RGB, "lW"))
        chains.append(f"[lS][lW]blend=all_expr='A+(B-A)*pow({light},2.2)'[pl]")
        cur = "pl"

    chains.append(f"[{cur}]format=yuv444p[py]")
    cur = "py"
    over = "if(lt(A,128),2*A*B/255,255-2*(255-A)*(255-B)/255)"   # overlay blend
    seed = span.first_frame + 1   # fixed by the cut's place in the film
    if seg.grain:
        chains.append(f"color=c=0x{GRAY}:s={size}:r={fps}:d={dur:.6f},format=yuv444p,"
                      f"noise=c0s={GRAIN_NOISE}:c0f=t:all_seed={seed},"
                      f"gblur=sigma={GRAIN_SIGMA * k:.2f}:planes=1[gN]")
        chains.append(f"[{cur}][gN]blend=c0_expr='A+({over}-A)*{GRAIN_LOOK:.3f}':c1_expr=A:c2_expr=A[pg]")
        cur = "pg"
    gjoin = ramps(span, fps, "grain")
    if gjoin:
        chains.append(f"color=c=0x{GRAY}:s={size}:r={fps}:d={dur:.6f},format=yuv444p,"
                      f"noise=c0s={GRAIN_JOIN_NOISE}:c0f=t:all_seed={seed + 7919},"
                      f"gblur=sigma={GRAIN_JOIN_SIGMA * k:.2f}:planes=1[gJ]")
        chains.append(f"[{cur}][gJ]blend=c0_expr='A+({over}-A)*{GRAIN_JOIN:.3f}*pow({gjoin},1.5)'"
                      f":c1_expr=A:c2_expr=A[pj]")
        cur = "pj"
    chains.append(f"[{cur}]format=yuv420p[out]")
    return ";".join(chains)


def resolve_source(seg: Segment, height: int) -> Path | None:
    """The source file; a video given per height picks the requested one or the tallest."""
    if seg.kind == "card":
        return None
    src = seg.source
    if isinstance(src, dict):
        src = src.get(str(height)) or src[max(src, key=int)]
    return Path(src)


def segment_command(seg: Segment, span: Span, fps: int, height: int, out: Path, band_top: int,
                    band_rgb: str, text_files: list[str] | None = None, ffmpeg: str = "ffmpeg") -> list[str]:
    width = height * DESIGN_W // DESIGN_H
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    srcp = resolve_source(seg, height)
    if seg.kind == "video":
        cmd += ["-i", str(srcp)]
    elif seg.kind == "still":
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{span.total / fps:.6f}", "-i", str(srcp)]
    graph = segment_graph(seg, span, fps, width, height, band_top, band_rgb, text_files)
    cmd += ["-filter_complex", graph, "-map", "[out]", "-frames:v", str(span.total), "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(SEGMENT_CRF), "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", str(out)]
    return cmd


def _hash(i: str, j: str) -> str:
    """Fixed pseudo-random value in [0, 1) of an integer cell (i, j)."""
    s = f"sin(({i})*12.9898+({j})*78.233)*43758.5453"
    return f"({s}-floor({s}))"


def sand_expr(width: int, height: int) -> str:
    """xfade custom expression: the picture comes apart in drifts of sand and the next forms.

    Each pixel's moment of change is 0.65 x smooth value noise (patches of
    SAND_PATCH_PX) + 0.35 x a fixed hash per SAND_PX grain, so the change spreads
    in patches with a grainy edge. xfade's P runs from 1 (first input) to 0
    (second); a pixel fades over the window SAND_SOFT of the join. X/W and Y/H
    keep the pattern the same on the subsampled chroma planes.
    """
    k = height / DESIGN_H
    pc, pr = round(width / (SAND_PATCH_PX * k)), round(height / (SAND_PATCH_PX * k))
    gc, gr = round(width / (SAND_PX * k)), round(height / (SAND_PX * k))
    # written out without st()/ld(): xfade runs the expression on several threads at once
    # and the registers would be shared between them
    def value_noise(cols: float, rows: float, shift: float) -> str:
        x, y = f"(X/W*{cols:.3f}+{shift})", f"(Y/H*{rows:.3f}+{shift})"
        i, j = f"floor{x}", f"floor{y}"
        fx, fy = f"({x}-{i})", f"({y}-{j})"
        sx, sy = f"({fx}*{fx}*(3-2*{fx}))", f"({fy}*{fy}*(3-2*{fy}))"
        return (f"({_hash(i, j)}*(1-{sx})*(1-{sy})+{_hash(f'{i}+1', j)}*{sx}*(1-{sy})"
                f"+{_hash(i, f'{j}+1')}*(1-{sx})*{sy}+{_hash(f'{i}+1', f'{j}+1')}*{sx}*{sy})")

    # two octaves (the second offset by half a cell so their grids do not line up)
    v = f"(0.65*{value_noise(pc, pr, 0)}+0.35*{value_noise(pc * 2.3, pr * 2.3, 0.5)})"
    r = f"(0.7*{v}+0.3*{_hash(f'floor(X/W*{gc})+101', f'floor(Y/H*{gr})+37')})"
    w = SAND_SOFT
    m = f"clip((P-{r}*{1 - w:.3f})/{w:.3f},0,1)"
    return f"B+(A-B)*{m}"


def xfade_args(kind: str, width: int, height: int) -> str:
    if kind == "grain":
        # a smooth fade under the swelling grain layer; a per-pixel or patchy break-up
        # (sand_expr, xfade dissolve) reads as TV static where the next cut is dark
        return "transition=fade"
    return "transition=fade"   # dissolve; light (both sides already hold the light at the boundary)


def join_graph(sp: list[Span], fps: int, width: int = DESIGN_W, height: int = DESIGN_H) -> str:
    """filter_complex joining the segment files (inputs 0..n-1) into [film]."""
    chains = [f"[{i}:v]settb=1/{fps},setpts=N/{fps}/TB[s{i}]" for i in range(len(sp))]
    cur = "s0"
    for i, jp in enumerate(join_points(sp, fps), 1):
        nxt = f"j{i}"
        if jp["kind"] == "cut":
            chains.append(f"[{cur}][s{i}]concat=n=2:v=1:a=0[{nxt}]")
        else:
            d = (jp["end"] - jp["start"]) / fps
            chains.append(f"[{cur}][s{i}]xfade={xfade_args(jp['kind'], width, height)}:duration={d:.6f}"
                          f":offset={jp['offset'] / fps:.6f}[{nxt}]")
        cur = nxt
    chains.append(f"[{cur}]format=yuv420p[film]")
    return ";".join(chains)


def join_command(files: list[Path], sp: list[Span], fps: int, out: Path, height: int = DESIGN_H,
                 ffmpeg: str = "ffmpeg") -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for f in files:
        cmd += ["-i", str(f)]
    width = height * DESIGN_W // DESIGN_H
    cmd += ["-filter_complex", join_graph(sp, fps, width, height), "-map", "[film]",
            "-frames:v", str(film_frames(sp, fps)), "-r", str(fps),
            "-c:v", "libx264", "-preset", "medium", "-crf", str(SEGMENT_CRF), "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", str(out)]
    return cmd


def mux_command(video: Path, audio: Path | None, out: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    """Without subtitles: copy the picture, add the audio if any."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video)]
    if audio:
        cmd += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-ac", "2",
                "-b:a", "192k", "-shortest"]
    cmd += ["-c:v", "copy", "-movflags", "+faststart", str(out)]
    return cmd


# --- build ------------------------------------------------------------------------


def run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, check=True, cwd=cwd)


def build(table: Table, out: Path, height: int, subs: Path | None, audio: Path | None,
          ffmpeg: str = "ffmpeg", log=print) -> Path:
    out = out.resolve()
    parts = out.parent / f"{out.stem}.parts"
    parts.mkdir(parents=True, exist_ok=True)
    sp = spans(table)
    files = []
    for i, (seg, span) in enumerate(zip(table.segments, sp)):
        f = parts / f"{i:02d}_{span.name.replace('+', '_')}.mp4"
        text_files = None
        if seg.kind == "card":
            text_files = []
            for j, line in enumerate(seg.card["lines"]):
                tf = f"{i:02d}_card_{j}.txt"
                (parts / tf).write_text(line["text"], encoding="utf-8")
                text_files.append(tf)
        # run in the parts folder: card text files are named bare (no drive colon in the filter)
        run(segment_command(seg, span, table.fps, height, f, table.band_top, table.band_rgb,
                            text_files, ffmpeg), cwd=parts)
        log(f"{span.name:10s} {seg.kind:5s} {span.first_frame / table.fps:7.2f}s "
            f"+{span.slot:4d}f  pre {span.pre} post {span.post}  -> {seg.join.kind}")
        files.append(f)
    film = parts / "film.mp4"
    run(join_command(files, sp, table.fps, film, height, ffmpeg))
    if subs:
        # the ass filter is given a bare file name: Windows drive colons break its option parser
        run(burn_command(film, Path(subs.name), out, audio.resolve() if audio else None, ffmpeg=ffmpeg),
            cwd=subs.resolve().parent)
    else:
        run(mux_command(film, audio.resolve() if audio else None, out, ffmpeg))
    log(f"wrote {out} ({film_frames(sp, table.fps) / table.fps:.3f} s)")
    return out


def read_table(path: Path, work: str | None, sets: list[str]) -> Table:
    names = {}
    if work:
        names["WORK"] = str(Path(work).resolve()).replace("\\", "/")
    for s in sets:
        k, _, v = s.partition("=")
        names[k] = v
    table = load_table(json.loads(path.read_text(encoding="utf-8")), names)
    if not table.script.is_absolute():
        table.script = (path.parent / table.script).resolve()
    return plan(table, slot_table(table.script.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "plan"):
        p = sub.add_parser(name)
        p.add_argument("table", type=Path)
        if name == "build":
            p.add_argument("out", type=Path)
            p.add_argument("--height", type=int, choices=(1080, 2160), default=1080)
            p.add_argument("--subs", type=Path)
            p.add_argument("--audio", type=Path)
        p.add_argument("--work", help="folder that ${WORK} in the table stands for")
        p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                       help="another ${NAME} in the table")
    a = ap.parse_args(argv)
    table = read_table(a.table, a.work, a.set)
    sp = spans(table)
    if a.cmd == "plan":
        for s in sp:
            print(f"{s.name:10s} {s.first_frame / table.fps:7.2f}s  slot {s.slot:4d}f  pre {s.pre:2d}  post {s.post:2d}")
        for jp in join_points(sp, table.fps):
            print(f"{jp['from']:>8s} -> {jp['to']:8s} {jp['kind']:8s} "
                  f"{jp['start'] / table.fps:7.3f} | {jp['boundary'] / table.fps:7.3f} | {jp['end'] / table.fps:7.3f}")
        print(f"total {film_frames(sp, table.fps) / table.fps:.3f} s")
        return 0
    ff = ffmpeg_path()
    build(table, a.out, a.height, a.subs, a.audio, ffmpeg=ff)
    return 0


if __name__ == "__main__":
    sys.exit(main())
