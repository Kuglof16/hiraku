"""H1 / H3 (assembly), H7 (entry_opening), C1 (crash, if it runs) on v005 + BASE + a set's variables.

  python -m studies.lere.checks NAME --out DIR   -> DIR/NAME/checks.json (and printed)
Load cases from the catalog against data/requirements/offroad-4w.yaml. Light (no shell solve)."""
import argparse
import json
import os


def main():
    ap = argparse.ArgumentParser(description="Assembly / entry / crash checks of one set.")
    ap.add_argument("name")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from .common import setup_env
    setup_env()
    from pathlib import Path
    from hiraku import solvers
    from hiraku.catalog import load_catalog, load_requirements
    from hiraku.catalog.build import build_load_cases
    from .common import REQUIREMENTS, regen
    from .sets import variables
    d = regen(variables(a.name))
    T = {t.name: t for t in solvers.builtin_tools()}
    req = load_requirements(Path(REQUIREMENTS))
    out = {}
    for tool, test in (("assembly", "H1"), ("assembly", "H3"), ("entry_opening", "H7"), ("crash", "C1")):
        if tool not in T:
            out[f"{tool} {test}"] = "tool not registered"
            print(tool, test, out[f"{tool} {test}"], flush=True)
            continue
        try:
            lcs = build_load_cases(load_catalog(), req, d, [test])
        except Exception as e:
            out[f"{tool} {test}"] = f"no load case: {type(e).__name__} {e}"
            print(tool, test, out[f"{tool} {test}"], flush=True)
            continue
        for lc in lcs:
            key = f"{tool} {test} {lc.get('load_case_id')}"
            try:
                rec = T[tool].run(d, lc, [{"prediction_id": "pred-0001"}], {})
                rows = {r["quantity"]: round(r["value"], 3) if isinstance(r["value"], (int, float)) else r["value"]
                        for r in rec.get("results", [])}
                out[key] = {"status": rec.get("status"), "error": rec.get("error"), "results": rows}
            except Exception as e:
                out[key] = f"{type(e).__name__}: {e}"
            print(key, out[key], flush=True)
    os.makedirs(os.path.join(a.out, a.name), exist_ok=True)
    with open(os.path.join(a.out, a.name, "checks.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)


if __name__ == "__main__":
    main()
