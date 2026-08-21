"""Build End-of-Voyage report PDF section-by-section (China Express EOV template)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.agent_log import progress
from vpm_agents.tools.eov_compute import compute_eov_report, good_weather_filter
from vpm_agents.tools.folder_layout import END_OF_VOYAGE_REPORT, voyage_report_dir
from vpm_agents.tools.report_narrative import llm_section
from vpm_agents.tools.templates import fill_template, write_text_pdf


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fmt(n: Any, digits: int = 2) -> str:
    try:
        return f"{float(n):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _llm(system: str, user: str) -> str:
    return llm_section(system, user, "(LLM unavailable — set CURSOR_API_KEY for narrative analysis.)")


def _eov_rows_from_registry(rec: dict[str, Any]) -> list[dict[str, Any]]:
    hist = rec.get("noon_history") or []
    rows = []
    for h in hist:
        er = h.get("eov_row")
        if isinstance(er, dict) and er.get("noonreportdata") is not None:
            rows.append(er)
            continue
        # Minimal fallback from slim noon payload
        rows.append(
            {
                "reporttype": h.get("report_type") or "Noon Report",
                "utcTime": h.get("observed_at"),
                "lat": h.get("lat"),
                "lon": h.get("lon"),
                "noonreportdata": {
                    "Avg_Speed": h.get("avg_speed_kn"),
                    "Distance": 0,
                },
            }
        )
    return rows


def resolve_eov_data(
    backend: Any,
    token: str,
    rec: dict[str, Any],
    voyage_number: str,
) -> dict[str, Any]:
    """Prefer live BE compute; fall back to local engine on registry noon_history."""
    vessel_id = str(rec.get("vessel_id") or "")
    cp_speed = float(rec.get("cp_speed_kn") or 0)
    cp_cons = float(rec.get("cp_consumption_mt_day") or 0)

    if settings.mode == "live" and hasattr(backend, "compute_eov_report") and vessel_id:
        try:
            data = backend.compute_eov_report(
                token,
                vessel_id=vessel_id,
                voyage_number=voyage_number,
                cp_speed=cp_speed,
                cp_cons=cp_cons,
                bf=4,
                wv=5,
            )
            if isinstance(data, dict) and data.get("voyageSummary"):
                return data
        except Exception as e:
            progress("EOVReport", f"{voyage_number} live compute failed → local: {e}")

    rows = _eov_rows_from_registry(rec)
    gw = good_weather_filter(rows)
    return compute_eov_report(rows, cp_speed=cp_speed, cp_cons=cp_cons, good_weather_reports=gw)


def _cover_metrics(rec: dict, data: dict) -> dict[str, str]:
    vs = data.get("voyageSummary") or {}
    oa = data.get("overallAnalysis") or {}
    return {
        "vessel_name": str(rec.get("vessel_name") or rec.get("vessel_id") or "—"),
        "voyage_number": str(rec.get("voyage_number") or "—"),
        "source_port": str(rec.get("source_port") or "—"),
        "dest_port": str(rec.get("dest_port") or "—"),
        "distance_nm": _fmt(oa.get("totalDistrun") or vs.get("totalDistRun")),
        "time_hrs": _fmt(oa.get("totalSteamingTime") or vs.get("totalSteamingTime")),
        "avg_speed_kn": _fmt(oa.get("overallAvgSpeed") or vs.get("avgSpeed")),
        "avg_rpm": _fmt(oa.get("totalAvgRpm") or vs.get("avgRPM")),
    }


def _section_executive(rec: dict, data: dict) -> str:
    payload = {
        "vessel": rec.get("vessel_name"),
        "voyage": rec.get("voyage_number"),
        "from": rec.get("source_port"),
        "to": rec.get("dest_port"),
        "condition": rec.get("condition"),
        "cp_speed": data.get("cpSpeed"),
        "cp_cons": data.get("cpCons"),
        "voyageSummary": data.get("voyageSummary"),
        "overallAnalysis": data.get("overallAnalysis"),
        "goodWeatherAnalysis": data.get("goodWeatherAnalysis"),
        "timeAnalysis": data.get("timeAnalysis"),
        "bunkerAnalysis": data.get("bunkerAnalysis"),
    }
    return _llm(
        "You write End-of-Voyage executive summaries for ship operators. "
        "3 short paragraphs + up to 3 bullet advisories. Factual, CP/fuel claim aware. No markdown headings.",
        json.dumps(payload, default=str)[:12000],
    )


def _section_overall_narrative(rec: dict, data: dict) -> str:
    payload = {
        "overall": data.get("overallAnalysis"),
        "good_weather": data.get("goodWeatherAnalysis"),
        "route": {
            "master_nm": data.get("overallAnalysis", {}).get("totalDistrun"),
            "ssh_recommended_nm": 0,
        },
    }
    return _llm(
        "Write a short analysis comparing good-weather-only vs entire-voyage performance for an EOV report. "
        "2 paragraphs. Mention route deviation only if SSH recommended distance is non-zero.",
        json.dumps(payload, default=str)[:8000],
    )


def _section_bunker_narrative(data: dict) -> str:
    ta = data.get("timeAnalysis") or {}
    ba = data.get("bunkerAnalysis") or {}
    fo_over = float(ba.get("fuelFOUnderOverCons") or 0)
    go_over = float(ba.get("fuelGOUnderOverCons") or 0)
    time_result = "NO TIME LOSS OR GAIN"
    tlg = float(ta.get("timeLossOrGain") or 0)
    if tlg > 0.05:
        time_result = f"TIME GAIN {_fmt(tlg)} hrs"
    elif tlg < -0.05:
        time_result = f"TIME LOSS {_fmt(abs(tlg))} hrs"
    fo_result = "NO FUEL CLAIM" if abs(fo_over) < 0.05 else ("FUEL CLAIM" if fo_over < 0 else "UNDER CONSUMPTION")
    go_result = "NO FUEL CLAIM" if abs(go_over) < 0.05 else ("FUEL CLAIM" if go_over < 0 else "UNDER CONSUMPTION")
    table = "\n".join(
        [
            f"  CP Speed (kts): {_fmt(data.get('cpSpeed'))}",
            f"  Allowed Time En Route (hrs): {_fmt(ta.get('minAllowedTime'))} – {_fmt(ta.get('maxAllowedTime'))}",
            f"  Time result: {time_result}",
            f"  Daily CP FO Allowance (MT): {_fmt(data.get('cpCons'))}",
            f"  Actual FO (MT): {_fmt((data.get('overallAnalysis') or {}).get('FoCons'))}",
            f"  Allowed FO (MT): {_fmt(ba.get('minAllowableFOCons'))} – {_fmt(ba.get('maxAllowableFOCons'))}",
            f"  FO result: {fo_result}",
            f"  Actual MGO (MT): {_fmt((data.get('overallAnalysis') or {}).get('GoCons'))}",
            f"  Allowed MGO (MT): {_fmt(ba.get('minAllowableGOCons'))} – {_fmt(ba.get('maxAllowableGOCons'))}",
            f"  MGO result: {go_result}",
        ]
    )
    note = _llm(
        "One short paragraph on time/bunker claim implications for this voyage. Plain text.",
        table,
    )
    return table + "\n\n" + note


def _overall_table(data: dict) -> str:
    oa = data.get("overallAnalysis") or {}
    gw = data.get("goodWeatherAnalysis") or {}
    return "\n".join(
        [
            f"  {'Metric':<32}  {'Good Weather':>14}  {'Entire Voyage':>14}",
            f"  {'Distance Sailed (NM)':<32}  {_fmt(gw.get('totalDistInGoodWeather')):>14}  {_fmt(oa.get('totalDistrun')):>14}",
            f"  {'Time en Route (hrs)':<32}  {_fmt(gw.get('goodWeatherSteamingTime')):>14}  {_fmt(oa.get('totalSteamingTime')):>14}",
            f"  {'Average Speed (kts)':<32}  {_fmt(gw.get('goodWeatherAvgSpeed')):>14}  {_fmt(oa.get('overallAvgSpeed')):>14}",
            f"  {'Average Slip (%)':<32}  {_fmt(gw.get('goodWeatherAvgSlip')):>14}  {_fmt(oa.get('overallAvgSlip')):>14}",
            f"  {'Average RPM':<32}  {_fmt(gw.get('goodWeatherAvgRPM')):>14}  {_fmt(oa.get('totalAvgRpm')):>14}",
            f"  {'FO Consumption (MT)':<32}  {_fmt(gw.get('goodWeatherFoCons')):>14}  {_fmt(oa.get('FoCons')):>14}",
            f"  {'MGO Consumption (MT)':<32}  {_fmt(gw.get('goodweatherGoCons')):>14}  {_fmt(oa.get('GoCons')):>14}",
        ]
    )


def _noon_table(data: dict) -> str:
    lines = [
        f"  {'#':>3}  {'UTC':<20}  {'Type':<16}  {'Dist':>7}  {'Hrs':>6}  {'Spd':>6}  "
        f"{'RPM':>6}  {'Slip':>6}  {'FO':>7}  {'MGO':>6}"
    ]
    for i, r in enumerate(data.get("perReportData") or [], 1):
        lines.append(
            f"  {i:3d}  {str(r.get('utcTime') or '—')[:20]:<20}  "
            f"{str(r.get('reporttype') or '—')[:16]:<16}  "
            f"{_fmt(r.get('distance')):>7}  {_fmt(r.get('meRunningHrs')):>6}  "
            f"{_fmt(r.get('avgSpeed')):>6}  {_fmt(r.get('meRpm')):>6}  "
            f"{_fmt(r.get('slip')):>6}  {_fmt(r.get('foCons')):>7}  {_fmt(r.get('mgoCons')):>6}"
        )
    return "\n".join(lines) if len(lines) > 1 else "  (no noon reports)"


def _good_weather_block(data: dict) -> str:
    rows = [
        r
        for r in (data.get("perReportData") or [])
        if (float(r.get("windSpeed") or 0) <= 4 and float(r.get("seaHeight") or 0) <= 5)
        and ("noon" in str(r.get("reporttype") or "").lower() or "arrival" in str(r.get("reporttype") or "").lower())
    ]
    lines = [f"  Good-weather noons (BF ≤ 4 and sea ≤ 5 m): {len(rows)}"]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"  {i}. {r.get('utcTime')}  dist={_fmt(r.get('distance'))} NM  "
            f"FO={_fmt(r.get('foCons'))} MT  MGO={_fmt(r.get('mgoCons'))} MT"
        )
    if not rows:
        lines.append("  (none met the good-weather filter)")
    note = llm_section(
        "One short paragraph on good-weather-only performance vs the entire voyage. Plain text.",
        {"goodWeatherAnalysis": data.get("goodWeatherAnalysis"), "overallAnalysis": data.get("overallAnalysis")},
        "",
    )
    if note:
        lines += ["", note]
    return "\n".join(lines)


def _section_formulations(data: dict) -> str:
    cp = _fmt(data.get("cpSpeed"))
    cons = _fmt(data.get("cpCons"))
    return "\n".join(
        [
            f"  Min. allowed CP speed (time gain): CP speed − 0.5 kn  (CP={cp})",
            "  Max. allowed CP speed (time loss): CP speed",
            "  Min. allowed time = total distance / CP speed",
            "  Max. allowed time = total distance / (CP speed − 0.5)",
            f"  Daily allowed CP consumption (MT): {cons}",
            "  Fuel tolerance: ±5% of (time at sea × daily CP cons / 24)  [default tenant]",
            "  If consumption inside min–max → no fuel claim; above max → fuel claim.",
        ]
    )


def _section_appendix(rec: dict) -> str:
    alerts = rec.get("eov_alerts") or rec.get("alerts") or []
    lines = []
    if not alerts:
        lines.append("  (none recorded in registry for this voyage)")
        return "\n".join(lines)
    for i, a in enumerate(alerts, 1):
        if isinstance(a, dict):
            lines.append(
                f"  {i}. {a.get('type', 'Alert')}  {a.get('issued_at', '')}  {a.get('description', a)}"
            )
        else:
            lines.append(f"  {i}. {a}")
    return "\n".join(lines)


def _plot_performance_curves(out_dir: Path, data: dict) -> list[Path]:
    """Generate FO/speed performance PNGs from performanceCurveData."""
    series = data.get("performanceCurveData") or []
    if len(series) < 2:
        return []
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        progress("EOVReport", "matplotlib missing — skip graphs (pip install matplotlib)")
        return []

    paths: list[Path] = []
    xs = list(range(1, len(series) + 1))
    fo = [float(p.get("meCons") or 0) for p in series]
    spd = [float(p.get("avgSpeed") or 0) for p in series]
    dist = [float(p.get("distance") or 0) for p in series]
    hrs = [float(p.get("meCons24Hrs") or 0) for p in series]

    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.bar(xs, fo, color="#2c5f8a", label="FO (MT)")
    ax.set_xlabel("Noon interval")
    ax.set_ylabel("FO consumption (MT)")
    ax.set_title("HFO/MGO consumption between noon reports")
    ax.legend(loc="upper right")
    fig.tight_layout()
    p1 = out_dir / "eov_curve_fuel.png"
    fig.savefig(p1, dpi=120)
    plt.close(fig)
    paths.append(p1)

    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(xs, dist, "o-", color="#2c5f8a", label="Distance (NM)")
    ax2 = ax.twinx()
    ax2.plot(xs, hrs, "s--", color="#c45c26", label="FO MT/day")
    ax.set_xlabel("Good-weather / noon day")
    ax.set_ylabel("Distance (NM)")
    ax2.set_ylabel("FO MT/day")
    ax.set_title("Daily distance and FO burn")
    fig.tight_layout()
    p2 = out_dir / "eov_curve_daily.png"
    fig.savefig(p2, dpi=120)
    plt.close(fig)
    paths.append(p2)

    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(xs, spd, "o-", color="#1a7a4c")
    ax.set_xlabel("Noon interval")
    ax.set_ylabel("Avg speed (kts)")
    ax.set_title("Average speed trend")
    fig.tight_layout()
    p3 = out_dir / "eov_curve_speed.png"
    fig.savefig(p3, dpi=120)
    plt.close(fig)
    paths.append(p3)
    return paths


def _fetch_voyage_map(
    out_dir: Path,
    voyage_number: str,
    points: list[dict],
    *,
    source_port: str = "",
    dest_port: str = "",
) -> Path | None:
    """Voyage map: optional VPM_VOYAGE_MAP_URL override, else GUI basemap (OSM.de tiles)."""
    pts = [{"lat": float(p["lat"]), "lon": float(p["lon"])} for p in points if p.get("lat") is not None]
    if len(pts) < 2:
        return None
    dest = out_dir / "eov_voyage_map.png"
    url = (settings.voyage_map_url or "").strip()
    if url:
        try:
            import httpx

            r = httpx.post(
                url,
                json={"voyage_number": voyage_number, "points": pts},
                timeout=60.0,
            )
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").lower()
            if "image" in ctype or r.content[:8] == b"\x89PNG\r\n\x1a\n":
                dest.write_bytes(r.content)
                return dest
            data = r.json()
            if isinstance(data, dict):
                b64 = data.get("image_base64") or data.get("png_base64")
                if b64:
                    import base64

                    dest.write_bytes(base64.b64decode(b64))
                    return dest
                img_url = data.get("url") or data.get("image_url")
                if img_url:
                    img = httpx.get(img_url, timeout=60.0)
                    img.raise_for_status()
                    dest.write_bytes(img.content)
                    return dest
        except Exception as e:
            progress("EOVReport", f"VPM_VOYAGE_MAP_URL failed → OSM.de tiles: {e}")

    from vpm_agents.tools.voyage_map import render_voyage_map

    try:
        return render_voyage_map(
            pts,
            dest,
            voyage_number=voyage_number,
            labels=(source_port or "Departure", dest_port or "Arrival"),
        )
    except Exception as e:
        progress("EOVReport", f"OSM.de map failed: {e}")
        return None


def build_end_of_voyage_report(
    *,
    backend: Any,
    registry: Any,
    voyage_number: str,
    token: str = "",
) -> dict[str, Any]:
    """Part-by-part EOV generation → reports/{imo}/{voyage}/end_of_voyage_report/."""
    t0 = time.monotonic()
    rec, key = registry.find_voyage(voyage_number)
    if not rec or not key:
        raise ValueError(f"voyage {voyage_number} not in registry")
    if rec.get("eov_report_path") and rec.get("eov_status") == "done":
        progress("EOVReport", f"{key} already done → {rec.get('eov_report_path')}")
        return {"voyage_number": key, "path": rec.get("eov_report_path"), "skipped": True}

    registry.upsert(key, {"eov_status": "running", "eov_started_at": datetime.now(timezone.utc).isoformat()})
    progress("EOVReport", f"{key} resolve data")
    data = resolve_eov_data(backend, token, rec, key)

    out_dir = voyage_report_dir(settings.reports_out_dir, str(rec.get("vessel_id") or ""), key, END_OF_VOYAGE_REPORT)
    assets = out_dir / "eov_assets"
    assets.mkdir(parents=True, exist_ok=True)

    rec = {**rec, "voyage_number": rec.get("voyage_number") or key}
    metrics = _cover_metrics(rec, data)
    progress("EOVReport", f"{key} cover metrics")
    progress("EOVReport", f"{key} executive summary (LLM)")
    executive = _section_executive(rec, data)
    progress("EOVReport", f"{key} overall table + narrative (LLM)")
    overall_table = _overall_table(data)
    overall_narrative = _section_overall_narrative(rec, data)
    progress("EOVReport", f"{key} time & bunker (LLM)")
    bunker = _section_bunker_narrative(data)
    progress("EOVReport", f"{key} noon + good-weather tables")
    noon_table = _noon_table(data)
    good_weather = _good_weather_block(data)
    formulations = _section_formulations(data)
    appendix = _section_appendix(rec)

    progress("EOVReport", f"{key} graphs")
    graphs = _plot_performance_curves(assets, data)
    track_pts = []
    for r in data.get("perReportData") or []:
        if r.get("lat") is not None and r.get("lon") is not None:
            track_pts.append({"lat": r["lat"], "lon": r["lon"]})
    if not track_pts:
        for wp in rec.get("master_waypoints") or []:
            if isinstance(wp, (list, tuple)) and len(wp) >= 2:
                track_pts.append({"lat": wp[0], "lon": wp[1]})
            elif isinstance(wp, dict):
                track_pts.append({"lat": wp.get("lat"), "lon": wp.get("lon")})
    progress("EOVReport", f"{key} voyage map")
    map_path = _fetch_voyage_map(
        assets,
        key,
        track_pts,
        source_port=str(rec.get("source_port") or "Departure"),
        dest_port=str(rec.get("dest_port") or "Arrival"),
    )

    stamp = _stamp()
    generated_at = datetime.now(timezone.utc).isoformat()
    ctx = {
        **metrics,
        "generated_at": generated_at,
        "executive_summary": executive,
        "overall_table": overall_table,
        "overall_narrative": overall_narrative,
        "bunker_block": bunker,
        "noon_table": noon_table,
        "good_weather_block": good_weather,
        "formulations_block": formulations,
        "appendix_block": appendix,
    }
    body = fill_template("end_of_voyage_report.txt", ctx)

    txt_path = out_dir / f"end_of_voyage_report_{stamp}.txt"
    txt_path.write_text(body, encoding="utf-8")
    json_path = out_dir / f"end_of_voyage_report_{stamp}.json"
    json_path.write_text(
        json.dumps({"voyage_number": key, "eov": data, "template_keys": list(ctx)}, indent=2, default=str),
        encoding="utf-8",
    )

    images = list(graphs)
    if map_path:
        images.insert(0, map_path)
    progress("EOVReport", f"{key} write PDF + email")
    pdf_path = write_text_pdf(
        out_dir,
        f"end_of_voyage_report_{stamp}.pdf",
        body,
        voyage_number=key,
        for_send=True,
        images=images,
    )

    registry.upsert(
        key,
        {
            "eov_status": "done",
            "eov_report_path": str(pdf_path),
            "eov_report_json": str(json_path),
            "eov_finished_at": datetime.now(timezone.utc).isoformat(),
            "phase": "eov_reported",
        },
    )
    elapsed = time.monotonic() - t0
    progress("EOVReport", f"{key} → {pdf_path.name}", elapsed_s=elapsed)
    return {"voyage_number": key, "path": str(pdf_path), "json": str(json_path), "elapsed_s": elapsed}


if __name__ == "__main__":
    from vpm_agents.tools.eov_compute import compute_eov_report as _c

    d = _c(
        [
            {
                "reporttype": "Noon Report",
                "utcTime": "t",
                "lat": 1,
                "lon": 2,
                "noonreportdata": {
                    "ME_Running_Hrs": 24,
                    "Distance": 250,
                    "Distance_Covered_Since_SOV": 250,
                    "Avg_Speed": 10,
                    "Total_HFOME_Consumed_In_MT": 20,
                },
            }
        ],
        cp_speed=11,
        cp_cons=24,
    )
    assert d["perReportData"]
    from vpm_agents.tools.templates import fill_template as _ft

    rec = {"vessel_name": "Test", "voyage_number": "V1", "source_port": "A", "dest_port": "B"}
    metrics = _cover_metrics(rec, d)
    body = _ft(
        "end_of_voyage_report.txt",
        {
            **metrics,
            "generated_at": "t",
            "executive_summary": "exec",
            "overall_table": _overall_table(d),
            "overall_narrative": "narr",
            "bunker_block": "bunker",
            "noon_table": _noon_table(d),
            "good_weather_block": "gw",
            "formulations_block": _section_formulations(d),
            "appendix_block": "(none)",
        },
    )
    assert "END OF VOYAGE REPORT" in body
    assert "COVER METRICS" in body
    assert "EXECUTIVE SUMMARY" in body
    assert "NOON REPORT ANALYSIS" in body
    print("eov_report self-check ok")
