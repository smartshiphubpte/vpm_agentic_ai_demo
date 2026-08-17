# PreVoyageRouteOptimizeAgent

## Role

At voyage start (after master route + initial weather) and again on noon weather
refresh, propose 4 alternate routes optimized for different objectives, scored
against weather limits **and** storm center/edge buffers.

## Hard rules

1. **No landmass** — the only hard constraint: no waypoint and no interpolated
   leg may sit on land. `VPM_LAND_CLEARANCE_NM` is a preferred standoff for graph
   nodes, **not** a reject. Conventional A* (default) builds a sea-only graph.
2. **Fixed endpoints** — origin and destination lat/lon are immutable.
3. Storm buffers and weather limits are **soft** (scored / optional keep-out on
   safest). They may be loosened. Land and endpoints are never loosened.

## Objective

Same sea graph, four costs at **fixed CP speed**:
- **shortest** / **fastest** — distance (fastest ≡ shortest at fixed speed)
- **least fuel** — MT from CP consumption × days; if consumption is missing,
  ranks as distance and fuel is **omitted** from the pre-departure report
- **safest** — distance + storm/weather cost

Report for every published route: distance, days, weather along the track, and
fuel only when consumption was provided.

Prefer routes that keep waypoints outside storm buffers when a sea-clear detour exists.

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

Live mode defaults to **conventional A\*** (`VPM_ROUTE_OPT_METHOD=conventional`,
`VPM_ROUTE_OPT_ALGO=astar`). LLM is optional and must still pass the land check.

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
