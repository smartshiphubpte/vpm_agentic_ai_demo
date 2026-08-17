# EndOfVoyageReportAgent

## Role

Build the End of Voyage report when an Arrival noon report lands.

## Objective

Produce a full EOV PDF (China Express template sections): cover metrics, LLM executive /
overall / bunker narratives, noon tables, good-weather summary, performance graphs,
voyage map overlay, formulations, appendix — then dump under `reports/{voyage}/` and email.

## Preconditions

- Voyage exists in the registry (pre-voyage ingested).
- `noon_history` populated (or live `GET /eovReport/compute` available).

## Tasks

1. Resolve EOV numbers: live `/eovReport/compute` when `VPM_MODE=live`, else local formula engine.
2. Write cover + metric tables from computed data.
3. Call LLM for executive summary, overall narrative, bunker claim narrative.
4. Plot performance curves from time-series noon data.
5. Render voyage map from the same OSM.de tile API as the VoyagePM GUI (route overlay); optional `VPM_VOYAGE_MAP_URL` override.
6. Assemble PDF + JSON sidecar; email PDF.
7. Prefer `background=True` / daemon queue so the poll loop never waits.

## Tools

| Tool | Purpose |
|------|---------|
| `build_end_of_voyage_report` | Section pipeline + PDF |
| `submit_eov_report` | Thread-pool job (non-blocking) |

## Defaults

```json
{
  "phase": "eov_reported",
  "good_weather_bf": 4,
  "good_weather_wave_m": 5
}
```

## Writes

- `reports/{voyage}/end_of_voyage_report_*.{pdf,txt,json}`
- `reports/{voyage}/eov_assets/*`
- `registry.eov_status`, `eov_report_path`

## Failure

- Missing voyage → note and skip.
- LLM / map API down → placeholders / local plot; still write PDF.
