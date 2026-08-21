# NoonExcelWatchAgent

## Role

Poll the noon drop folder **or** the client noon table on a timer and
dispatch new rows to NoonOpsAgent.

## Objective

Every `VPM_NOON_POLL_SECONDS`:
- `VPM_NOON_SOURCE=excel` — unprocessed rows from `VPM_NOON_INBOX_DIR` / `VPM_NOON_EXCEL_PATH`.
- `VPM_NOON_SOURCE=db` — unprocessed Departure → Noon → Arrival rows from the
  tenant client table (`std_enoonreporttable`) for **open** registry voyages.
  Needs `VPM_TENANT` + `prevoyage_db/.env`. Voyage numbers match after collapsing
  spaces (`2611 L` = `V2611L`); vessel id from ship lookup avoids cross-vessel hits.
- Pick **one** row: oldest `observed_at` first. Do not start the next row until the
  previous noon ingest job finishes (daemon mode gates on `noon:*` inflight/queued).

Each row is matched to a registry voyage by `voyage_number`. Two voyages → two
independent plans; two noons for the same voyage → last position wins, both reports written.
If the oldest unprocessed row is held (no pre-voyage yet, or that voyage still has a
`routeopt:` job pending/running), newer rows wait behind it.

## Preconditions

- Pre-voyage must exist in registry for the voyage_number.
- Excel must have `Latitude`, `Longitude`, `Voyage_Number` columns (BE noon format),
  or CSV with `voyage_number`, `lat`, `lon`.

## Tasks

1. `get_noon_sources()` (`DbNoonSource` when `VPM_NOON_SOURCE=db`).
2. Skip rows in `processed_noon_ids`. If the oldest unprocessed row's `voyage_number`
   is not in the registry, hold it (log once per poll) and do not process newer rows.
   Do not mark held rows processed.
3. Process that single oldest-ready row via `NoonOpsAgent`.
   In daemon forever mode (`enqueue=True`), queue one job on the ingest pool (`noon:{noon_id}`)
   only when no other `noon:*` job is queued or running.
   Noon route-optimize is then queued on the heavy pool so the next noon/inbox ingest is not held.
4. Mark `noon_id` processed after success; archive drop files when all rows are done.

## Tools

Uses `noon_source.get_noon_sources()` — `VPM_NOON_SOURCE=db` reads the client noon table.

## Defaults

```json
{
  "phase": "noon_excel_polled"
}
```

## Writes

Delegates to NoonOpsAgent (`voyage_track_weather_*.json`).

## Failure

Per-row failures logged; row not marked processed so it retries next poll.
Unknown drop files → `VPM_NOON_INBOX_DIR/failed/`.
