"""Self-check for report_sender (no SMTP/DB required)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from report_sender.config import settings
from report_sender.download import _gs_to_https
from report_sender.mailer import _parse_emails


def main() -> None:
    assert _parse_emails("a@x.com, b@y.com;c@z.com") == ["a@x.com", "b@y.com", "c@z.com"]
    assert _gs_to_https("gs://my-bucket/path/report.pdf") == (
        "https://storage.googleapis.com/my-bucket/path/report.pdf"
    )
    assert settings.report_table == "vpm_report"
    print("report_sender self-check ok")


if __name__ == "__main__":
    main()
