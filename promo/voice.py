"""Narration with a local AivisSpeech Engine (VOICEVOX-style HTTP API).

The script is a tab-separated file kept outside the repository:

    # comment
    N00<TAB>0:00-0:07<TAB>日本語の文。<TAB>English sentence.

one row per sentence. A readings file (``as written<TAB>as read``) fixes
words the engine misreads; subtitles keep the text as written.

    py promo/voice.py speakers
    py promo/voice.py models                       # voice models and their licences
    py promo/voice.py reading SCRIPT --speaker ID [--readings FILE]
    py promo/voice.py synth   SCRIPT OUT_DIR --speaker ID [--readings FILE] [--only N07_2.wav ...]

    py promo/voice.py adopt   SCRIPT WAV_DIR       # recorded WAVs (N00_1.wav ...) -> voice.json
    py promo/voice.py edges   SRC_DIR DST_DIR      # recorded WAVs with faded edges, same lengths

``synth`` writes one WAV per sentence and ``voice.json`` (file, seconds,
text), then prints how each cut's voice fits its slot. ``--only`` remakes
just the named sentences and keeps the others as they were.
``adopt`` measures WAVs made elsewhere (the recorder in promo/recorder/),
writes the same ``voice.json`` for the sentences it finds, and names the
sentences with no file and the files that are not in the script. It exits
with status 1 when a sentence is missing (voice.json is still written).
The engine address is http://127.0.0.1:10101 unless AIVIS_URL is set.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import sys
import urllib.parse
import urllib.request
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

ENGINE = os.environ.get("AIVIS_URL", "http://127.0.0.1:10101")

# Silence placed between two sentences of the same cut when they are laid out.
SENTENCE_GAP_S = 0.35


@dataclass
class Line:
    cut: str
    start: float
    end: float
    ja: str
    en: str


def parse_clock(text: str) -> float:
    """'1:04' -> 64.0, '0:07.5' -> 7.5"""
    m, s = text.strip().split(":")
    return int(m) * 60 + float(s)


def parse_slot(text: str) -> tuple[float, float]:
    a, b = text.split("-")
    start, end = parse_clock(a), parse_clock(b)
    if end <= start:
        raise ValueError(f"slot ends before it starts: {text}")
    return start, end


def parse_script(text: str) -> list[Line]:
    lines = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        cols = raw.split("\t")
        if len(cols) != 4:
            raise ValueError(f"line {n}: expected 4 tab-separated columns, got {len(cols)}")
        cut, slot, ja, en = (c.strip() for c in cols)
        start, end = parse_slot(slot)
        lines.append(Line(cut, start, end, ja, en))
    return lines


def parse_readings(text: str) -> list[tuple[str, str]]:
    pairs = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        src, dst = raw.split("\t")
        pairs.append((src.strip(), dst.strip()))
    # longer words first, so a short entry never breaks a longer one
    return sorted(pairs, key=lambda p: -len(p[0]))


def apply_readings(text: str, readings: list[tuple[str, str]]) -> str:
    for src, dst in readings:
        text = text.replace(src, dst)
    return text


def cuts_in_order(lines: list[Line]) -> list[str]:
    seen = []
    for ln in lines:
        if ln.cut not in seen:
            seen.append(ln.cut)
    return seen


def fit_report(lines: list[Line], seconds: list[float], gap: float = SENTENCE_GAP_S) -> list[dict]:
    """Voice length of each cut (sentences plus gaps) against its slot."""
    out = []
    for cut in cuts_in_order(lines):
        idx = [i for i, ln in enumerate(lines) if ln.cut == cut]
        voice = sum(seconds[i] for i in idx) + gap * (len(idx) - 1)
        slot = lines[idx[0]].end - lines[idx[0]].start
        out.append({"cut": cut, "voice_s": round(voice, 2), "slot_s": round(slot, 2),
                    "spare_s": round(slot - voice, 2)})
    return out


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


# --- engine -----------------------------------------------------------------

def _request(method: str, path: str, query: dict | None = None, body: bytes | None = None,
             timeout: float = 120) -> bytes:
    url = ENGINE + path + ("?" + urllib.parse.urlencode(query) if query else "")
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def speakers() -> list[dict]:
    return json.loads(_request("GET", "/speakers"))


def models() -> dict:
    return json.loads(_request("GET", "/aivm_models"))


def reading(text: str, speaker: int) -> str:
    phrases = json.loads(_request("POST", "/accent_phrases", {"text": text, "speaker": speaker}))
    parts = []
    for ph in phrases:
        parts.append("".join(m["text"] for m in ph["moras"]))
        if ph.get("pause_mora"):
            parts.append("、")
    return "/".join(parts)


def synthesize(text: str, speaker: int, speed: float = 1.0) -> bytes:
    query = json.loads(_request("POST", "/audio_query", {"text": text, "speaker": speaker}))
    query["speedScale"] = speed
    return _request("POST", "/synthesis", {"speaker": speaker}, json.dumps(query).encode("utf-8"))


# --- commands ---------------------------------------------------------------

def load(script: Path, readings_file: Path | None):
    lines = parse_script(script.read_text(encoding="utf-8"))
    readings = parse_readings(readings_file.read_text(encoding="utf-8")) if readings_file else []
    return lines, readings


def sentence_files(lines: list[Line]) -> list[str]:
    """'<cut>_<k>.wav' for the k-th sentence of each cut."""
    counter: dict[str, int] = {}
    names = []
    for ln in lines:
        counter[ln.cut] = counter.get(ln.cut, 0) + 1
        names.append(f"{ln.cut}_{counter[ln.cut]}.wav")
    return names


def cmd_synth(script: Path, out_dir: Path, speaker: int, readings_file: Path | None, speed: float,
              only: list[str] | None = None) -> None:
    """With ``only``, synthesize just those files and keep the rest from voice.json
    (the engine varies a little each time, so untouched sentences keep their length)."""
    lines, readings = load(script, readings_file)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = sentence_files(lines)
    kept: dict[str, dict] = {}
    if only:
        unknown = sorted(set(only) - set(names))
        if unknown:
            raise SystemExit(f"not in the script: {', '.join(unknown)}")
        old = json.loads((out_dir / "voice.json").read_text(encoding="utf-8"))
        if (old["speaker"], old["speed"]) != (speaker, speed):
            raise SystemExit("voice.json was made with another speaker or speed; synthesize all")
        kept = {r["file"]: r for r in old["lines"]}
    records, seconds = [], []
    for ln, name in zip(lines, names):
        if only and name not in only:
            if name not in kept:
                raise SystemExit(f"{name} is not in voice.json; add it to --only")
            # old text and audio, new slot
            rec = {**kept[name], "start": ln.start, "end": ln.end}
            records.append(rec)
            seconds.append(rec["seconds"])
            continue
        spoken = apply_readings(ln.ja, readings)
        (out_dir / name).write_bytes(synthesize(spoken, speaker, speed))
        sec = wav_seconds(out_dir / name)
        seconds.append(sec)
        records.append({**asdict(ln), "spoken": spoken, "file": name, "seconds": round(sec, 3)})
        print(f"{name:12s} {sec:5.2f} s  {ln.ja}")
    report = fit_report(lines, seconds)
    meta = {"engine": ENGINE, "speaker": speaker, "speed": speed,
            "sentence_gap_s": SENTENCE_GAP_S, "lines": records, "fit": report}
    (out_dir / "voice.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\ncut    voice  slot  spare")
    for r in report:
        flag = "  OVER" if r["spare_s"] < 0 else ""
        print(f"{r['cut']:5s} {r['voice_s']:6.2f} {r['slot_s']:5.1f} {r['spare_s']:6.2f}{flag}")
    total = sum(seconds) + SENTENCE_GAP_S * (len(seconds) - len(report))
    print(f"voice total {total:.1f} s")


def wav_format(path: Path) -> tuple[int, int, int]:
    """(rate, channels, bytes per sample)"""
    with wave.open(str(path), "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth()


def adopt(script: Path, wav_dir: Path) -> dict:
    """voice.json for recorded WAVs named like the synthesized ones.

    Returns {"meta": what was written, "missing": [...], "extra": [...],
    "odd_format": [(file, rate, channels, width)]}.
    """
    lines = parse_script(script.read_text(encoding="utf-8"))
    names = sentence_files(lines)
    records, present, missing, odd = [], [], [], []
    for ln, name in zip(lines, names):
        path = wav_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        fmt = wav_format(path)
        if fmt != (44100, 1, 2):
            odd.append((name, *fmt))
        sec = wav_seconds(path)
        present.append(ln)
        records.append({**asdict(ln), "spoken": ln.ja, "file": name, "seconds": round(sec, 3)})
    known = set(names)
    extra = sorted(p.name for p in wav_dir.glob("*.wav") if p.name not in known)
    report = fit_report(present, [r["seconds"] for r in records]) if records else []
    meta = {"source": "recorded", "sentence_gap_s": SENTENCE_GAP_S,
            "lines": records, "fit": report, "missing": missing}
    (wav_dir / "voice.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"meta": meta, "missing": missing, "extra": extra, "odd_format": odd}


# --- edges of recorded sentences --------------------------------------------
#
# Measured on the recorded takes: the room tone under the voice (about -60 dBFS
# in 5 ms frames, mostly above 2 kHz where the music is thin) started at the
# first sample of each WAV and stopped at the sample where the take was
# stopped, 0-0.65 s after the last word (one take was stopped while its last
# sound was still going), often with a click there. Over the
# music both edges stood out as a sudden start / stop of hiss. ``edges`` keeps
# the room tone only where the voice is: a raised-cosine fade-in ending at the
# voice's first sound, a raised-cosine fade-out starting just after its last
# word, digital silence outside. The length of the WAV does not change, so
# the timeline laid out from it does not move.

EDGE_FRAME_S = 0.005
EDGE_FADE_IN_S = 0.03     # into the first sound; short enough not to soften the onset
EDGE_FADE_OUT_S = 0.15    # room tone and breath after the last word
EDGE_HOLD_S = 0.02        # after the last voiced frame, before the fade-out starts
EDGE_VOICE_DB = 35.0      # voiced: a 5 ms frame within this of the loudest (as the recorder's trim)
EDGE_MIN_RUN_S = 0.03     # a voiced run this long; shorter ones are clicks
EDGE_SOFT_DB = 8.0        # first sound: this far above the room tone before the voice
EDGE_SOUND_DB = -80.0     # after the take ends only the filters' low-frequency decay is
                          # left; its second difference is near the 16-bit floor (about -93 dB)
EDGE_SNAP_S = 0.003       # how far a cut may move to reach a zero crossing


def _frame_db(x, n: int) -> list[float]:
    out = []
    for k in range(len(x) // n):
        s = sum(v * v for v in x[k * n:(k + 1) * n]) / n
        out.append(10 * math.log10(s + 1e-14))
    return out


def _runs(mask: list[bool], min_len: int) -> list[tuple[int, int]]:
    out, start = [], None
    for i, m in enumerate(mask + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def _zero_crossing(x, i: int, step: int, limit: int) -> int:
    """The nearest index from i (going by step, at most limit samples) where the
    signal changes sign or is zero; i itself when there is none."""
    for k in range(limit + 1):
        j = i + step * k
        if j <= 0 or j >= len(x):
            break
        if x[j] == 0 or (x[j - 1] < 0) != (x[j] < 0):
            return j
    return i


def find_edges(x, rate: int) -> dict:
    """Sample positions in a sentence: the first sound, the last voiced frame
    and where any sound ends (see the constants above). ``x`` is a sequence
    of floats or ints."""
    n = max(1, int(round(EDGE_FRAME_S * rate)))
    db = _frame_db(x, n)
    if not db:
        return {"onset": 0, "voice_end": len(x), "sound_end": len(x)}
    top = max(db)
    voiced = _runs([d > top - EDGE_VOICE_DB for d in db], max(1, int(round(EDGE_MIN_RUN_S / EDGE_FRAME_S))))
    if not voiced:
        return {"onset": 0, "voice_end": len(x), "sound_end": len(x)}
    first, last = voiced[0][0], voiced[-1][1]
    # the first sound: back from the first voiced run while frames stay above the room tone
    room = sorted(db[:first])[len(db[:first]) // 2] if first else -200.0
    onset = first
    while onset > 0 and db[onset - 1] > room + EDGE_SOFT_DB:
        onset -= 1
    # where sound ends: last frame whose second difference is above EDGE_SOUND_DB
    sound_end = last
    for k in range(len(db) - 1, last - 1, -1):
        seg = x[max(2, k * n):(k + 1) * n]
        d2 = [seg[i] - 2 * seg[i - 1] + seg[i - 2] for i in range(2, len(seg))] if len(seg) > 2 else []
        if d2 and 10 * math.log10(sum(v * v for v in d2) / len(d2) + 1e-14) > EDGE_SOUND_DB:
            sound_end = k + 1
            break
    return {"onset": onset * n, "voice_end": last * n, "sound_end": min(len(x), max(sound_end, last) * n)}


def fade_edges(x: list[float], rate: int) -> tuple[list[float], dict]:
    """``x`` with the room tone kept only where the voice is (see above).
    Returns the new samples and the spans in seconds."""
    e = find_edges(x, rate)
    y = list(x)
    fi, fo, hold = (int(round(s * rate)) for s in (EDGE_FADE_IN_S, EDGE_FADE_OUT_S, EDGE_HOLD_S))
    snap = int(round(EDGE_SNAP_S * rate))
    # fade-in: ends at the first sound, starts at a zero crossing
    in_end = e["onset"]
    in_start = _zero_crossing(x, max(0, in_end - fi), -1, snap)
    for i in range(0, in_start):
        y[i] = 0.0
    span = max(1, in_end - in_start)
    for i in range(in_start, in_end):
        y[i] = x[i] * math.sin(0.5 * math.pi * (i - in_start) / span) ** 2
    # fade-out: from just after the last word; it ends before the sound stops,
    # and moves earlier when the take was stopped while still sounding
    out_end = min(e["voice_end"] + hold + fo, e["sound_end"])
    out_end = _zero_crossing(x, out_end, -1, snap)
    out_start = max(in_end, out_end - fo)
    span = max(1, out_end - out_start)
    for i in range(out_start, out_end):
        y[i] = x[i] * math.cos(0.5 * math.pi * (i - out_start) / span) ** 2
    for i in range(out_end, len(y)):
        y[i] = 0.0
    spans = {"fade_in_n": (in_start, in_end), "fade_out_n": (out_start, out_end),
             "fade_in_s": (round(in_start / rate, 3), round(in_end / rate, 3)),
             "fade_out_s": (round(out_start / rate, 3), round(out_end / rate, 3)),
             "voice_end_s": round(e["voice_end"] / rate, 3), "sound_end_s": round(e["sound_end"] / rate, 3)}
    return y, spans


def read_pcm16(path: Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path.name}: expected 16-bit mono")
        rate = w.getframerate()
        data = array.array("h", w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        data.byteswap()
    return [v / 32768 for v in data], rate


def write_pcm16(path: Path, x: list[float], rate: int) -> None:
    data = array.array("h", (max(-32768, min(32767, round(v * 32768))) for v in x))
    if sys.byteorder == "big":
        data.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def cmd_edges(src: Path, dst: Path) -> int:
    if src.resolve() == dst.resolve():
        raise SystemExit("write the faded WAVs to another folder")
    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(src.glob("*.wav"))
    for p in files:
        x, rate = read_pcm16(p)
        y, s = fade_edges(x, rate)
        write_pcm16(dst / p.name, y, rate)
        print(f"{p.name:12s} {len(x) / rate:6.3f} s  in {s['fade_in_s'][0]:.3f}-{s['fade_in_s'][1]:.3f}  "
              f"voice end {s['voice_end_s']:.3f}  out {s['fade_out_s'][0]:.3f}-{s['fade_out_s'][1]:.3f}  "
              f"sound end {s['sound_end_s']:.3f}")
    print(f"wrote {len(files)} WAVs to {dst}")
    return 0


def cmd_adopt(script: Path, wav_dir: Path) -> int:
    res = adopt(script, wav_dir)
    for r in res["meta"]["lines"]:
        print(f"{r['file']:12s} {r['seconds']:5.2f} s  {r['ja']}")
    if res["meta"]["fit"]:
        print("\ncut    voice  slot  spare")
        for r in res["meta"]["fit"]:
            flag = "  OVER" if r["spare_s"] < 0 else ""
            print(f"{r['cut']:5s} {r['voice_s']:6.2f} {r['slot_s']:5.1f} {r['spare_s']:6.2f}{flag}")
    for name, rate, ch, width in res["odd_format"]:
        print(f"  {name}: {rate} Hz, {ch} ch, {8 * width} bit (expected 44100 Hz, 1 ch, 16 bit)")
    if res["extra"]:
        print("not in the script: " + ", ".join(res["extra"]))
    if res["missing"]:
        print("missing: " + ", ".join(res["missing"]))
    print(f"wrote {wav_dir / 'voice.json'} ({len(res['meta']['lines'])} sentences)")
    return 1 if res["missing"] else 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Japanese text on a cp932 console
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("speakers")
    sub.add_parser("models")
    p = sub.add_parser("reading")
    p.add_argument("script", type=Path)
    p.add_argument("--speaker", type=int, required=True)
    p.add_argument("--readings", type=Path)
    p = sub.add_parser("synth")
    p.add_argument("script", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--speaker", type=int, required=True)
    p.add_argument("--readings", type=Path)
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--only", nargs="+", metavar="FILE",
                   help="synthesize only these (e.g. N07_2.wav); keep the others from voice.json")
    p = sub.add_parser("adopt", help="measure recorded WAVs and write voice.json")
    p.add_argument("script", type=Path)
    p.add_argument("wav_dir", type=Path)
    p = sub.add_parser("edges", help="fade the room tone in and out at each recorded sentence's edges")
    p.add_argument("src", type=Path)
    p.add_argument("dst", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "speakers":
        for sp in speakers():
            for st in sp["styles"]:
                print(f"{st['id']:>12}  {sp['name']} / {st['name']}")
    elif a.cmd == "models":
        for uuid, m in models().items():
            man = m.get("manifest", {})
            print(f"{man.get('name', uuid)}  licence: {man.get('license', '?')!r}")
    elif a.cmd == "reading":
        lines, readings = load(a.script, a.readings)
        for ln in lines:
            print(f"{ln.cut:5s} {reading(apply_readings(ln.ja, readings), a.speaker)}")
    elif a.cmd == "synth":
        cmd_synth(a.script, a.out_dir, a.speaker, a.readings, a.speed, a.only)
    elif a.cmd == "adopt":
        return cmd_adopt(a.script, a.wav_dir)
    elif a.cmd == "edges":
        return cmd_edges(a.src, a.dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
