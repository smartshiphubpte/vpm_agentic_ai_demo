# PerformanceReportAgent

## Role

Noon ingest, CII, EOV, voyage performance / savings.

## Objective

Close out (or sample) voyage performance: ingest noon reports, compute CII + EOV, and
attach voyage KPIs / savings onto SessionState.

## Preconditions

- `state.voyage_id` required; otherwise skip.

## Tasks

1. Take noon reports from kwargs or Defaults.noon_reports.
2. Ingest each noon report.
3. Compute CII, EOV, and voyage performance.
4. Write results onto state; set phase to Defaults.phase.

## Tools

| Tool | Purpose |
|------|---------|
| `noon` | Ingest noon report |
| `cii` | Compute CII rating |
| `eov` | Compute EOV / savings |
| `performance` | Voyage KPI bundle |

## Defaults

```json
{
  "phase": "reported",
  "noon_reports": [
    {"lat": 3.0, "lon": 105.0, "speed": 12.0, "foMt": 19.2, "distanceNm": 290},
    {"lat": 8.0, "lon": 110.0, "speed": 11.5, "foMt": 18.8, "distanceNm": 275},
    {"lat": 15.0, "lon": 112.0, "speed": 12.2, "foMt": 20.1, "distanceNm": 310}
  ]
}
```

Replace `noon_reports` with real samples for performance_closeout demos.

## Writes

- `state.noon_reports`, `state.cii`, `state.eov`, `state.performance`
- `state.phase`

## Failure

- No voyage → note and skip.
