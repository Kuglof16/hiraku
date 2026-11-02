"""python -m hiraku.webserve [--port 8765] [--host 127.0.0.1] [--runs runs] [--saved showcase] [--default-run ID]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hiraku.webserve.server import DEFAULT_HOST, DEFAULT_PORT, SAVED_DIR, WEB_DIR, list_runs, make_server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m hiraku.webserve", description="Serve the HIRAKU screen and run records.")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"address to bind (default {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    p.add_argument("--runs", default="runs", help="folder that holds runs/<run_id>/ (default ./runs)")
    p.add_argument("--saved", default=str(SAVED_DIR), help="folder of saved runs kept in the repository (default: showcase/ of this repository)")
    p.add_argument("--default-run", default=None, help="run the screen opens when the URL names none (default: the newest saved run)")
    p.add_argument("--web", default=str(WEB_DIR), help="folder of the screen files (default: web/ of this repository)")
    p.add_argument("--quiet", action="store_true", help="do not log each request")
    args = p.parse_args(argv)

    server = make_server(args.host, args.port, Path(args.runs), Path(args.web), quiet=args.quiet,
                         saved_dir=Path(args.saved), default_run=args.default_run)
    host, port = server.server_address[:2]
    print(f"HIRAKU screen: http://{host}:{port}/  (runs: {Path(args.runs).resolve()})", flush=True)
    default = next((r["run_id"] for r in list_runs(Path(args.runs), Path(args.saved), args.default_run) if r["default"]), None)
    if default:
        print(f"  opens on: {default}  (run list: http://{host}:{port}/?list=1)", flush=True)
    elif args.default_run:
        print(f"  default run {args.default_run} not found; the screen opens on the run list", flush=True)
    print(f"  sample replay: http://{host}:{port}/?run=sample", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
