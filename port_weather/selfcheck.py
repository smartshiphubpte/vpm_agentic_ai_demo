"""Self-check: in-port detect + report write (no network)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from port_weather.report import track_from_wx, write_port_weather_report
from port_weather.service import in_port_vessels, tick_once
from vpm_agents.tools.mock_backend import MockBackend
from vpm_agents.tools.voyage_registry import VoyageRegistry


def _wx() -> dict:
    return {
        "provider": "test",
        "points": [
            {
                "lat": 22.3,
                "lon": 114.2,
                "windKn": 12,
                "windDirDeg": 90,
                "pressureHpa": 1012,
                "tempC": 28.0,
                "waveM": 0.4,
                "waveDirDeg": 90,
                "swellM": 0.2,
                "swellDirDeg": 80,
                "currentKn": 0.1,
                "currentDirDeg": 90,
                "validTime": "2026-08-18T00:00:00+00:00",
            },
            {
                "lat": 22.3,
                "lon": 114.2,
                "windKn": 40,
                "windDirDeg": 120,
                "pressureHpa": 1004,
                "tempC": 27.0,
                "waveM": 2.5,
                "waveDirDeg": 110,
                "swellM": 1.8,
                "swellDirDeg": 100,
                "currentKn": 0.2,
                "currentDirDeg": 10,
                "validTime": "2026-08-18T12:00:00+00:00",
            },
        ],
        "hardRegions": [],
    }


def main() -> None:
    arrival = {
        "voyage_number": "VTESTA",
        "vessel_id": "99",
        "vessel_name": "Test Ship",
        "dest_port": "Hong Kong",
        "last_noon": {
            "report_type": "Arrival Report",
            "lat": 22.3,
            "lon": 114.2,
            "observed_at": "2026-08-01T00:00:00Z",
            "noon_id": "arr1",
            "vessel_name": "Test Ship",
        },
    }
    found = in_port_vessels({"VTESTA": arrival})
    assert "id:99" in found, found
    assert found["id:99"]["port_name"] == "Hong Kong"

    departed = {
        "voyage_number": "VTESTB",
        "vessel_id": "99",
        "last_noon": {
            "report_type": "Departure Report",
            "lat": 22.3,
            "lon": 114.2,
            "observed_at": "2026-08-03T00:00:00Z",
            "noon_id": "dep1",
        },
    }
    assert in_port_vessels({"VTESTA": arrival, "VTESTB": departed}) == {}

    still = dict(departed)
    still["last_noon"] = {
        **departed["last_noon"],
        "observed_at": "2026-07-01T00:00:00Z",
    }
    assert "id:99" in in_port_vessels({"VTESTA": arrival, "VTESTB": still})

    tmp = Path(tempfile.mkdtemp(prefix="vpm_portwx_"))
    pdf, txt = write_port_weather_report(
        voyage_number="VTESTA",
        vessel_id="99",
        vessel_name="Test Ship",
        port_name="Hong Kong",
        lat=22.3,
        lon=114.2,
        arrived_at="2026-08-01T00:00:00Z",
        wx=_wx(),
        out_dir=tmp,
        stamp="SELFTEST",
    )
    assert pdf.is_file() and pdf.stat().st_size > 500
    assert pdf.parent.name == "incoming"
    assert txt.is_file() and "Hong Kong" in txt.read_text(encoding="utf-8")
    track = track_from_wx(22.3, 114.2, _wx())
    assert len(track["track"]) == 2

    reg_path = tmp / "voyage_registry.json"
    reg = VoyageRegistry(reg_path)
    reg.upsert("VTESTA", arrival)
    n = tick_once(registry=reg, backend=MockBackend(), state_path=tmp / "state.json")
    assert n == 1
    n2 = tick_once(registry=reg, backend=MockBackend(), state_path=tmp / "state.json")
    assert n2 == 0  # next_due is interval hours away
    print("port_weather self-check ok")


if __name__ == "__main__":
    main()
