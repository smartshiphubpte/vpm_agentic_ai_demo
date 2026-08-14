# RouteOptimizationAgent

## Role

NavAPI spine, ChartWorld, VO optimizers (shortest/fuel/fastest/safest).

## Objective

Produce optimized route variants for the configured objectives and publish a preferred
suggested (+ temp) route on the open voyage.

**Hard rules:** (1) any optimizer result whose waypoints cross landmass is rejected
(ships stay on water); (2) origin and destination lat/lon are pinned to the input
route ends — only intermediate waypoints may change. Weather remains a soft
preference handled by VO / later pre-voyage scoring.

## Preconditions

- Prefer `state.master_route` with ≥2 waypoints; else try `artifacts.spine.waypoints`.

## Tasks

1. Take objectives from kwargs or Defaults.objectives.
2. If route has ≥2 points, fetch NavAPI spine and prefer those waypoints.
3. For each objective, call VO optimize (pass weather summary when present).
4. Store all results on `state.optimized_routes`.
5. Pick preferred variant per Defaults.preferred (fallback keys listed in Defaults).
6. Save suggested + temp routes when `voyage_id` exists.
7. Set phase to Defaults.phase.

## Tools

| Tool | Purpose |
|------|---------|
| `navapi_spine` | Corridor between start/end |
| `optimize` | VO optimizer for one objective |
| `evaluate` | Fuel + ETA estimate |

## Defaults

```json
{
  "phase": "optimized",
  "objectives": ["shortest", "fuel", "fastest", "safest"],
  "preferred": "fuel",
  "preferred_fallbacks": ["lowest-fuel"]
}
```

Change `preferred` to `safest` (or reorder `objectives`) to bias the published suggested route.

## Writes

- `state.artifacts.spine`
- `state.optimized_routes`, `state.suggested_route`
- `state.phase`

## Failure

- Short / missing route → still attempt with whatever points exist; empty results note via supervisor log.
