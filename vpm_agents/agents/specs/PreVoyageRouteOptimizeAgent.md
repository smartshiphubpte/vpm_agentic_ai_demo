# PreVoyageRouteOptimizeAgent

## Role

At voyage start (after master route + initial weather) and again on noon weather
refresh, spawn **four objective sub-agents in parallel**. Each agent has its own
MD spec and proposes the best sea-only track for **one** job from the master
route. After weather-along, labels are assigned by measured metrics so the
published “shortest” is actually shortest, “fastest” has the lowest
weather-adjusted ETA, “least fuel” the lowest burn, and “safest” the calmest
storm/weather exposure.

## Hard rules

1. **No landmass** — the only hard constraint: no waypoint and no interpolated
   leg may sit on land. `VPM_LAND_CLEARANCE_NM` is a preferred standoff for graph
   nodes, **not** a reject. Conventional A* (default) builds a sea-only graph.
2. **Fixed endpoints** — origin and destination lat/lon are immutable.
3. Storm buffers and weather limits are **soft** (scored / optional keep-out on
   safest). They may be loosened. Land and endpoints are never loosened.

## Objective

Same master route, four specialist agents (fixed CP speed; weather adjusts ETA/fuel):
- **RouteOptShortestAgent** — minimize sailed NM (land detours only)
- **RouteOptFastestAgent** — minimize weather-adjusted ETA (calm can beat short+rough)
- **RouteOptFuelAgent** — minimize weather-adjusted fuel (heavy seas burn more)
- **RouteOptSafestAgent** — minimize storm/weather exposure (distance last)

Each agent is scored after weather-along; the published label is the winner of
that metric among the four candidates (not “whoever was asked”).

Report for every published route: distance, days (sea-state ETAs), weather along
the track, and fuel only when consumption was provided.

Prefer routes that keep waypoints outside storm buffers when a sea-clear detour exists.

Runs at:
- **pre-voyage** when the daemon flow includes `route_optimize`
- **noon** after noon weather track is written (current position + remaining WPs)

## Preconditions

- Voyage in registry with waypoints (master or remaining) and weather preferred.
- Storm map-layer available (mock or live); empty storms → weather-only scoring.

## Tasks

1. Load voyage + last storm snapshot.
2. Spawn RouteOptFastest/Shortest/Fuel/Safest agents and run them **in parallel**.
3. Reject any result that crosses land (hard). Build 6h plan; score weather + storms.
4. Assign each report label to the candidate that wins that metric.
5. If no alternate passes soft weather/storm caps, loosen weather limits stepwise;
   never re-admit a land-crossing route.
6. Write index JSON + **one JSON per objective** under `reports/{voyage}/subreports/`.
6. Store `optimized_routes` + `suggested_route` on registry (prefer storm-clear sea routes).
7. Enqueue `suggested_routes` job → `shipping_db.suggested_routes` (VO GeoJSON; one active row).

## Tools

| Tool | Purpose |
|------|---------|
| `optimize_all` | Fan-out to 4 objective agents (parallel) |
| `optimize` | Per-agent search — conventional A*, LLM (specialist MD), or voyagepm_be |

Live mode: `VPM_ROUTE_OPT_METHOD=llm` uses each agent's MD as the system brief
(`RouteOptFastestAgent.md` etc.). Conventional A* uses distinct edge costs per
objective. Land check still applies.

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
