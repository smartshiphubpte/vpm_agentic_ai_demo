#!/usr/bin/env python3
"""Report sender microservice — folder drop + multi-DB vpm_report poll."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from report_sender.config import settings  # noqa: E402
from report_sender.service import run_forever, run_once  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="VoyagePM report sender microservice")
    p.add_argument("--once", action="store_true", help="Single poll cycle then exit")
    args = p.parse_args()
    if args.once:
        folder_n, db_n = run_once()
        print(f"folder_sent={folder_n} db_sent={db_n}")
        return
    if not settings.folder_enabled and not (settings.db_enabled and settings.db_urls):
        print(
            "Enable VPM_REPORT_SENDER_FOLDER and/or VPM_REPORT_SENDER_DB_URLS in .env",
            file=sys.stderr,
        )
        sys.exit(1)
    run_forever()


if __name__ == "__main__":
    main()
