# NoonExcelWatchAgent

## Role

Poll combined noon Excel (or DB stub) on a timer and dispatch new rows to NoonOpsAgent.

## Objective

Simulate real-time noon arrival during testing: every `VPM_NOON_POLL_SECONDS`,
read unprocessed rows from `VPM_NOON_EXCEL_PATH` and process up to `VPM_NOON_BATCH_SIZE`.

## Preconditions

- Pre-voyage must exist in registry for the voyage_number.
- Excel must have `Latitude`, `Longitude`, `Voyage_Number` columns (BE noon format).

## Tasks

1. `ExcelNoonSource.fetch_new()` — skip rows in `processed_noon_ids`.
2. For each new row (oldest first), call `NoonOpsAgent` with noon dict.
3. Mark `noon_id` processed after success.

## Tools

Uses `noon_source.get_noon_source()` — switch `VPM_NOON_SOURCE=db` when DB is wired.

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
