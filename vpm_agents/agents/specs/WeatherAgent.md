# WeatherAgent

## Role

Route weather (Open-Meteo when `VPM_WEATHER_SOURCE=live`, else voyagepm_be), hard-region overlay, point queries.

## Objective

Annotate the working route with weather points and hard-region flags so storm / optimize /
alert agents can adapt.

## Preconditions

- Prefer `state.suggested_route`, else `state.master_route`. Skip if neither exists.

## Tasks

1. Choose waypoints (suggested → master).
2. Query weather along the route.
3. Write summary + hard regions + raw points into state.
4. Set phase to Defaults.phase.

## Tools

| Tool | Purpose |
|------|---------|
| `weather_route` | Weather along waypoints |
| `weather_point` | Single lat/lon sample |

## Defaults

```json
{
  "phase": "weathered",
  "prefer_route": "suggested"
}
```

Hard-region flags use `weather_limits` from **WeatherReportAgent.md** (one bar for report + GUI).

`prefer_route`: `suggested` then fall back to master (current code path).

## Writes

- `state.weather_summary`, `state.hard_regions`
- `state.artifacts.weather_points`
- `state.phase`

## Failure

- No route points → note and skip (do not fail the workflow).
