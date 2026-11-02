"""Background music for the demo video, made from scratch in this file.

Every sound is synthesized here with numpy and scipy (no samples, no outside
music): a pad of detuned band-limited oscillators, plucked notes built from
decaying partials, a sine bass, short noise hats, a noise riser and a low hit,
and a reverb made from a decaying noise tail. The arrangement follows the cuts
of the narration script, so the music changes where the picture changes. Three
moments take their time from the screens themselves: the agents lighting in
N00, the vehicle bursting open in N05 and the light opening in N18.

    py promo/bgm.py render SCRIPT OUT.wav [--seed 1]
    py promo/bgm.py mix BGM.wav NARRATION.wav OUT.wav [--music-db -24] [--duck-db 9]

``mix`` sets the music's level, lowers it under the voice (it starts going down
just before each sentence and comes back after a pause), adds the voice and
scales the sum so that its peak is -1 dBFS.
"""

from __future__ import annotations

import argparse
import re
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import signal

try:
    from promo.voice import Line, parse_script
except ImportError:  # run as a script: promo/ is on sys.path
    from voice import Line, parse_script

RATE = 44100              # same as the narration from the engine
BPM = 92.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT
SCREENS = Path(__file__).resolve().parent / "screens"

# First cut of each section, its style, and the chords, one per bar, repeated.
PLAN = [
    ("N00", "intro", ["Dadd9"]),
    ("N01", "question", ["Bm", "G", "D", "A"]),
    ("N05", "title", ["Dadd9"]),
    ("N06", "problem", ["Bm", "G", "Em", "F#m"]),
    ("N09", "work", ["D", "A", "Bm", "G"]),
    ("N14", "build", ["G", "A", "Bm", "D"]),
    ("N17", "finale", ["G", "A"]),
    ("N18", "end", ["Dadd9"]),
]

# Layer gains per style. arp_rate: notes per beat; bass_every: beats per bass note.
STYLES = {
    "intro":    dict(pad=0.50, cutoff=1200, attack=2.5),
    "question": dict(pad=0.30, cutoff=1800, arp=0.30, arp_rate=2, bass=0.22, bass_every=4, hat=0.12, ramp=True),
    "title":    dict(pad=0.60, cutoff=2600, attack=0.03, bass=0.28, bass_every=8),
    "problem":  dict(pad=0.32, cutoff=900, bass=0.18, bass_every=4, keys=0.20),
    "work":     dict(pad=0.32, cutoff=1800, arp=0.28, arp_rate=2, bass=0.22, bass_every=2, hat=0.12),
    "build":    dict(pad=0.36, cutoff=2400, arp=0.24, arp_rate=4, bass=0.26, bass_every=1, hat=0.15),
    "finale":   dict(pad=0.46, cutoff=2200, arp=0.22, arp_rate=2, bass=0.22, bass_every=4),
    "end":      dict(pad=0.55, cutoff=2000, attack=0.6),
}
PAD_RELEASE_S = 1.2
REVERB_WET = 0.22
FADE_OUT_S = 2.0

NOTE_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
QUALITY = {"": (0, 4, 7), "m": (0, 3, 7), "add9": (0, 4, 7, 14), "sus2": (0, 2, 7)}


# --- harmony and form -----------------------------------------------------------

def midi_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def parse_chord(name: str) -> tuple[int, tuple[int, ...]]:
    pc, rest = NOTE_PC[name[0]], name[1:]
    if rest[:1] == "#":
        pc, rest = pc + 1, rest[1:]
    elif rest[:1] == "b":
        pc, rest = pc - 1, rest[1:]
    return pc % 12, QUALITY[rest]


def chord_notes(name: str, low: int = 57) -> tuple[list[int], int]:
    """Close voicing within the octave from ``low`` (MIDI), and the root in octave 2."""
    pc, intervals = parse_chord(name)
    upper = sorted({low + (pc + iv - low) % 12 for iv in intervals})
    return upper, 36 + (pc - 36) % 12


def css_time(page: Path, name: str) -> float:
    """A '--t-x: 0.95s;' custom property of a screen, in seconds."""
    m = re.search(re.escape(name) + r"\s*:\s*([0-9.]+)s", page.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{name} not found in {page.name}")
    return float(m.group(1))


@dataclass
class Section:
    style: str
    start: float
    end: float
    chords: list[str]


def cut_starts(lines: list[Line]) -> dict[str, float]:
    starts: dict[str, float] = {}
    for ln in lines:
        starts.setdefault(ln.cut, ln.start)
    return starts


def snap(t: float) -> float:
    return round(t / BEAT) * BEAT


def plan_sections(lines: list[Line], burst_s: float) -> list[Section]:
    """Sections start on the beat nearest their first cut; the title starts
    exactly when the vehicle bursts open."""
    starts = cut_starts(lines)
    marks = []
    for cut, style, chords in PLAN:
        if cut in starts:
            t = starts[cut] + burst_s if style == "title" else snap(starts[cut])
            marks.append([t, style, chords])
    marks[0][0] = 0.0
    total = max(ln.end for ln in lines)
    ends = [m[0] for m in marks[1:]] + [total]
    return [Section(style, t, end, chords) for (t, style, chords), end in zip(marks, ends)]


def bars(sec: Section):
    t, i = sec.start, 0
    while t < sec.end - 1e-6:
        yield t, min(t + BAR, sec.end), sec.chords[i % len(sec.chords)]
        t, i = t + BAR, i + 1


def chord_segments(sec: Section):
    """Bars merged while the chord stays the same."""
    out: list[list] = []
    for a, b, chord in bars(sec):
        if out and out[-1][2] == chord:
            out[-1][1] = b
        else:
            out.append([a, b, chord])
    return out


# --- sounds -----------------------------------------------------------------

def _sos(kind: str, hz: float, rate: int, order: int = 2):
    return signal.butter(order, min(hz, 0.45 * rate), btype=kind, fs=rate, output="sos")


def wavetable(harmonics: int = 6, size: int = 4096) -> np.ndarray:
    """One cycle of a soft, band-limited saw."""
    ph = np.arange(size) / size
    k = np.arange(1, harmonics + 1)[:, None]
    wave_ = (np.sin(2 * np.pi * k * ph) / k ** 1.4).sum(0)
    return wave_ / np.abs(wave_).max()


def osc(table: np.ndarray, hz: float, n: int, rate: int, phase: float = 0.0) -> np.ndarray:
    idx = ((phase + hz * np.arange(n) / rate) % 1.0) * len(table)
    return table[idx.astype(int)]


def envelope(n: int, rate: int, attack: float, hold: float, release: float) -> np.ndarray:
    t = np.arange(n) / rate
    up = np.clip(t / max(attack, 1e-4), 0, 1)
    down = np.clip(1 - (t - attack - hold) / release, 0, 1)
    return np.sin(0.5 * np.pi * up) ** 2 * np.sin(0.5 * np.pi * down) ** 2


def pad_note(table, hz, dur, rate, attack, cutoff) -> np.ndarray:
    n = int((dur + PAD_RELEASE_S) * rate)
    env = envelope(n, rate, attack, max(dur - attack, 0.0), PAD_RELEASE_S)
    sos = _sos("lowpass", cutoff, rate)
    left = signal.sosfilt(sos, osc(table, hz * 2 ** (7 / 1200), n, rate))
    right = signal.sosfilt(sos, osc(table, hz * 2 ** (-7 / 1200), n, rate, 0.37))
    return np.stack([left, right], 1) * env[:, None]


def pluck(hz: float, dur: float, rate: int, decay: float = 3.0, partials: int = 6) -> np.ndarray:
    """Decaying partials; the higher ones die away faster."""
    t = np.arange(int(dur * rate)) / rate
    out = np.zeros_like(t)
    norm = 0.0
    for k in range(1, partials + 1):
        if hz * k > 0.45 * rate:
            break
        amp = 1 / k ** 1.3
        out += amp * np.sin(2 * np.pi * hz * k * t) * np.exp(-t * decay * (1 + 0.5 * (k - 1)))
        norm += amp
    return out * np.clip(t / 0.004, 0, 1) / norm


def bass_note(hz: float, dur: float, rate: int) -> np.ndarray:
    n = int((dur + 0.2) * rate)
    t = np.arange(n) / rate
    tone = np.sin(2 * np.pi * hz * t) + 0.3 * np.sin(4 * np.pi * hz * t)
    return tone * envelope(n, rate, 0.012, max(dur - 0.012, 0.0), 0.2) / 1.3


def hat(rate: int, rng: np.random.Generator) -> np.ndarray:
    n = int(0.08 * rate)
    t = np.arange(n) / rate
    noise = signal.sosfilt(_sos("highpass", 6500, rate), rng.standard_normal(n))
    return noise * np.exp(-t * 55) / np.abs(noise).max()


def boom(rate: int) -> np.ndarray:
    t = np.arange(int(1.6 * rate)) / rate
    hz = 42 + 50 * np.exp(-t * 8)
    return np.sin(2 * np.pi * np.cumsum(hz) / rate) * np.exp(-t * 2.2) * np.clip(t / 0.003, 0, 1)


def riser(dur: float, rate: int, rng: np.random.Generator) -> np.ndarray:
    """Noise that grows and brightens, ending at the hit."""
    n = int(dur * rate)
    x = (np.arange(n) / n) ** 2
    out = []
    for _ in range(2):
        noise = rng.standard_normal(n)
        dark = signal.sosfilt(_sos("lowpass", 600, rate), noise)
        bright = signal.sosfilt(_sos("lowpass", 6000, rate), noise)
        out.append(((1 - x) * dark + x * bright) * x)
    sig = np.stack(out, 1)
    sig[-int(0.02 * rate):] *= np.linspace(1, 0, int(0.02 * rate))[:, None]
    return sig / np.abs(sig).max()


def reverb(dry: np.ndarray, rate: int, rng: np.random.Generator, rt60: float = 2.2) -> np.ndarray:
    n = int(rt60 * 1.1 * rate)
    t = np.arange(n) / rate
    wet = np.zeros_like(dry)
    for ch in range(2):
        ir = signal.sosfilt(_sos("lowpass", 5000, rate), rng.standard_normal(n)) * np.exp(-t * 6.91 / rt60)
        ir /= np.sqrt((ir ** 2).sum())
        wet[:, ch] = signal.fftconvolve(dry[:, ch], ir)[: len(dry)]
    return wet


def pan(mono: np.ndarray, p: float) -> np.ndarray:
    a = (p + 1) * np.pi / 4
    return np.stack([mono * np.cos(a), mono * np.sin(a)], 1)


def add(buf: np.ndarray, t: float, sig: np.ndarray, rate: int) -> None:
    i = int(round(t * rate))
    if i < 0:
        sig, i = sig[-i:], 0
    m = min(len(sig), len(buf) - i)
    if m > 0:
        buf[i:i + m] += sig[:m]


# --- arrangement --------------------------------------------------------------

def render(lines: list[Line], rate: int = RATE, seed: int = 1,
           screens: Path = SCREENS) -> tuple[np.ndarray, list[Section]]:
    rng = np.random.default_rng(seed)
    secs = plan_sections(lines, css_time(screens / "n05.html", "--t-burst"))
    total = secs[-1].end
    dry = np.zeros((int(round(total * rate)), 2))
    table = wavetable()
    tick = hat(rate, rng)

    for sec in secs:
        st = STYLES[sec.style]
        for a, b, chord in chord_segments(sec):
            upper, bass = chord_notes(chord)
            notes = [bass + 12] + upper
            for m in notes:
                note = pad_note(table, midi_hz(m), b - a, rate, st.get("attack", 0.4), st["cutoff"])
                add(dry, a, note * st["pad"] / len(notes), rate)
        for k, (a, b, chord) in enumerate(bars(sec)):
            upper, bass = chord_notes(chord)
            grow = 0.45 + 0.55 * (a - sec.start) / (sec.end - sec.start) if st.get("ramp") else 1.0
            if "arp" in st:
                tones = upper + [upper[0] + 12]
                seq = tones + tones[-2:0:-1]
                step = BEAT / st["arp_rate"]
                for j, t in enumerate(np.arange(a, b - 1e-6, step)):
                    g = st["arp"] * grow * rng.uniform(0.85, 1.0)
                    add(dry, t, pan(pluck(midi_hz(seq[j % len(seq)] + 12), 0.9, rate), 0.3 * (-1) ** j) * g, rate)
            if "bass" in st:
                every = st["bass_every"] * BEAT
                for t in np.arange(a, b - 1e-6, every):
                    add(dry, t, pan(bass_note(midi_hz(bass), min(every, b - t) * 0.95, rate), 0.0) * st["bass"], rate)
            if "hat" in st and not (st.get("ramp") and k < 2):
                for t in np.arange(a + BEAT / 2, b - 1e-6, BEAT):
                    add(dry, t, pan(tick, 0.25) * st["hat"] * rng.uniform(0.7, 1.0), rate)
            if "keys" in st:
                add(dry, a, pan(pluck(midi_hz(upper[-1] + 12), 3.0, rate, decay=1.2), -0.2) * st["keys"], rate)

    starts = cut_starts(lines)
    if "N00" in starts:  # one bell per agent as its chip lights
        page = screens / "n00.html"
        t0, dt = css_time(page, "--t-light"), css_time(page, "--t-step")
        for i, m in enumerate([74, 76, 78, 81, 83, 86, 88]):
            add(dry, starts["N00"] + t0 + i * dt, pan(pluck(midi_hz(m), 2.5, rate, decay=1.6), -0.6 + 0.2 * i) * 0.20, rate)
    title = next((s for s in secs if s.style == "title"), None)
    if title:  # the vehicle bursts open
        add(dry, title.start - 2.4, riser(2.4, rate, rng) * 0.22, rate)
        add(dry, title.start, pan(boom(rate), 0.0) * 0.55, rate)
    if "N18" in starts:  # the light opens
        t = starts["N18"] + css_time(screens / "n18.html", "--t-open")
        for i, m in enumerate([74, 81, 86]):
            add(dry, t + 0.06 * i, pan(pluck(midi_hz(m), 3.5, rate, decay=1.0), -0.3 + 0.3 * i) * 0.16, rate)

    out = dry + REVERB_WET * reverb(dry, rate, rng)
    fade = int(FADE_OUT_S * rate)
    out[-fade:] *= np.cos(np.linspace(0, 0.5 * np.pi, fade))[:, None] ** 2
    return out * (10 ** (-1 / 20) / np.abs(out).max()), secs


# --- mixing with the voice ----------------------------------------------------

def level_db(x: np.ndarray, rate: int, window_s: float = 0.4, floor_db: float = -60.0) -> float:
    """Mean power of the windows that are not silent, in dBFS."""
    mono = x.mean(1) if x.ndim == 2 else x
    w = int(window_s * rate)
    power = (mono[: len(mono) // w * w].reshape(-1, w) ** 2).mean(1)
    loud = power[power > 10 ** (floor_db / 10)]
    return float(10 * np.log10(loud.mean())) if len(loud) else floor_db


def duck_gain(voice: np.ndarray, rate: int, depth_db: float, threshold_db: float = -35.0,
              attack_s: float = 0.3, hold_s: float = 0.6, window_s: float = 0.02) -> np.ndarray:
    """Gain for the music: down by ``depth_db`` while the voice speaks. It is
    fully down before a sentence starts and stays down over short pauses."""
    w = max(1, int(window_s * rate))
    m = len(voice) // w
    rms = np.sqrt((voice[: m * w].reshape(m, w) ** 2).mean(1))
    active = (20 * np.log10(rms + 1e-9) > threshold_db).astype(float)
    back, fwd = int(round(attack_s / window_s)), int(round(hold_s / window_s))
    covered = np.convolve(active, np.ones(back + fwd + 1))[back: back + m] > 0
    r = max(1, back)
    curve = np.convolve(covered.astype(float), np.ones(r) / r, mode="same")
    curve = np.concatenate([np.repeat(curve, w), np.full(len(voice) - m * w, curve[-1] if m else 0.0)])
    return 10 ** (-depth_db * curve / 20)


def mix(music: np.ndarray, voice: np.ndarray, rate: int, music_db: float = -24.0,
        duck_db: float = 9.0, peak_db: float = -1.0) -> np.ndarray:
    n = len(voice)
    music = np.concatenate([music[:n], np.zeros((max(0, n - len(music)), 2))])
    music = music * 10 ** ((music_db - level_db(music, rate)) / 20)
    out = music * duck_gain(voice, rate, duck_db)[:, None] + voice[:, None]
    peak, target = np.abs(out).max(), 10 ** (peak_db / 20)
    return out * (target / peak) if peak > target else out


# --- files and commands -------------------------------------------------------

def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: expected 16-bit PCM")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32768
        ch, rate = w.getnchannels(), w.getframerate()
    return (x.reshape(-1, ch) if ch > 1 else x), rate


def write_wav(path: Path, x: np.ndarray, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(np.round(x * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1] if x.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("render")
    p.add_argument("script", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--seed", type=int, default=1)
    p = sub.add_parser("mix")
    p.add_argument("music", type=Path)
    p.add_argument("voice", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--music-db", type=float, default=-24.0, help="music level where nobody speaks (dBFS)")
    p.add_argument("--duck-db", type=float, default=9.0, help="how far the music goes down under the voice")
    a = ap.parse_args(argv)

    if a.cmd == "render":
        music, secs = render(parse_script(a.script.read_text(encoding="utf-8")), seed=a.seed)
        write_wav(a.out, music, RATE)
        for s in secs:
            print(f"{s.style:9s} {s.start:7.2f} - {s.end:7.2f}  {' '.join(s.chords)}")
        print(f"wrote {a.out} ({len(music) / RATE:.2f} s, {BPM:g} BPM)")
    elif a.cmd == "mix":
        music, r1 = read_wav(a.music)
        voice, r2 = read_wav(a.voice)
        if r1 != r2 or voice.ndim != 1:
            raise SystemExit("the music and a mono voice must have the same sample rate")
        out = mix(music, voice, r1, a.music_db, a.duck_db)
        write_wav(a.out, out, r1)
        print(f"wrote {a.out}: music {a.music_db:g} dBFS, {a.duck_db:g} dB lower under the voice")
    return 0


if __name__ == "__main__":
    sys.exit(main())
