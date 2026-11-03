"""A first-time viewer's look at the video's cuts and the app's screens, judged by an image model.

    python -m qa.first_look --work WORK --out OUT [--assembly promo/assembly.json]
                            [--video NAME=FILE ...] [--images DIR ...] [--judge codex|none]

Collects one or more stills per item: each segment of the video's assembly table (a still as
it is; a clip at 30 % and 70 % of its length, via ffmpeg), the --video files (name=path, five
frames each) and the images in --images folders. Puts them 8 to a contact sheet with the id
burned in at the top left, and asks the judge, for every tile in order:
- what the picture says, as a first-time viewer would put it, and whether that is clear
  within 3 seconds (0-5)
- where the eye goes first
- labels that cannot be read, and things that overlap
- what to change
Answers are matched to the tiles by position (an image model may misread the ids; a reported id
that differs is kept as id_reported). Writes OUT/first_look.json and OUT/first_look.md (the
list of fixes) and says which judge (and model) gave them.

The judge is the Codex CLI (`codex exec -i sheet`, prompt on stdin, OPENAI_API_KEY removed so
it runs on the ChatGPT plan), 8 sheets at a time; its model is read from ~/.codex/config.toml.
--judge none only makes the sheets. Needs ffmpeg (on PATH or in FFMPEG) for clips and Pillow
for the sheets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PER_SHEET = 8
TILE_W = 960
PROMPT = """これは動画とアプリの画面の絵を {n} 枚並べた 1 枚のシートです。左上から右へ、上の段から順に並んでいます。各絵の上の黄色い帯に ID があります (帯は絵の一部ではないので、評価に入れないでください)。
あなたは、この製品 (車などの設計を AI が試験しながら直す道具) を初めて見る人です。説明は聞いていません。絵ごとに、次を日本語で答えてください。
- what: 初めて見た人として、この絵が何を言っていると思うか (1 文)
- clear_3s: 3 秒見てそれが分かるか。0 (全く分からない) から 5 (すぐ分かる)
- eye_first: 目が最初に行く所
- unreadable: 小さすぎる・薄い・切れているなどで読めない文字や札 (無ければ空の配列)
- overlaps: 重なって見えにくい物 (無ければ空の配列)
- busy: 情報が多すぎて焦点がぼけているか (true / false)
- fix: 初めての人に伝わるようにする直し方の案 (具体的に 1〜2 文)
答えは JSON の配列だけにしてください。絵の並び順のとおりに {n} 個、各要素は {{"id": 札の ID, "what": ..., "clear_3s": ..., "eye_first": ..., "unreadable": [...], "overlaps": [...], "busy": ..., "fix": ...}} です。"""


def ffmpeg() -> str:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is not on PATH (or set FFMPEG)")
    return exe


def duration(video: Path) -> float:
    err = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(video)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if not m:
        raise RuntimeError(f"no duration for {video}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def frame(video: Path, t: float, out: Path) -> Path:
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                    "-frames:v", "1", str(out)], check=True)
    return out


def collect(args, shots: Path) -> list[dict]:
    """Items: {id, name, source, image}."""
    items: list[dict] = []
    shots.mkdir(parents=True, exist_ok=True)

    def add(ident: str, name: str, source: str, image: Path):
        items.append({"id": ident, "name": name, "source": source, "image": str(image)})

    if args.assembly and args.work:
        table = json.loads(Path(args.assembly).read_text(encoding="utf-8"))
        for seg in table.get("segments", []):
            name = "+".join(seg.get("cuts", [])) or "?"
            if seg.get("still"):
                p = Path(seg["still"].replace("${WORK}", args.work))
                if p.exists():
                    add(name, name, str(p), p)
            elif seg.get("video"):
                v = seg["video"]
                v = v.get("1080") or next(iter(v.values())) if isinstance(v, dict) else v
                p = Path(v.replace("${WORK}", args.work))
                if p.exists():
                    d = duration(p)
                    for k, f in enumerate((0.3, 0.7), start=1):
                        add(f"{name}-{k}", name, f"{p.name} at {f * d:.1f} s",
                            frame(p, f * d, shots / f"{name}-{k}.png"))
    for spec in args.video or []:
        name, _, path = spec.partition("=")
        p = Path(path)
        d = duration(p)
        for k in range(1, 6):
            t = d * k / 6
            add(f"{name}-{k}", name, f"{p.name} at {t:.1f} s", frame(p, t, shots / f"{name}-{k}.png"))
    for folder in args.images or []:
        for p in sorted(Path(folder).glob("*.png")) + sorted(Path(folder).glob("*.jpg")):
            add(p.stem, p.stem, str(p), p)
    return items


def sheets(items: list[dict], out: Path) -> list[tuple[Path, list[dict]]]:
    from PIL import Image, ImageDraw, ImageFont

    out.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    made = []
    for s in range(0, len(items), PER_SHEET):
        part = items[s:s + PER_SHEET]
        tiles = []
        for it in part:
            with Image.open(it["image"]) as src:
                im = src.convert("RGB")
            im = im.resize((TILE_W, round(im.height * TILE_W / im.width)))
            tile = Image.new("RGB", (TILE_W, im.height + 56), (255, 230, 0))  # the id on a strip above, off the picture
            tile.paste(im, (0, 56))
            ImageDraw.Draw(tile).text((10, 6), it["id"], fill=(0, 0, 0), font=font)
            tiles.append(tile)
        h = max(t.height for t in tiles)
        cols = 4
        rows = (len(tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * TILE_W + (cols - 1) * 8, rows * h + (rows - 1) * 8), (255, 255, 255))
        for k, t in enumerate(tiles):
            sheet.paste(t, ((k % cols) * (TILE_W + 8), (k // cols) * (h + 8)))
        path = out / f"sheet_{s // PER_SHEET + 1:02d}.jpg"
        sheet.save(path, quality=88)
        made.append((path, part))
    return made


def codex_model() -> str:
    cfg = Path.home() / ".codex" / "config.toml"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return "unknown (no ~/.codex/config.toml)"
    model = re.search(r'^model\s*=\s*"([^"]+)"', text, re.M)
    effort = re.search(r'^model_reasoning_effort\s*=\s*"([^"]+)"', text, re.M)
    return f"{model.group(1) if model else '?'} (reasoning {effort.group(1) if effort else '?'})"


def codex_exe() -> str | None:
    """The Codex CLI: on PATH, or where npm puts it on Windows; None when it is not installed."""
    found = shutil.which("codex.cmd") or shutil.which("codex")
    if found:
        return found
    npm = Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd"
    return str(npm) if os.environ.get("APPDATA") and npm.is_file() else None


def ask_codex(sheet: Path, n: int) -> list[dict]:
    exe = codex_exe()
    if exe is None:
        raise RuntimeError("the Codex CLI is not installed (not on PATH, not in %APPDATA%/npm)")
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    proc = subprocess.run([exe, "exec", "--skip-git-repo-check", "-i", str(sheet)], input=PROMPT.format(n=n),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900)
    text = proc.stdout
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise RuntimeError(f"no JSON array from codex for {sheet.name}: {(proc.stderr or text)[-500:]}")
    return json.loads(text[start:end + 1])


def judge(made: list[tuple[Path, list[dict]]]) -> list[dict]:
    def one(entry):
        sheet, part = entry
        try:
            answers = ask_codex(sheet, len(part))
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            return [dict(it, sheet=sheet.name, error=str(e)) for it in part]
        out = []
        for k, it in enumerate(part):
            a = answers[k] if k < len(answers) else {}
            rec = dict(it, sheet=sheet.name, **{key: a.get(key) for key in
                                                ("what", "clear_3s", "eye_first", "unreadable", "overlaps", "busy", "fix")})
            if a.get("id") not in (None, it["id"]):
                rec["id_reported"] = a.get("id")
            if len(answers) != len(part):
                rec["warning"] = f"{len(answers)} answers for {len(part)} pictures: matched by position"
            out.append(rec)
        return out

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [r for rs in pool.map(one, made) for r in rs]


def report(results: list[dict], judge_name: str, out: Path) -> None:
    (out / "first_look.json").write_text(json.dumps({"judge": judge_name, "items": results}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    lines = [f"# 初めて見る人の目での点検", "", f"判定: {judge_name}", "",
             "| ID | 何を言っている絵か (判定) | 3 秒 | 目が行く所 | 読めない札・重なり | 直し方の案 |", "|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (r.get("clear_3s") if isinstance(r.get("clear_3s"), (int, float)) else 9, r["id"])):
        bad = "、".join([*(r.get("unreadable") or []), *(r.get("overlaps") or [])]) or "-"
        cell = lambda s: str(s if s is not None else r.get("error", "-")).replace("|", "/").replace("\n", " ")
        lines.append(f"| {r['id']} | {cell(r.get('what'))} | {cell(r.get('clear_3s'))}{' 多い' if r.get('busy') else ''} | "
                     f"{cell(r.get('eye_first'))} | {cell(bad)} | {cell(r.get('fix'))} |")
    (out / "first_look.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qa.first_look", description=__doc__.split("\n\n")[0])
    ap.add_argument("--work", help="the folder ${WORK} stands for in the assembly table")
    ap.add_argument("--assembly", default=str(ROOT / "promo" / "assembly.json"))
    ap.add_argument("--video", action="append", help="NAME=FILE: five frames of a video (e.g. the app's replay)")
    ap.add_argument("--images", action="append", help="a folder of PNG/JPG screens")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--judge", choices=("codex", "none"), default="codex")
    args = ap.parse_args(argv)
    items = collect(args, args.out / "shots")
    if not items:
        print("nothing to look at", file=sys.stderr)
        return 2
    made = sheets(items, args.out / "sheets")
    print(f"{len(items)} pictures on {len(made)} sheets in {args.out / 'sheets'}")
    if args.judge == "none":
        return 0
    if codex_exe() is None:
        print("the Codex CLI is not installed (not on PATH, not in %APPDATA%/npm): install it, or use --judge none "
              "to make the sheets only", file=sys.stderr)
        return 2
    name = f"Codex CLI, {codex_model()}"
    results = judge(made)
    report(results, name, args.out)
    errors = sum(1 for r in results if "error" in r)
    print(f"judge: {name}; {len(results)} answers ({errors} not answered) -> {args.out / 'first_look.md'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
