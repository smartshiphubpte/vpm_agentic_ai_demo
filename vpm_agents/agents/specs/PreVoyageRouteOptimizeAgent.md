# PreVoyageRouteOptimizeAgent

## Role

At voyage start (after master route + initial weather) and again on noon weather
refresh, propose 4 alternate routes optimized for different objectives, scored
against weather limits **and** storm center/edge buffers.

## Hard rules

1. **No landmass** — a ship route must stay on water and at least
   `VPM_LAND_CLEARANCE_NM` clear of land. Any alternate whose waypoints / 6h plan
   samples land (or under-clearance water) is **rejected** and never suggested.
   Conventional optimize builds coast-edge nodes from land rings so paths go
   *around* continents instead of cutting chords.
2. **Fixed endpoints** — origin and destination lat/lon are immutable; only
   intermediate waypoints may move. Optimizer output is pinned to the input
   start/end before scoring.
3. Soft rules (weather limits, storm buffers) may be **loosened** only when no
   land-safe alternate remains under the nominal caps. Land and endpoint rules
   are never loosened.

## Objective

Use weather along the plan plus active storm progressions to suggest **fastest**,
**shortest**, **least fuel**, and **safest** routes among **sea-clear** candidates.
Prefer routes that keep every waypoint outside `VPM_STORM_CENTER_BUFFER_NM` of
any storm center and outside `VPM_STORM_EDGE_BUFFER_NM` of the storm edge (radius).

Runs at:
- **pre-voyage** when the daemon flow includes `route_optimize`
- **noon** after noon weather track is written (current position + remaining WPs)

## Preconditions

- Voyage in registry with waypoints (master or remaining) and weather preferred.
- Storm map-layer available (mock or live); empty storms → weather-only scoring.

## Tasks

1. Load voyage + fetch active storms (map-layer progressions).
2. For each objective, call VO optimize with storm payload.
3. Reject any result that crosses land (hard). Build 6h plan; score weather + storms.
4. If no alternate passes soft weather/storm caps, loosen weather limits stepwise;
   never re-admit a land-crossing route.
5. Write index JSON + **one JSON per objective** under `reports/{voyage}/subreports/`.
6. Store `optimized_routes` + `suggested_route` on registry (prefer storm-clear sea routes).

## Tools

| Tool | Purpose |
|------|---------|
| `optimize` | Per-objective optimizer — local conventional (A*/Dijkstra), LLM (`RouteOptimizeLLMAgent.md`), or voyagepm_be (`VPM_ROUTE_OPT_METHOD`) |
| `weather_route` | Weather along 6h plan |
| `score` | Weather + storm violations |

Live mode defaults to **local** optimize (`VPM_ROUTE_OPT_METHOD=conventional|llm`); set `backend` only if voyagepm_be VO is available.

## Defaults

```json
{
  "phase": "routes_optimized",
  "horizon_hours": 168,
  "waypoint_interval_hours": 6,
  "preferred": "safest",
  "reject_if_limits_exceeded": false,
  "reject_if_storm_encounter": false,
  "hard_rules": ["no_landmass", "fixed_endpoints"],
  "weather_limits": {
    "max_wind_kn": 35,
    "max_wave_m": 4.0,
    "max_swell_m": 3.0
  },
  "objectives": [
    {"id": "fastest", "optimize_for": "fastest", "label": "Fastest ETA"},
    {"id": "shortest", "optimize_for": "shortest", "label": "Shortest distance"},
    {"id": "fuel", "optimize_for": "fuel", "label": "Least fuel"},
    {"id": "safest", "optimize_for": "safest", "label": "Safest (weather+storm)"}
  ]
}
```

## Writes

- `reports/{voyage}/subreports/route_alternatives_{trigger}_{stamp}.json` (index)
- `reports/{voyage}/subreports/route_alt_{id}_{trigger}_{stamp}.json` (per objective)
- `reports/{voyage}/subreports/route_alternatives_{trigger}_{stamp}.txt`
- registry `optimized_routes`, `suggested_route`, `route_optimize_at`

## Failure

- Missing voyage / waypoints → note and skip (do not fail daemon).
