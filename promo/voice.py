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

``synth`` writes one WAV per sentence and ``voice.json`` (file, seconds,
text), then prints how each cut's voice fits its slot. ``--only`` remakes
just the named sentences and keeps the others as they were.
The engine address is http://127.0.0.1:10101 unless AIVIS_URL is set.
"""

from __future__ import annotations

import argparse
import json
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
