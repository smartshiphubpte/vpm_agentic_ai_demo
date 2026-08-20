"""Self-check for report_sender (no SMTP/DB required)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from report_sender.config import settings
from report_sender.download import _gs_to_https
from report_sender.draft import (
    END_OF_VOYAGE,
    PASSAGE_WEATHER,
    PORT_WEATHER,
    PRE_DEPARTURE,
    STORM_ALERT,
    base_context,
    classify_report,
    fill_template,
    render_email,
)
from report_sender.mailer import _parse_emails


def main() -> None:
    assert _parse_emails("a@x.com, b@y.com;c@z.com") == ["a@x.com", "b@y.com", "c@z.com"]
    assert _gs_to_https("gs://my-bucket/path/report.pdf") == (
        "https://storage.googleapis.com/my-bucket/path/report.pdf"
    )
    assert settings.report_table == "vpm_report"
    assert classify_report("weather_report_20260811T100046Z.pdf") == PASSAGE_WEATHER
    assert classify_report("cyclone_alert_x.pdf", "weather_report") == STORM_ALERT
    assert classify_report("pre_voyage_route_x.pdf") == PRE_DEPARTURE
    assert classify_report("port_weather_x.pdf") == PORT_WEATHER
    assert classify_report("end_of_voyage_report_x.pdf") == END_OF_VOYAGE
    ctx = base_context(
        voyage_number="V001",
        vessel_name="MV Test",
        extra={"source_port": "A", "dest_port": "B", "recipients": "ops@x.com", "timestamp": "now"},
    )
    sub, body = render_email(PRE_DEPARTURE, ctx)
    assert "V001" in sub and "MV Test" in body and "ops@x.com" in body
    assert fill_template("Hi {missing}", {}) == "Hi —"
    print("report_sender self-check ok")


if __name__ == "__main__":
    main()
