# WeatherReportAgent

## Role

After a **Departure Report** (noon), wait `VPM_WEATHER_REPORT_DELAY_MINUTES` then fetch weather along the active plan and write a passage weather report. Pre-voyage ingest does **not** trigger passage weather.

## Objective

Generate a templated passage weather report (and JSON) under `VPM_WEATHER_OUT_DIR/{voyage_number}/` when `weather_due_at` elapses **and** the voyage has departed.

## Preconditions

- Voyage in registry with `passage_weather_active` (set on Departure Report ingest).
- `weather_due_at` ≤ now and a waypoint plan (`noon_seven_day_plan` preferred, else `six_hour_plan`).

## Tasks

1. Scan registry for due weather jobs.
2. Authenticate (mock/live backend).
3. Call weather along plan waypoints.
4. Fill `weather_report.txt` template; write `.txt` + `weather_points_*.json`.
5. Clear `weather_due_at`; set `last_weather_report_at`.

## Tools

| Tool | Purpose |
|------|---------|
| `weather_route` | Weather along waypoints |

## Defaults

```json
{
  "phase": "weather_reported",
  "weather_limits": {
    "max_wind_kn": 5,
    "max_wave_m": 4.0,
    "max_swell_m": 3.0
  }
}
```

A track point is bad weather when wind, significant wave, or swell meets or exceeds these limits (or the provider already flagged it `hard`). Edit this JSON — not env vars — to change the bars.

## Writes

- `reports/{voyage_number}/weather_report_*.txt`
- `reports/{voyage_number}/weather_points_*.json`
- Registry `last_weather_report*`

## Failure

- Per-voyage errors are noted; other due voyages still run.
