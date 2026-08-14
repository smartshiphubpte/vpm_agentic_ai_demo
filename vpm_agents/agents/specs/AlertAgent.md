# AlertAgent

## Role

Configure & evaluate voyage alerts; create advisories.

## Objective

Ensure voyage alert rules exist, evaluate current conditions, and leave an operator
advisory summarizing hard weather / suggested-route status.

## Preconditions

- `state.voyage_id` required; otherwise skip.

## Tasks

1. Take alert rules from kwargs or Defaults.rules.
2. Configure rules on the voyage.
3. Evaluate alerts; store issued alerts on state.
4. Build advisory text (include hard-region count from weather).
5. Create advisory; store on state.
6. Set phase to Defaults.phase.

## Tools

| Tool | Purpose |
|------|---------|
| `configure` | Create / upsert alert rules |
| `evaluate` | Run alert evaluation |
| `advisory` | Create operator advisory |

## Defaults

```json
{
  "phase": "alerted",
  "rules": [
    {"type": "ETA", "threshold": 6},
    {"type": "Fuel", "threshold": 20},
    {"type": "GeoFence"},
    {"type": "RecommendedRoute"},
    {"type": "Weather"}
  ],
  "advisory_template": "Hard weather regions: {hard_count}. Suggested route published. Monitor ETA/fuel/geofence."
}
```

Tighten thresholds or drop rule types here to change monitoring posture without code edits.

## Writes

- `state.alerts`, `state.advisories`
- `state.phase`

## Failure

- No voyage → note and skip.
