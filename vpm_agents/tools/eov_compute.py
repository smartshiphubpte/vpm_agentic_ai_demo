"""Local EOV formula engine — mirrors voyagepm_be/src/helpers/eovFormulas.js (default tenant)."""

from __future__ import annotations

from typing import Any

# ponytail: default-tenant only; live mode prefers GET /eovReport/compute for full tenant config
_DEFAULT_CFG: dict[str, Any] = {
    "foTags": [
        "Total_HFOME_Consumed_In_MT",
        "Total_VLSFOME_Consumed_In_MT",
        "Total_HFOAE_Consumed_In_MT",
        "Total_VLSFOAX_Consumed_In_MT",
        "Total_HFOBLR_Consumed_In_MT",
        "Total_VLSFOBLR_Consumed_In_MT",
    ],
    "mgoTags": [
        "Total_LSMGO_Consumed_In_MT",
        "Total_ULSGO_Consumed_In_MT",
        "Total_VLSGO_Consumed_In_MT",
    ],
    "mgoReportFilter": None,
    "bunkerFormula": "standard",
    "bunkerToleranceMin": -5,
    "bunkerToleranceMax": 5,
    "mgoAllowanceDailyMT": 0.1,
    "mgoAllowanceToleranceMin": -5,
    "mgoAllowanceToleranceMax": 5,
    "cpSpeedMargin": 0.5,
    "performanceCurveReportTypes": ["noon report"],
}


def _norm_type(report_type: str | None) -> str:
    return (report_type or "").replace("'", "").strip().lower()


def _sum_tags(nd: dict[str, Any] | None, tags: list[str]) -> float:
    nd = nd or {}
    return sum(float(nd.get(t) or 0) for t in tags)


def _include_mgo(report_type: str | None, filt: list[str] | None) -> bool:
    if not filt:
        return True
    n = _norm_type(report_type)
    return any(_norm_type(f) == n for f in filt)


def compute_eov_report(
    noon_reports: list[dict[str, Any]],
    *,
    cp_speed: float,
    cp_cons: float,
    good_weather_reports: list[dict[str, Any]] | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the same shape as voyagepm_be computeEOVReport (plus noonReports passthrough)."""
    tenant = {**_DEFAULT_CFG, **(cfg or {})}
    reports = list(noon_reports or [])
    gw = list(good_weather_reports or [])
    if not reports:
        return {
            "cpSpeed": cp_speed,
            "cpCons": cp_cons,
            "voyageSummary": {},
            "overallAnalysis": {},
            "goodWeatherAnalysis": {},
            "timeAnalysis": {},
            "bunkerAnalysis": {},
            "perReportData": [],
            "performanceCurveData": [],
            "noonReports": [],
            "tenantConfig": tenant,
        }

    first = reports[0]
    last_dist = next(
        (
            r
            for r in reversed(reports)
            if (r.get("noonreportdata") or {}).get("Distance_Covered_Since_SOV") is not None
        ),
        reports[-1],
    )
    nd_last = last_dist.get("noonreportdata") or {}
    nd_first = first.get("noonreportdata") or {}

    total_dist = float(nd_last.get("Distance_Covered_Since_SOV") or 0)
    total_steam = sum(float((r.get("noonreportdata") or {}).get("ME_Running_Hrs") or 0) for r in reports)
    avg_speed = total_dist / total_steam if total_steam else 0.0

    fo_rob0 = float(nd_first.get("Remaining_On_Board_HFO_In_MT") or 0) + float(
        nd_first.get("Remaining_On_Board_VLSFO_In_MT") or 0
    )
    fo_rob1 = float(nd_last.get("Remaining_On_Board_HFO_In_MT") or 0) + float(
        nd_last.get("Remaining_On_Board_VLSFO_In_MT") or 0
    )
    go_rob0 = sum(
        float(nd_first.get(k) or 0)
        for k in (
            "Remaining_On_Board_VLSGO_In_MT",
            "Remaining_On_Board_ULSGO_In_MT",
            "Remaining_On_Board_LSMGO_In_MT",
        )
    )
    go_rob1 = sum(
        float(nd_last.get(k) or 0)
        for k in (
            "Remaining_On_Board_VLSGO_In_MT",
            "Remaining_On_Board_ULSGO_In_MT",
            "Remaining_On_Board_LSMGO_In_MT",
        )
    )

    fo_cons = go_cons = slip_sum = rpm_sum = 0.0
    for r in reports:
        nd = r.get("noonreportdata") or {}
        fo_cons += _sum_tags(nd, tenant["foTags"])
        if _include_mgo(r.get("reporttype"), tenant.get("mgoReportFilter")):
            go_cons += _sum_tags(nd, tenant["mgoTags"])
        slip_sum += float(nd.get("Slip") or 0)
        rpm_sum += float(nd.get("ME_RPM") or 0)
    n_excl = max(1, len(reports) - 1)
    overall_avg_slip = slip_sum / n_excl
    total_avg_rpm = rpm_sum / n_excl

    gw_fo = gw_go = gw_steam = gw_dist = gw_slip = gw_rpm = 0.0
    for r in gw:
        nd = r.get("noonreportdata") or {}
        gw_fo += _sum_tags(nd, tenant["foTags"])
        if _include_mgo(r.get("reporttype"), tenant.get("mgoReportFilter")):
            gw_go += _sum_tags(nd, tenant["mgoTags"])
        gw_steam += float(nd.get("ME_Running_Hrs") or 0)
        gw_dist += float(nd.get("Distance") or 0)
        gw_slip += float(nd.get("Slip") or 0)
        gw_rpm += float(nd.get("ME_RPM") or 0)
    gw_n = len(gw) or 1

    margin = float(tenant.get("cpSpeedMargin") or 0.5)
    min_allowed_time = total_dist / cp_speed if cp_speed else 0.0
    max_allowed_time = total_dist / (cp_speed - margin) if (cp_speed - margin) > 0 else 0.0
    time_loss_or_gain = 0.0
    if total_steam < min_allowed_time:
        time_loss_or_gain = min_allowed_time - total_steam
    elif total_steam > max_allowed_time:
        time_loss_or_gain = max_allowed_time - total_steam

    min_pct = 1 + float(tenant["bunkerToleranceMin"]) / 100
    max_pct = 1 + float(tenant["bunkerToleranceMax"]) / 100
    if tenant.get("bunkerFormula") == "custom" and cp_speed and total_dist:
        base = (total_dist / cp_speed / 24) * cp_cons
        min_fo = base * min_pct
        max_fo = base * max_pct
    else:
        min_fo = (cp_cons * min_pct * total_steam) / 24
        max_fo = (cp_cons * max_pct * total_steam) / 24

    mgo_daily = float(tenant.get("mgoAllowanceDailyMT") or 0.1)
    mgo_min_pct = 1 + float(tenant.get("mgoAllowanceToleranceMin") or -5) / 100
    mgo_max_pct = 1 + float(tenant.get("mgoAllowanceToleranceMax") or 5) / 100
    min_go = (mgo_daily * mgo_min_pct * total_steam) / 24
    max_go = (mgo_daily * mgo_max_pct * total_steam) / 24

    fo_under_over = 0.0
    if fo_cons < min_fo:
        fo_under_over = min_fo - fo_cons
    elif fo_cons > max_fo:
        fo_under_over = max_fo - fo_cons
    go_under_over = 0.0
    if go_cons < min_go:
        go_under_over = min_go - go_cons
    elif go_cons > max_go:
        go_under_over = max_go - go_cons

    per_report = []
    for r in reports:
        nd = r.get("noonreportdata") or {}
        fo = _sum_tags(nd, tenant["foTags"])
        mgo = (
            _sum_tags(nd, tenant["mgoTags"])
            if _include_mgo(r.get("reporttype"), tenant.get("mgoReportFilter"))
            else 0.0
        )
        per_report.append(
            {
                "utcTime": r.get("utcTime"),
                "reporttype": r.get("reporttype"),
                "lat": r.get("lat"),
                "lon": r.get("lon"),
                "meRunningHrs": float(nd.get("ME_Running_Hrs") or 0),
                "distance": float(nd.get("Distance") or 0),
                "avgSpeed": float(nd.get("Avg_Speed") or 0),
                "meRpm": float(nd.get("ME_RPM") or 0),
                "slip": float(nd.get("Slip") or 0),
                "foCons": fo,
                "mgoCons": mgo,
                "windSpeed": float(nd.get("Wind_Speed") or nd.get("Wind_Force") or 0),
                "seaHeight": float(nd.get("Sea_Height") or 0),
                "swellHeight": float(nd.get("Swell_Height") or 0),
                "distanceSinceSOV": float(nd.get("Distance_Covered_Since_SOV") or 0),
                "currentVelocity": float(nd.get("Current_Velocity") or 0),
                "currentDirection": float(nd.get("Current_Direction") or 0),
            }
        )

    perf_types = {_norm_type(t) for t in (tenant.get("performanceCurveReportTypes") or ["noon report"])}
    perf = []
    for r in reports:
        if _norm_type(r.get("reporttype")) not in perf_types:
            continue
        nd = r.get("noonreportdata") or {}
        me_cons = _sum_tags(nd, tenant["foTags"])
        hrs = float(nd.get("ME_Running_Hrs") or 0)
        perf.append(
            {
                "utcTime": r.get("utcTime"),
                "avgSpeed": float(nd.get("Avg_Speed") or 0),
                "meRpm": float(nd.get("ME_RPM") or 0),
                "slip": float(nd.get("Slip") or 0),
                "meCons": me_cons,
                "meCons24Hrs": (me_cons / hrs) * 24 if hrs else 0.0,
                "windSpeed": float(nd.get("Wind_Speed") or nd.get("Wind_Force") or 0),
                "distance": float(nd.get("Distance") or 0),
            }
        )

    return {
        "cpSpeed": cp_speed,
        "cpCons": cp_cons,
        "voyageSummary": {
            "totalDistRun": total_dist,
            "totalSteamingTime": total_steam,
            "avgSpeed": avg_speed,
            "fuelOilCons": fo_rob0 - fo_rob1,
            "gasOilCons": go_rob0 - go_rob1,
            "avgRPM": total_avg_rpm,
        },
        "overallAnalysis": {
            "FoCons": fo_cons,
            "GoCons": go_cons,
            "overallAvgSpeed": avg_speed,
            "overallDailyFOCons": (fo_cons / total_steam) * 24 if total_steam else 0.0,
            "overallDailyGOCons": (go_cons / total_steam) * 24 if total_steam else 0.0,
            "overallAvgSlip": overall_avg_slip,
            "totalAvgRpm": total_avg_rpm,
            "totalSteamingTime": total_steam,
            "totalDistrun": total_dist,
        },
        "goodWeatherAnalysis": {
            "goodWeatherFoCons": gw_fo,
            "goodweatherGoCons": gw_go,
            "goodWeatherSteamingTime": gw_steam,
            "totalDistInGoodWeather": gw_dist,
            "goodWeatherAvgSlip": gw_slip / gw_n if gw else 0.0,
            "goodWeatherAvgRPM": gw_rpm / gw_n if gw else 0.0,
            "goodWeatherAvgSpeed": gw_dist / gw_steam if gw_steam else 0.0,
            "dailyFoCons": (gw_fo / gw_steam) * 24 if gw_steam else 0.0,
            "dailyGoCons": (gw_go / gw_steam) * 24 if gw_steam else 0.0,
        },
        "timeAnalysis": {
            "minAllowedTime": min_allowed_time,
            "maxAllowedTime": max_allowed_time,
            "timeLossOrGain": time_loss_or_gain,
        },
        "bunkerAnalysis": {
            "minAllowableFOCons": min_fo,
            "maxAllowableFOCons": max_fo,
            "minAllowableGOCons": min_go,
            "maxAllowableGOCons": max_go,
            "fuelFOUnderOverCons": fo_under_over,
            "fuelGOUnderOverCons": go_under_over,
            "mgoDailyAllowance": mgo_daily,
        },
        "perReportData": per_report,
        "performanceCurveData": perf,
        "noonReports": reports,
        "tenantConfig": tenant,
    }


def good_weather_filter(
    reports: list[dict[str, Any]],
    *,
    bf_limit: float = 4.0,
    wave_limit: float = 5.0,
) -> list[dict[str, Any]]:
    """Simple onboard BF/wave filter when historical weather API is unavailable."""
    out = []
    for r in reports:
        if "noon" not in _norm_type(r.get("reporttype")) and "arrival" not in _norm_type(
            r.get("reporttype")
        ):
            continue
        nd = r.get("noonreportdata") or {}
        bf = float(nd.get("Wind_Force") or nd.get("Beaufort_Scale") or nd.get("Wind_Speed") or 0)
        wave = float(nd.get("Sea_Height") or nd.get("Wave_Height") or nd.get("Swell_Height") or 0)
        if bf <= bf_limit and wave <= wave_limit:
            out.append(r)
    return out


if __name__ == "__main__":
    sample = [
        {
            "reporttype": "Departure Report",
            "utcTime": "2025-06-05T10:00:00Z",
            "lat": 13.1,
            "lon": 100.8,
            "noonreportdata": {
                "ME_Running_Hrs": 0,
                "Distance": 0,
                "Distance_Covered_Since_SOV": 0,
                "Remaining_On_Board_HFO_In_MT": 2100,
                "Remaining_On_Board_VLSFO_In_MT": 0,
                "Remaining_On_Board_LSMGO_In_MT": 50,
            },
        },
        {
            "reporttype": "Noon Report",
            "utcTime": "2025-06-06T04:00:00Z",
            "lat": 14.0,
            "lon": 105.0,
            "noonreportdata": {
                "ME_Running_Hrs": 18,
                "Distance": 200,
                "Distance_Covered_Since_SOV": 200,
                "Avg_Speed": 11.1,
                "ME_RPM": 70,
                "Slip": 22,
                "Wind_Force": 3,
                "Sea_Height": 0.5,
                "Total_HFOME_Consumed_In_MT": 18,
                "Total_HFOAE_Consumed_In_MT": 1,
                "Total_LSMGO_Consumed_In_MT": 0.1,
                "Remaining_On_Board_HFO_In_MT": 2081,
                "Remaining_On_Board_LSMGO_In_MT": 49.9,
            },
        },
        {
            "reporttype": "Arrival Report",
            "utcTime": "2025-06-07T10:00:00Z",
            "lat": 22.0,
            "lon": 113.0,
            "noonreportdata": {
                "ME_Running_Hrs": 30,
                "Distance": 340,
                "Distance_Covered_Since_SOV": 540,
                "Avg_Speed": 11.3,
                "ME_RPM": 71,
                "Slip": 23,
                "Wind_Force": 4,
                "Sea_Height": 0.8,
                "Total_HFOME_Consumed_In_MT": 28,
                "Total_HFOAE_Consumed_In_MT": 2,
                "Total_LSMGO_Consumed_In_MT": 0.2,
                "Remaining_On_Board_HFO_In_MT": 2051,
                "Remaining_On_Board_LSMGO_In_MT": 49.7,
            },
        },
    ]
    data = compute_eov_report(sample, cp_speed=11.5, cp_cons=24.0, good_weather_reports=sample[1:])
    assert data["voyageSummary"]["totalDistRun"] == 540
    assert abs(data["overallAnalysis"]["FoCons"] - 49) < 0.01
    assert len(data["perReportData"]) == 3
    print("eov_compute self-check ok")
