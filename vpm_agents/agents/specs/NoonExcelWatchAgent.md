# NoonExcelWatchAgent

## Role

Poll the noon drop folder (and optional combined Excel / DB stub) on a timer and
dispatch new rows to NoonOpsAgent.

## Objective

Every `VPM_NOON_POLL_SECONDS`:
- Read new files in `VPM_NOON_INBOX_DIR` (all pending files / rows this poll).
- Optionally drip unprocessed rows from `VPM_NOON_EXCEL_PATH` up to `VPM_NOON_BATCH_SIZE`.

Each row is matched to a registry voyage by `voyage_number`. Two voyages → two
independent plans; two noons for the same voyage → last position wins, both reports written.

## Preconditions

- Pre-voyage must exist in registry for the voyage_number.
- Excel must have `Latitude`, `Longitude`, `Voyage_Number` columns (BE noon format),
  or CSV with `voyage_number`, `lat`, `lon`.

## Tasks

1. `FolderNoonSource.fetch_new()` then `ExcelNoonSource` / `DbNoonSource`.
2. Skip rows in `processed_noon_ids`.
3. For each new row (oldest first), call `NoonOpsAgent` with noon dict.
4. Mark `noon_id` processed after success; archive drop files when all rows are done.

## Tools

Uses `noon_source.get_noon_sources()` — switch `VPM_NOON_SOURCE=db` when DB is wired.

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
