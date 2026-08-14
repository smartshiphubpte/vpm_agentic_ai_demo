# StormGeofenceAgent

## Role

JTWC storm registry, geofence check, storm-triggered re-optimize.

## Objective

Detect storm / geofence risk against the open voyage and, when required, publish a
storm-safest suggested route.

## Preconditions

- Auth token preferred.
- Geofence check requires `state.voyage_id`; storms list still runs without it.

## Tasks

1. Run storm watcher; load storm registry onto state.
2. If no voyage: note storm count and return.
3. Resolve vessel position (kwarg → first master waypoint → Defaults.position).
4. Run geofence check; store under artifacts.
5. If `reoptimize` is true: geofence-optimize suggested/master route, update suggested
   and `optimized_routes[Defaults.storm_route_key]`.
6. Set phase to Defaults.phase.

## Tools

| Tool | Purpose |
|------|---------|
| `storms` | List active storms |
| `watcher` | Refresh JTWC / storm pipeline |
| `geofence` | Check voyage vs fences |
| `reoptimize` | Storm-safe re-route |

## Defaults

```json
{
  "phase": "storm_checked",
  "storm_route_key": "storm-safest",
  "position": {"lat": 10.0, "lon": 110.0}
}
```

## Writes

- `state.storms`, `state.suggested_route` (when re-opt)
- `state.optimized_routes[storm_route_key]`
- `state.artifacts.geofence`
- `state.phase`

## Failure

- No voyage → storms still recorded; skip geofence / re-opt.
