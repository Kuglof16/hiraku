"""Background music for the demo video: a breathing drone with scattered grains.

Every sound is synthesized here with numpy and scipy (no samples, no outside
music). There is no beat and no rhythm:

- a drone on D and A whose loudness slowly swells and recedes, each voice on
  its own long period
- a thin colour above it: one to three notes of the D major pentatonic scale
  for each stretch of the story, crossfaded from one stretch to the next
- grains: short soft notes on the same pitches two to three octaves higher,
  scattered at random times; more of them where the story opens up (the title
  N05, the mechanism from N09, N15-N17)
- a long reverb made from a decaying noise tail

The random numbers come from generators seeded by (seed, block of time), so a
stretch of music is the same whichever part is rendered and from where
(``--start``/``--end`` render a part). Grains also mark three moments of the
screens, at the times in screens/layout.json (the ones the pages use): the agents lighting in N00, the
vehicle bursting open in N05 and the light opening in N18.

    py promo/bgm.py render SCRIPT OUT.wav [--seed 1] [--start S] [--end E]
    py promo/bgm.py mix BGM.wav NARRATION.wav OUT.wav [--under-voice-lu -15] [--duck-db 0]
                                                      [--lufs -16] [--true-peak -3]

``mix`` keeps the music ``--under-voice-lu`` below the voice at one level all
through; ``--duck-db`` above 0 lets it come up by that much between sentences
instead. The sum is scaled to ``--lufs`` and its true peak is held at
``--true-peak`` by a short look-ahead limiter. Loudness and true peak are
measured with ffmpeg's ebur128 filter (ITU-R BS.1770).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage, signal

try:
    from promo.export import ffmpeg_path
    from promo.voice import Line, parse_script
except ImportError:  # run as a script: promo/ is on sys.path
    from export import ffmpeg_path
    from voice import Line, parse_script

RATE = 44100              # same as the narration from the engine
SCREENS = Path(__file__).resolve().parent / "screens"
PENTATONIC = (2, 4, 6, 9, 11)   # D E F# A B

# Drone voices: MIDI note, gain, breathing period (s), phase. The periods share
# no simple ratio, so the swells do not line up into a pattern.
DRONE = [
    (38, 0.50, 11.3, 0.0),   # D2
    (45, 0.36, 14.7, 2.1),   # A2
    (50, 0.18, 19.1, 4.0),   # D3
]
DRONE_PARTIALS = ((1, 1.0), (2, 0.45), (3, 0.2), (4, 0.08))
DRONE_DETUNE_CENTS = 2.0

# From the first cut of each stretch: colour notes (MIDI) and grains per second.
STORY = [
    ("N00", (66,), 0.7),
    ("N01", (69, 71), 0.9),
    ("N05", (66, 69, 76), 6.0),
    ("N06", (64, 71), 0.5),
    ("N09", (66, 71), 2.5),
    ("N15", (64, 69, 78), 4.5),
    ("N18", (66, 69, 76), 1.5),
]
COLOUR_GAIN = 0.10
COLOUR_FADE_S = 2.0       # each side of a stretch boundary
DENSITY_RAMP_S = 1.0      # each side of a stretch boundary

GRAIN_RANGE = (84, 104)   # MIDI: C6 to G#7
GRAIN_GAIN = 0.16
GRAIN_TAU_S = (0.08, 0.5)
GRAIN_MAX_S = 0.02 + 6 * GRAIN_TAU_S[1]
BLOCK_S = 0.5             # grains of one block share one generator

REVERB_RT60_S = 4.5
REVERB_WET = 0.45
PREROLL_S = REVERB_RT60_S * 1.1 + 0.1   # what a part needs before its start
FADE_IN_S, FADE_OUT_S = 4.0, 3.0
MASTER = 0.45

# Keys for the generators of the fixed events (block numbers stay far below).
KEY_REVERB, KEY_BURST = 10**6, 10**6 + 1


def midi_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def screen_time(screens: Path, cut: str, name: str) -> float:
    """A time of a screen in seconds from the start of its cut: layout.json
    ``<cut>.times.<name>_s`` (the same value the page's ``--t-<name>`` gets)."""
    layout = json.loads((screens / "layout.json").read_text(encoding="utf-8"))
    try:
        return float(layout[cut]["times"][name + "_s"])
    except KeyError:
        raise ValueError(f"{cut}.times.{name}_s not found in layout.json") from None


def cut_starts(lines: list[Line]) -> dict[str, float]:
    starts: dict[str, float] = {}
    for ln in lines:
        starts.setdefault(ln.cut, ln.start)
    return starts


# --- the story's stretches ---------------------------------------------------------

@dataclass
class Stretch:
    start: float
    end: float
    tones: tuple[int, ...]
    density: float


def stretches(lines: list[Line]) -> list[Stretch]:
    starts = cut_starts(lines)
    marks = [[starts[cut], tones, dens] for cut, tones, dens in STORY if cut in starts]
    marks[0][0] = 0.0
    ends = [m[0] for m in marks[1:]] + [max(ln.end for ln in lines)]
    return [Stretch(t, end, tones, dens) for (t, tones, dens), end in zip(marks, ends)]


def stretch_at(t: float, sts: list[Stretch]) -> Stretch:
    for s in reversed(sts):
        if t >= s.start:
            return s
    return sts[0]


def density_at(t, sts: list[Stretch]):
    """Grains per second, ramping across each boundary."""
    xs, ys = [sts[0].start], [sts[0].density]
    for a, b in zip(sts, sts[1:]):
        xs += [b.start - DENSITY_RAMP_S, b.start + DENSITY_RAMP_S]
        ys += [a.density, b.density]
    return np.interp(t, xs, ys)


def grain_pitches(s: Stretch) -> list[int]:
    """The stretch's colour notes and the drone's D and A, two to three octaves up."""
    pcs = sorted({m % 12 for m in s.tones} | {2, 9})
    return [m for m in range(GRAIN_RANGE[0], GRAIN_RANGE[1] + 1) if m % 12 in pcs]


# --- sounds -------------------------------------------------------------------

def smooth_step(x):
    return np.sin(0.5 * np.pi * np.clip(x, 0.0, 1.0)) ** 2


def breath(t, period: float, phase: float, floor: float = 0.3):
    return floor + (1 - floor) * (0.5 - 0.5 * np.cos(2 * np.pi * t / period + phase))


def drone(t: np.ndarray) -> np.ndarray:
    """D and A, each breathing on its own period; t is absolute time (s)."""
    out = np.zeros((len(t), 2))
    for m, gain, period, phase in DRONE:
        amp = gain * breath(t, period, phase)
        for k, a in DRONE_PARTIALS:
            for side, cents in ((0, DRONE_DETUNE_CENTS), (1, -DRONE_DETUNE_CENTS)):
                out[:, side] += amp * a * np.sin(2 * np.pi * midi_hz(m) * k * 2 ** (cents / 1200) * t)
    return out / sum(a for _, a in DRONE_PARTIALS)


def colour(t: np.ndarray, sts: list[Stretch]) -> np.ndarray:
    out = np.zeros((len(t), 2))
    for i, s in enumerate(sts):
        w = np.ones_like(t)
        if i > 0:
            w *= smooth_step((t - s.start + COLOUR_FADE_S) / (2 * COLOUR_FADE_S))
        if i < len(sts) - 1:
            w *= smooth_step((s.end + COLOUR_FADE_S - t) / (2 * COLOUR_FADE_S))
        live = np.nonzero(w > 0)[0]
        if not len(live):
            continue
        tt = t[live]
        for j, m in enumerate(s.tones):
            hz = midi_hz(m)
            tone = (np.sin(2 * np.pi * hz * tt) + 0.25 * np.sin(4 * np.pi * hz * tt)) / 1.25
            tone *= COLOUR_GAIN * w[live] * breath(tt, 9.0 + 2.3 * j, 1.7 * j, 0.5)
            p = (j - (len(s.tones) - 1) / 2) * 0.4
            out[live] += pan(tone, p)
    return out


@dataclass
class Grain:
    t: float
    midi: int
    gain: float
    pan: float
    attack: float
    tau: float


def grain_sound(g: Grain, rate: int) -> np.ndarray:
    n = int((g.attack + 6 * g.tau) * rate)
    t = np.arange(n) / rate
    env = np.where(t < g.attack, smooth_step(t / g.attack), np.exp(-(t - g.attack) / g.tau))
    hz = midi_hz(g.midi)
    tone = np.sin(2 * np.pi * hz * t)
    if 2 * hz < 0.45 * rate:
        tone += 0.18 * np.sin(4 * np.pi * hz * t) * np.exp(-t / (0.5 * g.tau))
    return pan(tone * env * g.gain / 1.18, g.pan)


def random_grains(sts: list[Stretch], seed: int, t0: float, t1: float) -> list[Grain]:
    """Grains sounding in [t0, t1). Block k uses the generator (seed, k), so
    the same block gives the same grains whichever part is rendered."""
    out = []
    for k in range(max(0, math.floor((t0 - GRAIN_MAX_S) / BLOCK_S)), math.ceil(t1 / BLOCK_S)):
        rng = np.random.default_rng([seed, k])
        a = k * BLOCK_S
        for _ in range(rng.poisson(float(density_at(a + BLOCK_S / 2, sts)) * BLOCK_S)):
            t = a + rng.uniform(0, BLOCK_S)
            pitches = grain_pitches(stretch_at(t, sts))
            out.append(Grain(t, pitches[rng.integers(len(pitches))], GRAIN_GAIN * rng.uniform(0.4, 1.0),
                             rng.uniform(-0.8, 0.8), rng.uniform(0.004, 0.012), rng.uniform(*GRAIN_TAU_S)))
    return out


def screen_grains(lines: list[Line], sts: list[Stretch], seed: int, screens: Path) -> list[Grain]:
    starts = cut_starts(lines)
    out = []
    if "N00" in starts:  # one grain per agent as its chip lights, rising
        t0, dt = screen_time(screens, "n00", "light"), screen_time(screens, "n00", "step")
        for i, m in enumerate([86, 88, 90, 93, 95, 98, 100]):
            out.append(Grain(starts["N00"] + t0 + i * dt, m, GRAIN_GAIN * 0.9, -0.6 + 0.2 * i, 0.006, 0.6))
    if "N05" in starts:  # a spray as the vehicle bursts open
        rng = np.random.default_rng([seed, KEY_BURST])
        t = starts["N05"] + screen_time(screens, "n05", "burst")
        pitches = grain_pitches(stretch_at(t + 0.01, sts))
        for _ in range(14):
            out.append(Grain(t, pitches[rng.integers(len(pitches))], GRAIN_GAIN * rng.uniform(0.6, 1.0),
                             rng.uniform(-0.9, 0.9), 0.004, rng.uniform(0.2, 0.6)))
            t += rng.exponential(0.09)
    if "N18" in starts:  # the light opens
        t = starts["N18"] + screen_time(screens, "n18", "open")
        for i, m in enumerate([86, 90, 93, 98, 102]):
            out.append(Grain(t + 0.12 * i, m, GRAIN_GAIN * 0.8, -0.4 + 0.2 * i, 0.008, 0.7))
    return out


def reverb(dry: np.ndarray, rate: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng([seed, KEY_REVERB])
    n = int(REVERB_RT60_S * 1.1 * rate)
    t = np.arange(n) / rate
    wet = np.zeros_like(dry)
    for ch in range(2):
        ir = signal.sosfilt(signal.butter(2, min(4000, 0.45 * rate), fs=rate, output="sos"), rng.standard_normal(n))
        ir *= np.exp(-t * 6.91 / REVERB_RT60_S) * smooth_step(t / 0.03)
        ir /= np.sqrt((ir ** 2).sum())
        wet[:, ch] = signal.fftconvolve(dry[:, ch], ir)[: len(dry)]
    return wet


def pan(mono: np.ndarray, p: float) -> np.ndarray:
    a = (p + 1) * np.pi / 4
    return np.stack([mono * np.cos(a), mono * np.sin(a)], 1)


def add_at(buf: np.ndarray, i: int, sig: np.ndarray) -> None:
    if i < 0:
        sig, i = sig[-i:], 0
    m = min(len(sig), len(buf) - i)
    if m > 0:
        buf[i:i + m] += sig[:m]


def render(lines: list[Line], rate: int = RATE, seed: int = 1, start: float = 0.0, end: float | None = None,
           screens: Path = SCREENS) -> tuple[np.ndarray, list[Stretch], int]:
    """The music from ``start`` to ``end`` (s), and the stretches and the number
    of grains. A part equals the same samples of the whole."""
    sts = stretches(lines)
    total = sts[-1].end
    i_start = round(start * rate)
    i_end = round((total if end is None else min(end, total)) * rate)
    i0 = max(0, i_start - round(PREROLL_S * rate))
    t = (i0 + np.arange(i_end - i0)) / rate
    dry = drone(t) + colour(t, sts)
    grains = random_grains(sts, seed, t[0], t[-1] + 1 / rate) + screen_grains(lines, sts, seed, screens)
    for g in grains:
        add_at(dry, round(g.t * rate) - i0, grain_sound(g, rate))
    out = dry + REVERB_WET * reverb(dry, rate, seed)
    out *= (smooth_step(t / FADE_IN_S) * smooth_step((total - t) / FADE_OUT_S) * MASTER)[:, None]
    shown = [g for g in grains if i_start <= round(g.t * rate) < i_end]
    return out[i_start - i0:], sts, len(shown)


# --- mixing with the voice ----------------------------------------------------

def parse_ebur128(text: str) -> tuple[float, float]:
    """Integrated loudness (LUFS) and true peak (dBFS) from ffmpeg's ebur128 summary."""
    summary = text[text.rindex("Summary:"):]
    i = re.search(r"I:\s+(-?[0-9.]+|-inf) LUFS", summary)
    p = re.search(r"Peak:\s+(-?[0-9.]+|-inf) dBFS", summary)
    if not i or not p:
        raise ValueError("no integrated loudness or true peak in the ebur128 summary")
    return float(i.group(1)), float(p.group(1))


def ebur128(x: np.ndarray, rate: int) -> tuple[float, float]:
    """Integrated loudness and true peak of a stereo signal, measured by ffmpeg."""
    cmd = [ffmpeg_path(), "-hide_banner", "-nostats", "-f", "f32le", "-ar", str(rate), "-ac", "2",
           "-i", "-", "-af", "ebur128=peak=true", "-f", "null", "-"]
    proc = subprocess.run(cmd, input=np.ascontiguousarray(x, dtype="<f4").tobytes(), capture_output=True, check=True)
    return parse_ebur128(proc.stderr.decode("utf-8", "replace"))


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


def limit(x: np.ndarray, rate: int, ceiling_db: float, window_s: float = 0.01) -> tuple[np.ndarray, float]:
    """Hold sample peaks at ``ceiling_db``. The gain starts going down
    ``window_s`` before a peak and comes back as long after it. Returns the
    signal and the deepest gain reduction (dB)."""
    need = np.minimum(1.0, 10 ** (ceiling_db / 20) / np.maximum(np.abs(x).max(1), 1e-12))
    n = max(1, int(window_s * rate))
    g = ndimage.minimum_filter1d(need, 2 * n + 1, mode="nearest")
    g = ndimage.uniform_filter1d(g, n + 1, mode="nearest")
    return x * g[:, None], float(20 * np.log10(g.min()))


def db(x: float) -> float:
    return 10 ** (x / 20)


def mix(music: np.ndarray, voice: np.ndarray, rate: int, under_voice_lu: float = -15.0, duck_db: float = 0.0,
        lufs: float = -16.0, true_peak: float = -3.0, measure=ebur128) -> tuple[np.ndarray, dict]:
    n = len(voice)
    music = np.concatenate([music[:n], np.zeros((max(0, n - len(music)), 2))])
    speech = np.stack([voice, voice], 1)
    lv, _ = measure(speech, rate)
    lm, _ = measure(music, rate)
    music = music * db(lv + under_voice_lu + duck_db - lm) * duck_gain(voice, rate, duck_db)[:, None]
    out = music + speech
    scale, reduction = 1.0, 0.0
    for _ in range(6):
        level, tp = measure(out, rate)
        if abs(level - lufs) <= 0.1 and tp <= true_peak + 0.1:
            break
        out, scale = out * db(lufs - level), scale * db(lufs - level)
        tp += lufs - level
        if tp > true_peak:
            peak_db = 20 * np.log10(np.abs(out).max())
            out, r = limit(out, rate, peak_db - (tp - true_peak) - 0.2)
            reduction = min(reduction, r)
    level, tp = measure(out, rate)
    return out, {"lufs": level, "true_peak": tp, "voice_lufs": lv + 20 * np.log10(scale),
                 "music_lufs": measure(music * scale, rate)[0], "limited_db": reduction}


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
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--end", type=float, default=None)
    p = sub.add_parser("mix")
    p.add_argument("music", type=Path)
    p.add_argument("voice", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--under-voice-lu", type=float, default=-15.0, help="music under the voice, against the voice")
    p.add_argument("--duck-db", type=float, default=0.0,
                   help="how far the music comes up between sentences (0: one level all through)")
    p.add_argument("--lufs", type=float, default=-16.0)
    p.add_argument("--true-peak", type=float, default=-3.0)
    a = ap.parse_args(argv)

    if a.cmd == "render":
        lines = parse_script(a.script.read_text(encoding="utf-8"))
        music, sts, count = render(lines, seed=a.seed, start=a.start, end=a.end)
        write_wav(a.out, music, RATE)
        for s in sts:
            print(f"{s.start:7.2f} - {s.end:7.2f}  {s.density:3.1f} grains/s  colour {s.tones}")
        print(f"wrote {a.out} ({len(music) / RATE:.2f} s from {a.start:g} s, {count} grains, "
              f"peak {20 * np.log10(np.abs(music).max()):.1f} dBFS)")
    elif a.cmd == "mix":
        music, r1 = read_wav(a.music)
        voice, r2 = read_wav(a.voice)
        if r1 != r2 or voice.ndim != 1:
            raise SystemExit("the music and a mono voice must have the same sample rate")
        out, rep = mix(music, voice, r1, a.under_voice_lu, a.duck_db, a.lufs, a.true_peak)
        write_wav(a.out, out, r1)
        print(f"wrote {a.out}: {rep['lufs']:.1f} LUFS, true peak {rep['true_peak']:.1f} dBFS; "
              f"voice {rep['voice_lufs']:.1f} LUFS, music {rep['music_lufs']:.1f} LUFS; "
              f"limiter at most {rep['limited_db']:.1f} dB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
