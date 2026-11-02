"""python -m hiraku.webserve [--port 8765] [--host 127.0.0.1] [--runs runs]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hiraku.webserve.server import DEFAULT_HOST, DEFAULT_PORT, WEB_DIR, make_server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m hiraku.webserve", description="Serve the HIRAKU screen and run records.")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"address to bind (default {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    p.add_argument("--runs", default="runs", help="folder that holds runs/<run_id>/ (default ./runs)")
    p.add_argument("--web", default=str(WEB_DIR), help="folder of the screen files (default: web/ of this repository)")
    p.add_argument("--quiet", action="store_true", help="do not log each request")
    args = p.parse_args(argv)

    server = make_server(args.host, args.port, Path(args.runs), Path(args.web), quiet=args.quiet)
    host, port = server.server_address[:2]
    print(f"HIRAKU screen: http://{host}:{port}/  (runs: {Path(args.runs).resolve()})", flush=True)
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
