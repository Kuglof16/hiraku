"""R4 (R4-rollover on the soil, R4-rollover-pavement) on the fixed v5 design reach222, as R4's builder makes the load
cases, with K2's rollover_beam: one row per ground, and each record saved, to compare one computer with another.

    python -m studies.k2_r4_ec2.run_r4 [out_dir]

out_dir (default runs/k2_r4_ec2, not committed) gets r4_<load case>.json (the whole record) and summary.json."""
import json
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = Path(__file__).with_name("reach222_design.json")
QUANTITIES = ("rops_occupant_clearance", "head_room", "trunk_room", "touch_speed", "rollbar_top_displacement",
              "ground_force_peak", "broken_members", "frame_split_fraction")


def main(out: Path) -> int:
    from hiraku.catalog import load_catalog, load_requirements
    from hiraku.catalog.builders import BUILDERS
    from hiraku.solvers.k2_vehicle.shelf import TOOLS

    out.mkdir(parents=True, exist_ok=True)
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    req = load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml")
    rows = []
    for lc in BUILDERS["R4"](load_catalog().test("R4"), req, design):
        t0 = time.perf_counter()
        rec = TOOLS["rollover_beam"].run(design, lc, [{"prediction_id": "pred-0001"}], {})
        wall = time.perf_counter() - t0
        (out / f"r4_{lc['load_case_id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=float),
                                                          encoding="utf-8")
        res = {r["quantity"]: r for r in rec.get("results", [])}
        row = {"load_case": lc["load_case_id"], "status": rec["status"], "wall_s": round(wall, 1),
               "contact_stiffness_N_per_mm": lc["params"]["contact_stiffness"]["value"],
               **{k: {"value": res[k]["value"], "where": res[k].get("where")} for k in QUANTITIES if k in res}}
        if "rops_occupant_clearance" in res:
            row["R4"] = "OK" if res["rops_occupant_clearance"]["value"] >= 0.0 else "NG"
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    summary = {"platform": platform.platform(), "python": sys.version, "design": DESIGN.name, "rows": rows}
    try:
        import numpy
        import numba
        summary.update(numpy=numpy.__version__, numba=numba.__version__)
    except ImportError:
        pass
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if all(r["status"] == "ok" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs" / "k2_r4_ec2"))
