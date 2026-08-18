# NoonOpsAgent

## Role

On noon report for a known voyage, replan next 7 days at 6-hour intervals from current lat/lon.

## Objective

Use pre-voyage CP speed + remaining master route from noon position; emit a templated
7-day report into the reports folder.

## Preconditions

- Noon file in inbox **or** a DB noon row (`VPM_NOON_SOURCE=db`) with `voyage_number`, `lat`, `lon`.
- Matching `voyage_number` already in registry from PreVoyageIngestAgent.

## Tasks

1. Parse noon report.
2. Load voyage from registry (speed + master waypoints).
3. Build remaining route from noon lat/lon.
4. Generate 6h waypoints for `VPM_NOON_HORIZON_HOURS` (default 168 = 7 days).
5. Fetch weather along plan; write combined track + optional weather report.
6. Fill `noon_7day_report.txt` template; write report.
7. Recompute the 4 storm-aware alternate routes from current position + remaining
   waypoints into `reports/{voyage}/subreports/`. In daemon forever mode this is queued on
   the heavy pool so noon ingest of the next row does not wait.
8. Archive file to processed/failed.

## Tools

| Tool | Purpose |
|------|---------|
| `parse_noon` | Parse noon Excel/CSV |

## Defaults

```json
{
  "phase": "noon_reported"
}
```

## Writes

- Registry `last_noon`, `noon_seven_day_plan`
- `reports/{voyage_number}/noon_7day_*`

## Failure

- Unknown voyage_number → failed/; note that pre-voyage must arrive first.
