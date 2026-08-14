# VoyageAgent

## Role

Create/list voyages; persist master/suggested/temp routes.

## Objective

Open a voyage on the selected vessel with a master route so optimization and monitoring
have a concrete voyage id to attach to.

## Preconditions

- `state.vessel_id` must be set (FleetAgent). Otherwise abort with a note.

## Tasks

1. Build voyage payload from kwargs or Defaults (departure, destination, route, voyage_number).
2. Create voyage via backend.
3. Persist master route on the voyage.
4. Copy voyage id / number / master_route onto SessionState.
5. Set phase to Defaults.phase.

## Tools

| Tool | Purpose |
|------|---------|
| `create_voyage` | Open a new voyage |
| `list_voyages` | Inspect existing voyages |
| `save_route` | Persist master / suggested / temp waypoints |

## Defaults

```json
{
  "phase": "voyage_open",
  "departure": "Singapore",
  "destination": "Hong Kong",
  "route": [
    {"lat": 1.25, "lon": 103.85, "name": "Singapore"},
    {"lat": 5.0, "lon": 108.0, "name": "via SCS"},
    {"lat": 22.3, "lon": 114.2, "name": "Hong Kong"}
  ]
}
```

Edit `route` / ports here to change the default demo corridor without touching Python.

## Writes

- `state.voyage_id`, `state.voyage_number`, `state.master_route`
- `state.phase`

## Failure

- No vessel → note and return (do not create a voyage).
