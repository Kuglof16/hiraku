"""The CAD tools (export_step, import_step, edit_step): a thin adapter between the MCP tools and hiraku.cad.

The only module outside hiraku/cad that imports it. hiraku/tools/mcp.py imports this module in one line,
and on import it adds its tools to mcp's tables (input schema from the contract's tool_input.<name>,
description, function). Removing that line, this file and hiraku/cad leaves the rest working.

Every path is relative to the run's folder and must stay inside it (checked after resolving, so
`..` and links cannot leave it) and end in .step or .stp, so no other record can be read or written.
import_step returns a design record but makes no version (write_design does that).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from hiraku import contract
from hiraku.cad import CadError
from hiraku.cad.import_design import design_from_step
from hiraku.cad.step_edit import OPS, edit_step_report
from hiraku.cad.step_read import read_step
from hiraku.cad.step_write import design_to_step
from hiraku.contract.protocols import ToolError
from hiraku.store import StoreError

Record = dict[str, Any]

STEP_SUFFIXES = (".step", ".stp")
MAX_READ_BYTES = 64 * 1024 * 1024  # a STEP file larger than this is refused (read in memory as text)
CAD_DIR = "cad"  # the default folder in the run for written files


def _run_dir(ctx: Any) -> Path:
    store = getattr(ctx, "store", None)
    path = getattr(store, "path", None)
    if path is None:
        raise ToolError("実行のフォルダがありません。実行を始めてから呼んでください")
    return Path(path).resolve()


def run_path(ctx: Any, rel: Any, *, must_exist: bool) -> Path:
    """The file `rel` names inside the run's folder; refused when it leaves the folder or is not STEP."""
    if not isinstance(rel, str) or not rel.strip():
        raise ToolError("path に実行のフォルダの中の STEP ファイルを、フォルダからの相対の道筋で渡してください (cad/frame.step など)")
    run = _run_dir(ctx)
    p = (run / rel).resolve()
    if not p.is_relative_to(run) or p == run:
        raise ToolError(f"path は実行のフォルダの中だけです ({rel!r} は外を指します)")
    if p.suffix.lower() not in STEP_SUFFIXES:
        raise ToolError(f"path は .step か .stp のファイルだけです ({rel!r})")
    if must_exist:
        if not p.is_file():
            raise ToolError(f"{rel!r} は実行のフォルダにありません")
        if p.stat().st_size > MAX_READ_BYTES:
            raise ToolError(f"{rel!r} は {p.stat().st_size} バイトで、読める上限 {MAX_READ_BYTES} バイトを超えます")
    return p


def _rel(ctx: Any, p: Path) -> str:
    return p.relative_to(_run_dir(ctx)).as_posix()


def _read(p: Path) -> str:
    raw = p.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")  # Part 21 is ISO 8859-1 outside its \X\ escapes


def _write(p: Path, text: str) -> int:
    p.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    p.write_bytes(data)
    return len(data)


def _cad(fn: Callable[[Any, Record], Record]) -> Callable[..., Record]:
    def wrapped(ctx: Any, args: Record | None = None, agent: str | None = None) -> Record:
        try:
            return fn(ctx, dict(args or {}))
        except CadError as e:
            raise ToolError(f"STEP を扱えませんでした: {e}") from None

    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


@_cad
def export_step(ctx: Any, args: Record) -> Record:
    """Write a design version as STEP into the run's folder."""
    store = ctx.store
    v = args.get("design_version") or store.design_version
    if not v:
        raise ToolError("設計の版がまだありません。版ができてから呼んでください")
    try:
        design = store.design(v)
    except StoreError:
        raise ToolError(f"設計のv{v} はありません (今はv{store.design_version} まで)") from None
    rel = args.get("path")
    if rel is None:
        p = run_path(ctx, f"{CAD_DIR}/design_v{v}.step", must_exist=False)
    else:
        p = run_path(ctx, rel, must_exist=False)
        if p.exists():
            raise ToolError(f"{rel!r} はもうあります。別の path にしてください (書き換えません)")
    text = design_to_step(design)
    size = _write(p, text)
    return {"path": _rel(ctx, p), "design_version": v, "bytes": size,
            "solids": len(read_step(text)),
            "members": len(design.get("members") or []), "parts": len(design.get("parts") or [])}


@_cad
def import_step(ctx: Any, args: Record) -> Record:
    """Read a STEP file of the run's folder as a design record; no version is made."""
    p = run_path(ctx, args.get("path"), must_exist=True)
    rho = args.get("density_kg_m3")
    density = None if rho is None else (dict(rho) if isinstance(rho, dict) else {"*": float(rho)})
    solids = read_step(_read(p))
    if not solids:
        raise ToolError(f"{_rel(ctx, p)} に立体 (MANIFOLD_SOLID_BREP など) がありません")
    got = design_from_step(solids, name=p.stem, density_by_name=density)
    design = got["design"]
    part_ids: dict[str, list[str]] = {}
    for part in design["parts"]:
        part_ids.setdefault(part["name"], []).append(part["id"])
    became = []
    for r in got["report"]:
        row = dict(r)
        if r["became"] == "part":
            row["part"] = part_ids[r["solid"]].pop(0)
        became.append(row)
    return {"path": _rel(ctx, p), "design": design, "errors": got["errors"], "needed": got["needed"],
            "solids": became, "free_ends": got["free_ends"], "part_basis": got["part_basis"],
            "counts": {"solids": len(solids), "members": len(design["members"]), "parts": len(design["parts"]),
                       "nodes": len(design["nodes"]), "sections": len(design["sections"])}}


@_cad
def edit_step(ctx: Any, args: Record) -> Record:
    """Apply edits to a STEP file of the run's folder and write the result to another file."""
    src = run_path(ctx, args.get("path"), must_exist=True)
    edits = args.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ToolError(f"edits に変更を 1 つ以上並べてください (op は {', '.join(OPS)})")
    if args.get("out_path") is not None:
        out = run_path(ctx, args["out_path"], must_exist=False)
        if out.exists():
            raise ToolError(f"{args['out_path']!r} はもうあります。別の out_path にしてください (書き換えません)")
    else:
        n = 1
        while (out := src.with_name(f"{src.stem}_edit{n}{src.suffix}")).exists():
            n += 1
    text, log = edit_step_report(_read(src), edits)
    size = _write(out, text)
    return {"path": _rel(ctx, src), "out_path": _rel(ctx, out), "bytes": size, "log": log,
            "solids": len(read_step(text))}


DESCRIPTIONS = {
    "export_step": (
        "設計の版を STEP (ISO 10303-21, AP214) のファイルにして実行のフォルダに書く。部材 (断面の管) と部品 (箱) が "
        "1 つずつ ID の名前つきの立体になる。path を省くと cad/design_v<版>.step。返るのは書いた path・立体の数・バイト数"
    ),
    "import_step": (
        "実行のフォルダの中の STEP を読み、設計の記録 (design: 部材・節点・断面・部品) と、立体ごとに何になったか "
        "(solids: member か part と理由) を返す。数 (寸法・体積・質量) はコードが形から測る。版は作らない "
        "(版にするなら返った design を直して版を作るツールに渡す)。needed は STEP に無いもの (支持・材料・荷重)。"
        "density_kg_m3 を渡すと部品の質量 = 体積 x 密度"
    ),
    "edit_step": (
        "実行のフォルダの中の STEP の立体を名前で選んで変え、別のファイルに書く (元は書き換えない)。edits は順に: "
        "move (by_mm)、rotate (axis, angle_deg, about_mm?)、delete、add_box (center_mm, size_mm, rpy_deg?)、"
        "set_section (section: {shape, dims_mm}、まっすぐな管か棒だけ)、set_wall (t_mm)、"
        "add_brace (from_mm, to_mm, section)。返るのは書いた out_path と変更ごとの記録 log"
    ),
}

FUNCTIONS = {"export_step": export_step, "import_step": import_step, "edit_step": edit_step}


def _input_schema(name: str) -> Record:
    schema = dict(contract.schema()["$defs"][f"tool_input.{name}"])
    schema.pop("description", None)
    return schema


TOOLS: dict[str, Record] = {
    name: {"description": DESCRIPTIONS[name], "input_schema": _input_schema(name), "function": FUNCTIONS[name]}
    for name in FUNCTIONS
}
assert tuple(TOOLS) == contract.MCP_TOOLS_CAD


def _register() -> None:
    """Add the tools to hiraku.tools.mcp's tables (imported here; when mcp is the importer, its tables are ready)."""
    from hiraku.tools import mcp

    for name, t in TOOLS.items():
        mcp.INPUT_SCHEMAS[name] = t["input_schema"]
        mcp.DESCRIPTIONS[name] = t["description"]
        mcp.HANDLERS[name] = t["function"]


_register()
