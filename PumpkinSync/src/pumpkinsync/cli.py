from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local PumpkinSync editor")
    parser.add_argument("--host", default=os.environ.get("PUMPKINSYNC_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PUMPKINSYNC_PORT", "8765")))
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--template", type=Path, default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    print("PumpkinSync is starting (the first launch draws the built-in pumpkin face; this takes a few seconds)…", flush=True)
    application = create_app(data_root=args.data, template_path=args.template)
    print(f"PumpkinSync is ready at http://{'127.0.0.1' if args.host in {'0.0.0.0', '::'} else args.host}:{args.port}", flush=True)
    if not args.no_browser and args.host in {"127.0.0.1", "localhost"}:
        threading.Timer(1.1, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run(application, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
