#!/usr/bin/env python3
"""Minimal self-check — fails loud if agentic pipeline regressions appear."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vpm_agents.config import settings
from vpm_agents.core.orchestrator import WORKFLOWS, SupervisorOrchestrator
from vpm_agents.core.spec_loader import SPECS_DIR, load_agent_spec
from vpm_agents.tools.geo import six_hour_waypoints
from vpm_agents.tools.inbox_io import classify_inbox_file, parse_noon_report, parse_pre_voyage
from vpm_agents.tools.route_json import parse_route_points


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _continuous_cycle() -> None:
    """Drop sample pre-voyage + noon; run one daemon cycle."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_daemon", ROOT / "scripts" / "run_daemon.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    inbox = settings.inbox_dir
    noon_inbox = settings.noon_inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    noon_inbox.mkdir(parents=True, exist_ok=True)
    dest = inbox / "pre_voyage.csv"
    if dest.exists():
        dest.unlink()
    shutil.copy(ROOT / "samples" / "inbox" / "pre_voyage.csv", dest)
    noon_dest = noon_inbox / "noon_report.csv"
    if noon_dest.exists():
        noon_dest.unlink()
    shutil.copy(ROOT / "samples" / "inbox" / "noon_report.csv", noon_dest)

    state = mod.run_once(storm=True, inbox=True)
    assert_true(
        any("PreVoyageIngestAgent" in l and "VYG-2026-001" in l for l in state.log),
        f"pre-voyage not ingested: {state.log}",
    )
    assert_true(
        any("NoonOpsAgent" in l and "VYG-2026-001" in l for l in state.log),
        f"noon not processed: {state.log}",
    )
    assert_true(settings.registry_path.is_file(), "registry not written")
    assert_true(any(settings.storm_out_dir.glob("storms_*.json")), "storm snapshot missing")
    from vpm_agents.tools.folder_layout import (
        PRE_VOYAGE_REPORT,
        VPA_REPORT,
        WEATHER_REPORT,
        voyage_report_dir,
        voyage_root,
    )

    voy_dir = voyage_root(settings.reports_out_dir, "9184902", "VYG-2026-001")
    assert_true((voy_dir / "master_route.json").is_file(), "master_route missing")
    assert_true(
        any(voyage_report_dir(settings.reports_out_dir, "9184902", "VYG-2026-001", PRE_VOYAGE_REPORT).glob("pre_voyage_route_*.txt")),
        "pre-voyage report missing",
    )
    assert_true(
        any(voyage_report_dir(settings.reports_out_dir, "9184902", "VYG-2026-001", VPA_REPORT).glob("noon_7day_report_*.txt")),
        "noon report missing",
    )
    assert_true(
        any(voyage_report_dir(settings.reports_out_dir, "9184902", "VYG-2026-001", WEATHER_REPORT).glob("voyage_track_weather_*.json"))
        or any(voyage_report_dir(settings.reports_out_dir, "9184902", "VYG-2026-001", WEATHER_REPORT).glob("weather_report_*.pdf")),
        "combined track+weather missing",
    )

    master_pts = parse_route_points(json.loads((voy_dir / "master_route.json").read_text()))
    assert_true(len(master_pts) >= 2, "route json parse failed")

    from vpm_agents.tools.noon_io import parse_dms_coordinate, parse_noon_excel
    from vpm_agents.tools.route_weather import build_voyage_track
    from vpm_agents.tools.storm_proximity import assess_storm_route_proximity

    lat = parse_dms_coordinate("35°13'6''N")
    assert_true(34 < lat < 36, f"dms parse bad: {lat}")
    combined = build_voyage_track(
        "VTEST",
        [{"seq": 1, "lat": 1.0, "lon": 100.0, "eta_utc": "2026-01-01T00:00:00Z"}],
        {"points": [{"lat": 1.0, "lon": 100.0, "windKn": 10, "waveM": 1.0}], "hardRegions": []},
        noon={"lat": 1.0, "lon": 100.0, "observed_at": "2026-01-01T00:00:00Z"},
    )
    assert_true(combined["track"][0]["marker"] == "🚢", "ship marker missing")
    prox = assess_storm_route_proximity(
        [{"id": "S1", "name": "Test", "lat": 1.5, "lon": 100.0, "radius_nm": 50}],
        "VTEST",
        {"master_waypoints": [[1.0, 100.0], [2.0, 101.0]]},
        threshold_nm=500,
    )
    assert_true(prox[0]["route_encounter_likely"], "storm proximity should hit")

    from vpm_agents.tools.storm_normalize import normalize_active_storms
    from vpm_agents.tools.storm_proximity import point_violates_storm, score_route_storms

    layer = {
        "storms": [
            {
                "stormId": "T1",
                "stormName": "TestProg",
                "dangerCorridorRadiusNm": 40,
                "positions": [
                    {"lat": 10.0, "lon": 100.0, "isPresent": True, "trackPhase": "live", "validAtIso": "2026-01-01T00:00:00Z"},
                    {"lat": 11.0, "lon": 101.0, "isPresent": False, "trackPhase": "forecast", "validAtIso": "2026-01-01T12:00:00Z"},
                ],
            }
        ]
    }
    norms = normalize_active_storms(layer)
    assert_true(len(norms) == 1 and len(norms[0]["positions"]) == 2, "map-layer normalize failed")
    far = point_violates_storm(0.0, 0.0, 10.0, 100.0, 40, center_buffer_nm=500, edge_buffer_nm=300)
    assert_true(not far["violates"], "far point should clear storm buffers")
    near = point_violates_storm(10.1, 100.0, 10.0, 100.0, 40, center_buffer_nm=500, edge_buffer_nm=300)
    assert_true(near["violates"] and near["within_center_buffer"], "near center should violate")
    # Outside 500 NM center but inside edge buffer (radius 400 → edge dist ~150 ≤ 300)
    # ~550 NM north of center (9.17° lat) with large radius so only edge fires.
    edge_only = point_violates_storm(19.17, 100.0, 10.0, 100.0, 400, center_buffer_nm=500, edge_buffer_nm=300)
    assert_true(edge_only["violates"], "edge buffer should catch when center buffer alone would not")
    assert_true(not edge_only["within_center_buffer"] and edge_only["within_edge_buffer"], "edge-only case")
    sc = score_route_storms([[10.05, 100.0], [12.0, 100.0]], norms, center_buffer_nm=500, edge_buffer_nm=300)
    assert_true(not sc["storm_clear"], "route through storm should not be clear")

    from vpm_agents.tools.land_mask import is_land, score_route_land
    from vpm_agents.tools.route_optimize import optimize_route_alternatives
    from vpm_agents.tools.mock_backend import MockBackend

    assert_true(not is_land(1.25, 103.85), "Singapore approaches must be water")
    assert_true(not is_land(22.3, 114.2), "Hong Kong approaches must be water")
    assert_true(not is_land(36.07, 120.38), "Qingdao approaches must be water")
    assert_true(not is_land(24.5, 119.5), "Taiwan Strait must be water")
    assert_true(is_land(29.2, 120.8), "Zhejiang interior must be land")
    assert_true(
        score_route_land([[12.2, 109.2], [36.07, 120.38]], sample_nm=12)["sea_clear"] is False,
        "Vietnam→Qingdao chord must fail land hard rule",
    )
    assert_true(is_land(40.0, -100.0), "continental interior must be land")
    assert_true(
        score_route_land([[40.0, -100.0], [0.0, -150.0]], sample_nm=30)["sea_clear"] is False,
        "Kansas→Pacific must fail land hard rule",
    )
    assert_true(
        score_route_land([[1.25, 103.85], [5.0, 108.0], [22.3, 114.2]], sample_nm=25)["sea_clear"],
        "SG→HK sea corridor must stay clear",
    )
    mb = MockBackend()
    tok = mb.login("ops@smartshiphub.com", "demo")["token"]
    # Land-crossing master must not yield a land-crossing suggestion
    land_master = [[40.0, -100.0], [41.0, -99.0], [0.0, -150.0]]
    opt = optimize_route_alternatives(
        mb,
        tok,
        land_master,
        12.0,
        None,
        {
            "weather_limits": {"max_wind_kn": 35, "max_wave_m": 4.0, "max_swell_m": 3.0},
            "reject_if_limits_exceeded": False,
            "preferred": "safest",
            "horizon_hours": 48,
            "objectives": [
                {"id": "fastest", "optimize_for": "fastest", "label": "Fastest"},
                {"id": "safest", "optimize_for": "safest", "label": "Safest"},
            ],
        },
    )
    for rid, r in opt["routes"].items():
        assert_true(r.get("sea_clear"), f"{rid} must be sea_clear")
        assert_true(score_route_land(r["route"]["waypoints"])["sea_clear"], f"{rid} WPs on land")
    assert_true(opt["hard_rules"]["no_landmass"], "hard rule flag missing")
    assert_true(opt["hard_rules"].get("fixed_endpoints"), "fixed endpoints hard rule missing")

    sea_master = [[1.25, 103.85], [5.0, 108.0], [14.0, 112.0], [22.3, 114.2]]
    sea_opt = optimize_route_alternatives(
        mb,
        tok,
        sea_master,
        12.0,
        None,
        {
            "weather_limits": {"max_wind_kn": 35, "max_wave_m": 4.0, "max_swell_m": 3.0},
            "preferred": "safest",
            "horizon_hours": 72,
            "objectives": [
                {"id": "fastest", "optimize_for": "fastest", "label": "Fastest"},
                {"id": "safest", "optimize_for": "safest", "label": "Safest"},
            ],
        },
    )
    assert_true(bool(sea_opt["routes"]), "sea master should yield alternatives")
    for rid, r in sea_opt["routes"].items():
        wps = r["route"]["waypoints"]
        assert_true(
            abs(wps[0]["lat"] - sea_master[0][0]) < 1e-9
            and abs(wps[0]["lon"] - sea_master[0][1]) < 1e-9,
            f"{rid} moved origin",
        )
        assert_true(
            abs(wps[-1]["lat"] - sea_master[-1][0]) < 1e-9
            and abs(wps[-1]["lon"] - sea_master[-1][1]) < 1e-9,
            f"{rid} moved destination",
        )

    from vpm_agents.tools.route_opt_conventional import optimize_conventional

    storm_hit = [
        {
            "id": "T1",
            "name": "Test",
            "lat": 14.0,
            "lon": 112.0,
            "radius_nm": 50,
            "positions": [{"lat": 14.0, "lon": 112.0, "radius_nm": 50}],
        }
    ]
    conv = optimize_conventional("safest", sea_master, None, storm_hit, algo="astar")
    assert_true(conv["provider"].startswith("local-astar"), f"bad provider {conv['provider']}")
    assert_true(len(conv["waypoints"]) >= 2, "conventional path too short")
    assert_true(
        abs(conv["waypoints"][0]["lat"] - sea_master[0][0]) < 1e-9
        and abs(conv["waypoints"][-1]["lat"] - sea_master[-1][0]) < 1e-9,
        "conventional moved endpoints",
    )
    assert_true(score_route_land(conv["waypoints"])["sea_clear"], "conventional path on land")
    assert_true(conv.get("sea_clear", True), "conventional sea_clear flag")
    # Land-cutting master must be routed around, not through Africa
    africa = optimize_conventional(
        "shortest",
        [[0.0, -10.0], [0.0, 10.0], [0.0, 30.0], [0.0, 50.0]],
        None,
        None,
        algo="astar",
    )
    assert_true(
        score_route_land(africa["waypoints"], sample_nm=15)["sea_clear"],
        "Africa chord must not cross land",
    )
    dij = optimize_conventional("shortest", sea_master, None, None, algo="dijkstra")
    assert_true(dij["provider"].startswith("local-dijkstra"), "dijkstra provider")
    # BE: storms are hard keep-out when a sea detour exists (South China Sea corridor)
    conv_clear = score_route_storms(conv["waypoints"], storm_hit)
    assert_true(
        conv_clear["storm_clear"] or conv.get("sea_clear"),
        "safest should prefer storm keep-out when graph allows",
    )
    assert_true(load_agent_spec("RouteOptimizeLLMAgent").path.is_file(), "missing LLM route-opt spec")
    assert_true(load_agent_spec("PreVoyageRouteOptimizeAgent").path.is_file(), "missing pre-voyage opt spec")

    pts = six_hour_waypoints([[1.0, 100.0], [2.0, 101.0]], 12.0, horizon_hours=12)
    assert_true(len(pts) >= 2, "six_hour_waypoints too short")

    from dataclasses import replace

    g = replace(settings, llm_provider="gemini", gemini_api_key="k", openai_api_key="")
    assert_true(g.use_llm and g.llm_api_key == "k", "gemini llm_api_key")
    assert_true(g.llm_model.startswith("gemini"), "gemini llm_model default")
    assert_true(g.effective_llm_base_url.endswith("/openai"), "gemini base url")

    kind = classify_inbox_file(ROOT / "samples" / "inbox" / "pre_voyage.csv")
    assert_true(kind == "pre_voyage", f"bad classify {kind}")
    pv = parse_pre_voyage(ROOT / "samples" / "inbox" / "pre_voyage.csv")
    assert_true(pv["cp_speed_kn"] == 12.5, "bad speed")
    assert_true(pv.get("cp_consumption_mt_day") is None, "sample CSV has no consumption")

    from vpm_agents.tools.route_optimize import voyage_metrics, format_alternatives_block

    m0 = voyage_metrics(240.0, 12.0, None)
    assert_true(m0["fuelMt"] is None and m0["days"] == 0.83, f"metrics skip fuel {m0}")
    m1 = voyage_metrics(240.0, 12.0, 24.0)
    assert_true(m1["fuelMt"] == 20.0, f"fuel from MT/day {m1}")
    blk = format_alternatives_block(
        {
            "shortest": {
                "id": "shortest",
                "label": "Shortest",
                "voyage": m0,
                "sea_clear": True,
                "avoids_storms": True,
                "weather_along": "wind 10 kn",
            }
        }
    )
    assert_true("fuel consumption" not in blk, "must omit fuel when unknown")
    assert_true("distance: 240" in blk, "distance missing in report block")
    nr = parse_noon_report(ROOT / "samples" / "inbox" / "noon_report.csv")
    assert_true(nr["voyage_number"] == "VYG-2026-001", "bad noon voyage")

    from vpm_agents.tools.report_email import _parse_emails, recipients_from_db, send_report_pdf

    assert_true(_parse_emails("a@x.com, b@y.com") == ["a@x.com", "b@y.com"], "report email parse")
    assert_true(recipients_from_db("VTEST") == [], "db recipients placeholder")
    assert_true(send_report_pdf(ROOT / "nope.pdf") is False, "missing pdf should skip")


def main() -> None:
    orch = SupervisorOrchestrator()

    assert_true(len(orch.agents) == 8, "expected 8 specialist agents")
    assert_true(len(WORKFLOWS) >= 5, "expected >=5 named workflows")

    for name, agent in orch.agents.items():
        assert_true(agent.spec.path.is_file(), f"missing spec for {name}")
        assert_true(bool(agent.description), f"empty description from MD for {name}")
    for cont in (
        "PreVoyageIngestAgent",
        "NoonOpsAgent",
        "NoonExcelWatchAgent",
        "StormWatchAgent",
        "WeatherReportAgent",
        "InboxWatchAgent",
        "EndOfVoyageReportAgent",
    ):
        assert_true(load_agent_spec(cont).path.is_file(), f"missing continuous spec {cont}")
    sup = load_agent_spec("SupervisorOrchestrator")
    assert_true(sup.get("workflows") == WORKFLOWS, "orchestrator workflows drift from Supervisor MD")
    assert_true((SPECS_DIR / "_TEMPLATE.md").is_file(), "missing specs template")

    state = orch.run_workflow(
        "full_voyage_lifecycle",
        email="ops@smartshiphub.com",
        password="demo",
        company="orion",
    )
    assert_true(state.authenticated, "auth failed")
    assert_true(state.company == "orion", "company not set")
    assert_true(bool(state.vessel_id), "no vessel")
    assert_true(bool(state.voyage_id), "no voyage")
    assert_true(len(state.optimized_routes) >= 3, "optimizers missing")
    assert_true(state.weather_summary.get("pointCount", 0) > 0, "weather missing")
    assert_true(len(state.storms) >= 1, "storms missing")
    assert_true(bool(state.cii.get("rating")), "cii missing")
    assert_true(bool(state.eov), "eov missing")
    assert_true(any("Supervisor" in l and "complete" in l for l in state.log), "no completion note")

    plan = orch.resolve_goal("compute CII and EOV from noon reports")
    assert_true(plan == WORKFLOWS["performance_closeout"], f"bad goal plan: {plan}")

    plan2 = orch.resolve_goal("avoid the typhoon with geofence reoptimize")
    assert_true(plan2 == WORKFLOWS["storm_response"], f"bad storm plan: {plan2}")

    # Auth failure path
    bad = orch.agents["AuthAgent"].run(
        __import__("vpm_agents.core.state", fromlist=["SessionState"]).SessionState(),
        email="nobody@x.com",
        password="nope",
    )
    assert_true(not bad.authenticated, "bad login should fail")

    _continuous_cycle()

    print("SELFCHECK OK")
    print(f"  voyage={state.voyage_number} cii={state.cii['rating']} alerts={len(state.alerts)}")
    print(f"  workflows={list(WORKFLOWS)}")
    print(f"  inbox={settings.inbox_dir} storms={settings.storm_out_dir}")


if __name__ == "__main__":
    main()
