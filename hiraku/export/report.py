"""The run's report as Markdown and HTML, from the records alone: the report record (report.json,
the last report.written), the causes, the verdicts and the requirements with their sources. Nothing
is left out that the record has: assumptions, sources, assumed values, the causes of mismatches and
the values not checked. Both files are built from the same sections."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from hiraku import contract
from hiraku.contract import layout
from hiraku.store import RunStore

Record = dict[str, Any]
Section = tuple[str, list[str | list[list[str]]]]  # heading, paragraphs (str) or tables (rows of cells)

STATE_JA = {"green": "合格", "red": "不合格", "pending": "未判定"}


def _amount(q: Record | None) -> str:
    if not q:
        return ""
    return f"{q['value']:.4g}" if q.get("unit") in (None, "1") else f"{q['value']:.4g} {q['unit']}"


def report_record(store: RunStore) -> Record | None:
    """The run's report: report.json (the file always equals the last report.written)."""
    path = store.path / layout.REPORT_FILE
    if path.is_file():
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _causes(store: RunStore) -> list[str | list[list[str]]]:
    """Every cause record (load cases without a verdict too), with the comparison it explains."""
    rows = [["原因", "版", "照合", "荷重条件", "量", "差", "原因の文", "直し方", "安全側"]]
    for v in sorted(store.project.get("design_versions") or []):
        cmps = {c["comparison_id"]: c for c in store.records("comparison", v)}
        for c in store.records("cause", v):
            cmp = cmps.get(c["comparison_id"], {})
            pred = store.get("prediction", cmp.get("prediction_id", "")) or {}
            safe = (f"はい: {c.get('safe_side_reason', '')}" if c.get("safe_side") else "")
            rows.append([c["cause_id"], str(v), c["comparison_id"], pred.get("load_case_id", ""), cmp.get("quantity", ""),
                         f"{cmp['diff_rel'] * 100:+.1f} %" if "diff_rel" in cmp else "", " ".join(c["cause"].split()),
                         " ".join(c["fix"].split()), safe])
    return [rows] if len(rows) > 1 else ["食い違いはありません"]


def sections(store: RunStore) -> list[Section]:
    p = store.project
    rep = report_record(store)
    out: list[Section] = [("実行", [
        f"run_id: {p['run_id']}", f"作りたいもの: {p['want']}", f"状態: {p.get('status')}"
        + (f" ({p['stop_reason']})" if p.get("stop_reason") else ""),
        f"設計の版: {', '.join(str(v) for v in p.get('design_versions') or [])}",
    ])]
    if rep is None:
        out.append(("報告", ["この実行には報告の記録 (report.json) がありません"]))
    else:
        out.append(("まとめ", [rep["summary"], f"報告の版: {rep['design_version']}"]))
        causes = {c["cause_id"]: c for c in store.records("cause")}
        for d in rep["domains"]:
            body: list[str | list[list[str]]] = [f"{STATE_JA.get(d['state'], d['state'])}: {d['line']}"]
            if d.get("verdict_ids"):
                body.append("判定の記録: " + "、".join(d["verdict_ids"]))
            if d.get("effects"):
                body.append([["効いている項", "値", "割合"]] + [[t["name"], _amount(t) if "value" in t else "",
                                                          f"{t['share']:.2g}" if "share" in t else ""]
                                                         for t in d["effects"]])
            if d.get("assumptions"):
                body.append([["前提"]] + [[a] for a in d["assumptions"]])
            if d.get("differences"):
                rows = [["照合", "差", "原因"]]
                for x in d["differences"]:
                    c = causes.get(x.get("cause_id") or "")
                    rows.append([x["comparison_id"], f"{x['diff_rel'] * 100:+.1f} %",
                                 (c["cause"] + (f" (直し方: {c['fix']})" if c.get("fix") else "")) if c else "原因の記録なし"])
                body.append(rows)
            out.append((f"{contract.DOMAIN_NAMES_JA.get(d['domain'], d['domain'])} ({d['domain']})", body))
        if rep.get("next"):
            out.append(("次に変えるなら", [[["分野", "変更", "理由"]] + [[n["domain"], n["change"], n["reason"]]
                                                                for n in rep["next"]]]))
        out.append(("確かめていない値", [[["項目", "出どころ"]] + [[u["item"], u["source"]] for u in rep["unchecked"]]]
                    if rep["unchecked"] else ["なし"]))
    out.append(("食い違いと原因", _causes(store)))
    out.append(("要件と出典", [[["番号", "項目", "値", "出どころ", "信頼度", "出典・根拠"]] + [
        [r["key"], r.get("name", ""), f"{r.get('value')}" + (f" {r['unit']}" if r.get("unit") else ""),
         r.get("source", ""), str(r.get("trust", "")), " ".join(str(r.get("basis", "")).split())]
        for r in p.get("requirements", [])]]))
    return out


def _md_cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def to_markdown(secs: list[Section], title: str) -> str:
    lines = [f"# {title}", ""]
    for head, body in secs:
        lines += [f"## {head}", ""]
        for part in body:
            if isinstance(part, str):
                lines += [part, ""]
            else:
                lines.append("| " + " | ".join(_md_cell(c) for c in part[0]) + " |")
                lines.append("|" + "---|" * len(part[0]))
                lines += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in part[1:]]
                lines.append("")
    return "\n".join(lines)


def to_html(secs: list[Section], title: str) -> str:
    e = html.escape
    parts = ['<!doctype html><html lang="ja"><head><meta charset="utf-8">', f"<title>{e(title)}</title>",
             "<style>body{font-family:sans-serif;max-width:60em;margin:2em auto;line-height:1.5}"
             "table{border-collapse:collapse;margin:.5em 0}td,th{border:1px solid #999;padding:.2em .5em;"
             "text-align:left;vertical-align:top}</style></head><body>", f"<h1>{e(title)}</h1>"]
    for head, body in secs:
        parts.append(f"<h2>{e(head)}</h2>")
        for part in body:
            if isinstance(part, str):
                parts.append(f"<p>{e(part)}</p>")
            else:
                parts.append("<table><tr>" + "".join(f"<th>{e(str(c))}</th>" for c in part[0]) + "</tr>"
                             + "".join("<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in row) + "</tr>"
                                       for row in part[1:]) + "</table>")
    parts.append("</body></html>")
    return "\n".join(parts)


def write_report(store: RunStore, folder: Path) -> list[Path]:
    """report.md and report.html in folder."""
    secs = sections(store)
    title = f"HIRAKU の報告 {store.project['run_id']}"
    folder.mkdir(parents=True, exist_ok=True)
    md, page = folder / "report.md", folder / "report.html"
    md.write_text(to_markdown(secs, title), encoding="utf-8")
    page.write_text(to_html(secs, title), encoding="utf-8")
    return [md, page]
