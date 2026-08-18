# PortWeatherAgent

## Role

While a vessel is **in port** (after an Arrival report, until a later Departure for the same vessel), emit a templated port weather forecast on a timer.

## Objective

Drop `port_weather_*.pdf` under `VPM_PORT_WEATHER_OUT_DIR/{voyage}/incoming/` so report-sender can pick them up. Refresh every `VPM_PORT_WEATHER_INTERVAL_HOURS` (default 24). Stop when a newer Departure report lands for that vessel.

## Preconditions

- Voyage registry has an Arrival noon (from the noon service).
- Same vessel has no later Departure across any registered voyage.

## Tasks

1. Scan registry; group noons by `vessel_id` (fallback vessel name).
2. In port = latest Arrival is newer than latest Departure.
3. On first sighting (and each due interval) fetch hourly forecast at the arrival lat/lon for `VPM_PORT_WEATHER_HORIZON_HOURS`.
4. Fill `port_weather_report.txt`; write PDF into `incoming/`.
5. On a later Departure for that vessel, stop (no more PDFs).

## Tools

| Tool | Purpose |
|------|---------|
| `weather_along_route` | Hourly forecast at the port position |

## Defaults

```json
{
  "phase": "port_weather_reported"
}
```

Weather limits come from **WeatherReportAgent.md** (same bars as passage weather).

## Writes

- `VPM_PORT_WEATHER_OUT_DIR/{voyage_number}/incoming/port_weather_*.pdf`
- `VPM_PORT_WEATHER_OUT_DIR/{voyage_number}/port_weather_*.txt`
- `VPM_PORT_WEATHER_STATE_PATH` (next due + active vessels)

## Failure

- Per-vessel errors are logged; other in-port vessels still run.
- Missing lat/lon on the Arrival row → skip that vessel.
