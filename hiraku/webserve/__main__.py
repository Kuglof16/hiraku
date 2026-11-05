"""python -m hiraku.webserve [--port 8765] [--host 127.0.0.1] [--runs runs] [--saved showcase] [--default-run ID] [--open]"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from hiraku.webserve.launch import DEMO_RUNS_FILE, KEY_NAME
from hiraku.webserve.server import DEFAULT_HOST, DEFAULT_PORT, LAYOUT_FILE, PETS_FILE, REPO_DIR, SAVED_DIR, WEB_DIR, list_runs, make_server

RUNS_DIR = REPO_DIR / "runs"  # where runs write their records, wherever the server is started from


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m hiraku.webserve", description="Serve the HIRAKU screen and run records.")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"address to bind (default {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    p.add_argument("--runs", default=str(RUNS_DIR), help="folder that holds runs/<run_id>/ (default: runs/ of this repository)")
    p.add_argument("--saved", default=str(SAVED_DIR), help="folder of saved runs kept in the repository (default: showcase/ of this repository)")
    p.add_argument("--default-run", default=None, help="run the run list recommends at its top (default: the newest saved run)")
    p.add_argument("--web", default=str(WEB_DIR), help="folder of the screen files (default: web/ of this repository)")
    p.add_argument("--layout", default=str(LAYOUT_FILE), help="the video's layout rules served as /layout.json (default: promo/screens/layout.json)")
    p.add_argument("--pets", default=str(PETS_FILE), help="the 3D pets' script served as /pets/pet3d.js (default: promo/pets/pet3d.js; without it the cut pages draw no pets)")
    p.add_argument("--demo-runs", default=str(DEMO_RUNS_FILE), help="the demo runs the run list offers (default: data/catalog/demo_runs.yaml)")
    p.add_argument("--quiet", action="store_true", help="do not log each request")
    p.add_argument("--open", action="store_true", help="open the screen in the default browser once the server listens")
    args = p.parse_args(argv)

    try:
        server = make_server(args.host, args.port, Path(args.runs), Path(args.web), quiet=args.quiet,
                             saved_dir=Path(args.saved), default_run=args.default_run, layout_file=Path(args.layout),
                             pets_file=Path(args.pets),
                             demo_runs_file=Path(args.demo_runs))
    except OSError as e:
        print(f"HIRAKU screen: port {args.port} on {args.host} is in use ({e.strerror or e}). "
              f"Another HIRAKU screen may still be open: close its window, or start with --port <another number>.",
              file=sys.stderr, flush=True)
        return 1
    host, port = server.server_address[:2]
    print(f"HIRAKU screen: http://{host}:{port}/  (runs: {Path(args.runs).resolve()})", flush=True)
    default = next((r["run_id"] for r in list_runs(Path(args.runs), Path(args.saved), args.default_run) if r["default"]), None)
    if default:
        print(f"  the run list recommends: {default}  (replay: http://{host}:{port}/?run={default})", flush=True)
    elif args.default_run:
        print(f"  default run {args.default_run} not found; the run list recommends none", flush=True)
    print(f"  sample replay: http://{host}:{port}/?run=sample", flush=True)
    launcher = server.RequestHandlerClass.launcher
    if launcher:
        claude = "and with Claude" if launcher.claude_ready() else f"(with Claude only when {KEY_NAME} is set in this window)"
        print(f"  start a run from the screen: scripted {claude}: http://{host}:{port}/?list=1", flush=True)
    if args.open:
        # the socket already listens (make_server binds it), so the page finds the server
        webbrowser.open(f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
