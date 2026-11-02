"""Lay the narration on the video's timeline, write burned-in subtitles.

Reads ``voice.json`` from ``voice.py synth``. Each cut's sentences start
``--lead`` seconds into the cut's slot and follow one another with a short
gap. A Japanese sentence too wide for one line is split at 「、」 into
chunks shown one after another; the chunk boundaries are placed at pauses
found in the sentence's audio (or by text length when no pause is near).
The English line stays up for the whole sentence.

    py promo/subs.py layout VOICE_DIR OUT_DIR [--cuts N00,N01] [--hot "99 %"]
    py promo/subs.py burn VIDEO OUT_DIR/subs.ass OUT.mp4 --audio OUT_DIR/narration.wav

Look (fixed, 1920x1080 coordinates, scaled by libass for 4K): an ink band
over the bottom 170 px for the whole video, Japanese 48 px bold with its bottom
at y = 995, English 30 px with its bottom at y = 1040, each always one line.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

try:
    from promo.export import ffmpeg_path
except ImportError:  # run as a script: promo/ is on sys.path
    from export import ffmpeg_path

BAND_TOP, BAND_H = 910, 170
JA_Y, EN_Y = 995, 1040
JA_MAX_EM = 34.0          # widest Japanese line, in full-width characters
LEAD_S = 0.3              # first sentence starts this long into its slot
GAP_S = 0.35              # silence between sentences of one cut
PAUSE_SEARCH_S = 0.6      # how far from the text-length guess a pause may be


# --- text ---------------------------------------------------------------------

def em_width(text: str) -> float:
    """Width in full-width characters: ASCII and spaces count half."""
    return sum(0.5 if ord(ch) < 0x2000 else 1.0 for ch in text)


def split_at_commas(text: str) -> list[str]:
    parts, cur = [], ""
    for ch in text:
        cur += ch
        if ch == "、":
            parts.append(cur)
            cur = ""
    if cur:
        parts.append(cur)
    return parts


def chunk_sentence(text: str, max_em: float = JA_MAX_EM) -> list[str]:
    """Pack comma-separated parts into as few one-line chunks as fit."""
    if em_width(text) <= max_em:
        return [text]
    chunks, cur = [], ""
    for part in split_at_commas(text):
        if cur and em_width(cur + part) > max_em:
            chunks.append(cur)
            cur = part
        else:
            cur += part
    if cur:
        chunks.append(cur)
    return chunks


def speech_weight(text: str) -> float:
    """Rough speaking length of a chunk: characters, without spaces and marks."""
    return float(sum(1 for ch in text if not ch.isspace() and ch not in "、。「」"))


# --- audio --------------------------------------------------------------------

def read_wav(path: Path) -> tuple[array.array, int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path}: expected 16-bit mono")
        data = array.array("h", w.readframes(w.getnframes()))
        return data, w.getframerate()


def find_pauses(samples, rate: int, window_s: float = 0.01, min_pause_s: float = 0.08,
                rel_threshold: float = 0.06) -> list[float]:
    """Centres (s) of quiet stretches inside the speech, leading/trailing silence excluded."""
    win = max(1, int(rate * window_s))
    rms = []
    for i in range(0, len(samples) - win + 1, win):
        seg = samples[i:i + win]
        rms.append(math.sqrt(sum(x * x for x in seg) / win))
    if not rms:
        return []
    loud = max(rms)
    quiet = [r < loud * rel_threshold for r in rms]
    voiced = [i for i, q in enumerate(quiet) if not q]
    if not voiced:
        return []
    first, last = voiced[0], voiced[-1]
    pauses, run = [], None
    for i in range(first, last + 1):
        if quiet[i]:
            run = i if run is None else run
        elif run is not None:
            if (i - run) * window_s >= min_pause_s:
                pauses.append((run + i) / 2 * window_s)
            run = None
    return pauses


def chunk_times(chunks: list[str], duration: float, pauses: list[float],
                search_s: float = PAUSE_SEARCH_S) -> list[float]:
    """Start time of each chunk within a sentence of the given duration.

    The text-length guess for each boundary is moved to the nearest unused
    pause within ``search_s``; boundaries stay in order.
    """
    weights = [speech_weight(c) for c in chunks]
    total = sum(weights) or 1.0
    starts, acc, used = [0.0], 0.0, set()
    for w in weights[:-1]:
        acc += w
        guess = duration * acc / total
        best = None
        for k, p in enumerate(pauses):
            if k in used or p <= starts[-1]:
                continue
            if abs(p - guess) <= search_s and (best is None or abs(p - guess) < abs(pauses[best] - guess)):
                best = k
        if best is not None:
            used.add(best)
            starts.append(pauses[best])
        else:
            starts.append(max(guess, starts[-1]))
    return starts


# --- timeline -----------------------------------------------------------------

@dataclass
class Cue:
    start: float
    end: float
    ja: str
    en: str


def place_sentences(lines: list[dict], lead: float = LEAD_S, gap: float = GAP_S,
                    origin: float = 0.0) -> list[dict]:
    """Start time on the video timeline for every sentence (lines from voice.json)."""
    placed, cursor, cut = [], None, None
    for ln in lines:
        if ln["cut"] != cut:
            cut = ln["cut"]
            cursor = ln["start"] - origin + lead
        start = cursor
        cursor = start + ln["seconds"] + gap
        placed.append({**ln, "at": round(start, 3)})
    return placed


def build_cues(placed: list[dict], chunk_starts: list[list[float]]) -> list[Cue]:
    cues = []
    for ln, starts in zip(placed, chunk_starts):
        chunks = chunk_sentence(ln["ja"])
        end = ln["at"] + ln["seconds"]
        for k, ch in enumerate(chunks):
            a = ln["at"] + starts[k]
            b = ln["at"] + starts[k + 1] if k + 1 < len(chunks) else end
            cues.append(Cue(a, b, ch, ln["en"]))
    return cues


# --- ASS ----------------------------------------------------------------------

def ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    """'F4F1EA' -> '&H00EAF1F4' (style colours are AABBGGRR)."""
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_inline_colour(hex_rgb: str) -> str:
    """'E7B36A' -> '&H6AB3E7&' (override tags are BBGGRR)."""
    return f"&H{hex_rgb[4:6]}{hex_rgb[2:4]}{hex_rgb[0:2]}&".upper()


PAPER, SOFT, WARM = "F4F1EA", "B9B3A5", "E7B36A"


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def highlight(text: str, hot: list[str], base: str = PAPER) -> str:
    """Escape for ASS and show each ``hot`` phrase in the warm colour."""
    text = ass_escape(text)
    for h in hot:
        h = ass_escape(h)
        text = text.replace(h, f"{{\\c{ass_inline_colour(WARM)}}}{h}{{\\c{ass_inline_colour(base)}}}")
    return text


def write_ass(cues: list[Cue], hot: list[str] | None = None, font: str = "BIZ UDPGothic") -> str:
    hot = hot or []
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: JA,{font},48,{ass_colour(PAPER)},{ass_colour(PAPER)},&H00000000,&H00000000,-1,0,0,0,100,100,1,0,1,0,0,5,0,0,0,1
Style: EN,{font},30,{ass_colour(SOFT)},{ass_colour(SOFT)},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for c in cues:
        events.append(f"Dialogue: 0,{ass_time(c.start)},{ass_time(c.end)},JA,,0,0,0,,"
                      f"{{\\an2\\pos(960,{JA_Y})}}{highlight(c.ja, hot)}")
    # one English event per sentence, spanning all of its chunks
    for en, a, b in merge_english(cues):
        events.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},EN,,0,0,0,,"
                      f"{{\\an2\\pos(960,{EN_Y})}}{highlight(en, hot, base=SOFT)}")
    return head + "\n".join(events) + "\n"


def merge_english(cues: list[Cue]) -> list[tuple[str, float, float]]:
    out: list[tuple[str, float, float]] = []
    for c in cues:
        if out and out[-1][0] == c.en and abs(out[-1][2] - c.start) < 1e-6:
            out[-1] = (c.en, out[-1][1], c.end)
        else:
            out.append((c.en, c.start, c.end))
    return out


# --- audio mix ----------------------------------------------------------------

def mix_voice(placed: list[dict], voice_dir: Path, out: Path, total_s: float) -> None:
    rate = None
    track = None
    for ln in placed:
        data, r = read_wav(voice_dir / ln["file"])
        if rate is None:
            rate = r
            track = array.array("h", bytes(2 * int(math.ceil(total_s * rate))))
        elif r != rate:
            raise ValueError("sentences have different sample rates")
        off = int(round(ln["at"] * rate))
        end = min(len(track), off + len(data))
        track[off:end] = data[: end - off]
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(track.tobytes())


# --- commands -----------------------------------------------------------------

def cmd_layout(voice_dir: Path, out_dir: Path, cuts: list[str] | None, hot: list[str],
               lead: float) -> None:
    meta = json.loads((voice_dir / "voice.json").read_text(encoding="utf-8"))
    lines = [ln for ln in meta["lines"] if not cuts or ln["cut"] in cuts]
    if not lines:
        raise SystemExit("no sentences for those cuts")
    origin = lines[0]["start"]
    total = max(ln["end"] for ln in lines) - origin
    placed = place_sentences(lines, lead=lead, origin=origin)
    starts = []
    for ln in placed:
        chunks = chunk_sentence(ln["ja"])
        if len(chunks) == 1:
            starts.append([0.0])
            continue
        samples, rate = read_wav(voice_dir / ln["file"])
        starts.append(chunk_times(chunks, ln["seconds"], find_pauses(samples, rate)))
    cues = build_cues(placed, starts)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "subs.ass").write_text(write_ass(cues, hot), encoding="utf-8")
    mix_voice(placed, voice_dir, out_dir / "narration.wav", total)
    (out_dir / "cues.json").write_text(json.dumps(
        {"origin_s": origin, "length_s": total, "sentences": placed,
         "cues": [c.__dict__ for c in cues]}, ensure_ascii=False, indent=2), encoding="utf-8")
    late = [ln["cut"] for ln in placed if ln["at"] + ln["seconds"] > ln["end"] - origin]
    print(f"{len(placed)} sentences, {len(cues)} Japanese lines, {total:.2f} s")
    if late:
        print("voice runs past its slot in: " + ", ".join(sorted(set(late))))


def burn_command(video: Path, ass: Path, out: Path, audio: Path | None, ffmpeg: str = "ffmpeg",
                 crf: int = 16) -> list[str]:
    band = (f"drawbox=x=0:y=ih*{BAND_TOP}/1080:w=iw:h=ih*{BAND_H}/1080"
            f":color=0x1B1A17@1:t=fill")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video)]
    if audio:
        cmd += ["-i", str(audio)]
    cmd += ["-vf", f"{band},ass={ass.name}",
            "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    if audio:
        # Stereo: the ffmpeg build used here stalls writing mono AAC at 192 kb/s.
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-ac", "2", "-b:a", "192k", "-shortest"]
    cmd += ["-movflags", "+faststart", str(out)]
    return cmd


def cmd_burn(video: Path, ass: Path, out: Path, audio: Path | None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # the ass filter is given a bare file name: Windows drive colons break its option parser
    cmd = burn_command(video.resolve(), ass, out.resolve(), audio.resolve() if audio else None,
                       ffmpeg=ffmpeg_path())
    subprocess.run(cmd, check=True, cwd=ass.resolve().parent)
    print(f"wrote {out}")


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Japanese text on a cp932 console
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("layout")
    p.add_argument("voice_dir", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--cuts", help="comma-separated cuts; time 0 is the first cut's start")
    p.add_argument("--hot", action="append", default=[], help="text shown in the warm colour")
    p.add_argument("--lead", type=float, default=LEAD_S)
    p = sub.add_parser("burn")
    p.add_argument("video", type=Path)
    p.add_argument("ass", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--audio", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "layout":
        cuts = a.cuts.split(",") if a.cuts else None
        cmd_layout(a.voice_dir, a.out_dir, cuts, a.hot, a.lead)
    elif a.cmd == "burn":
        cmd_burn(a.video, a.ass, a.out, a.audio)
    return 0


if __name__ == "__main__":
    sys.exit(main())
