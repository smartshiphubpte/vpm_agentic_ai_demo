#!/usr/bin/env python3
"""Serve the route-compare map GUI (upload master + waypoint JSON)."""

from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI_DIR = ROOT / "gui"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(GUI_DIR), **kwargs)


def main() -> None:
    p = argparse.ArgumentParser(description="Route compare map viewer")
    p.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("VPM_ROUTE_VIEWER_PORT", "8765")),
        help="HTTP port (default 8765, or VPM_ROUTE_VIEWER_PORT)",
    )
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    if not (GUI_DIR / "route_compare.html").is_file():
        raise SystemExit(f"missing {GUI_DIR / 'route_compare.html'}")

    url = f"http://127.0.0.1:{args.port}/route_compare.html"
    with socketserver.TCPServer(("", args.port), _Handler) as httpd:
        print(f"Route viewer at {url}", flush=True)
        print("Upload master_route.json and six_hour_plan.json (or noon plan). Ctrl+C to stop.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()
