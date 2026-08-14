# FleetAgent

## Role

List vessels, fleet positions, pick working vessel (/vessels, /fleet).

## Objective

Select the working vessel for this run and stash the fleet snapshot for downstream agents.

## Preconditions

- Prefer `state.artifacts.token` from AuthAgent (mock mode may tolerate empty token).

## Tasks

1. List tenant vessels.
2. Fetch fleet positions.
3. If `vessel_id` kwarg is set, select that vessel; else pick the first vessel.
4. Write vessel id/name onto state; store full lists under artifacts.
5. Set phase to Defaults.phase.

## Tools

| Tool | Purpose |
|------|---------|
| `list_vessels` | Tenant vessel catalogue |
| `fleet_positions` | Live / mock fleet map positions |

## Defaults

```json
{
  "phase": "fleet_ready",
  "pick": "first"
}
```

`pick`: `first` = first catalogue entry when no explicit `vessel_id`.

## Writes

- `state.vessel_id`, `state.vessel_name`
- `state.artifacts.vessels`, `state.artifacts.fleet`
- `state.phase`

## Failure

- Empty fleet → leave vessel unset, note count; VoyageAgent will abort cleanly.
