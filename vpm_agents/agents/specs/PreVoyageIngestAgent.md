# PreVoyageIngestAgent

## Role

Ingest pre-voyage Excel/CSV from the inbox drop folder; build master route and 6-hour waypoints.

## Objective

When a new pre-voyage file appears, register the voyage and publish a full-route 6h plan
using CP speed from the file.

## Preconditions

- File in `VPM_INBOX_DIR` classified as pre_voyage:
  - Flat CSV/xlsx with `waypoints` + `cp_speed_kn`, **or**
  - SSH Pre-Dep Voyage workbook (sheets: `Voyage Details`, `Waypoints List`,
    `Vessel Details`, `CP Terms FWC`).

## Tasks

1. Parse voyage metadata + master waypoints + CP speed.
2. Walk master route at CP speed; emit a point every `VPM_WAYPOINT_INTERVAL_HOURS` (default 6).
3. Upsert voyage into `VPM_REGISTRY_PATH`.
4. Write master_route.json, six_hour_plan.json, and templated `pre_voyage_route.txt` under reports.
5. Archive inbox file to `processed/` (or `failed/` on error).

## Tools

| Tool | Purpose |
|------|---------|
| `parse_pre_voyage` | Parse Excel/CSV |
| `plan_waypoints` | 6h waypoint walk |

## Defaults

```json
{
  "phase": "pre_voyage_ingested"
}
```

## Writes

- Voyage registry entry
- `reports/{voyage_number}/…`
- SessionState master_route + artifacts.six_hour_plan

## Failure

- Parse / plan errors → note, move file to `failed/`, continue daemon loop.
