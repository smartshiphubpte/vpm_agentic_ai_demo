# WeatherReportAgent

## Role

After pre-voyage or noon ingest, wait `VPM_WEATHER_REPORT_DELAY_MINUTES` then fetch weather along the active plan and write a report.

## Objective

Generate a templated weather report (and JSON points file) under `VPM_REPORTS_OUT_DIR/{voyage_number}/` when `weather_due_at` elapses.

## Preconditions

- Voyage in registry with `weather_due_at` ≤ now and a waypoint plan (`six_hour_plan` or `noon_seven_day_plan`).

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
  "phase": "weather_reported"
}
```

## Writes

- `reports/{voyage_number}/weather_report_*.txt`
- `reports/{voyage_number}/weather_points_*.json`
- Registry `last_weather_report*`

## Failure

- Per-voyage errors are noted; other due voyages still run.
