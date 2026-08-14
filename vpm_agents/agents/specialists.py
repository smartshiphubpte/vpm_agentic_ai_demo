"""Domain specialist agents — one agent per VoyagePM capability area.

Task briefs and tunable defaults live in `agents/specs/{AgentName}.md`.
"""

from __future__ import annotations

from typing import Any

from vpm_agents.core.base import Agent, Tool, ToolResult
from vpm_agents.core.state import SessionState
from vpm_agents.config import settings
from vpm_agents.tools.land_mask import score_route_land
from vpm_agents.tools.route_optimize import pin_route_endpoints


def _token(state: SessionState) -> str:
    return state.artifacts.get("token", "")


class AuthAgent(Agent):
    name = "AuthAgent"

    def build_tools(self) -> list[Tool]:
        return [
            Tool("login", "Authenticate user", self._login, {"email": "str", "password": "str"}),
            Tool("set_company", "Select tenant company", self._set_company, {"company": "str"}),
            Tool("identify_company", "Resolve companies from email domain", self._identify, {"email": "str"}),
        ]

    def _login(self, email: str, password: str) -> ToolResult:
        data = self.backend.login(email, password)
        return ToolResult(ok=True, data=data)

    def _set_company(self, company: str, token: str = "") -> ToolResult:
        data = self.backend.set_company(token, company)
        return ToolResult(ok=True, data=data)

    def _identify(self, email: str) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.identify_company(email))

    def run(self, state: SessionState, email: str = "", password: str = "", company: str = "") -> SessionState:
        email = email or state.user_email
        password = password or state.artifacts.get("password") or self.spec.get("password_fallback", "demo")
        res = self.call("login", email=email, password=password)
        if not res.ok:
            state.note(self.name, f"login failed: {res.error}")
            return state
        data = res.data
        state.authenticated = True
        state.user_email = data["email"]
        state.role = data.get("role", "")
        state.company = company or data.get("company", "")
        state.artifacts["token"] = data["token"]
        state.artifacts["companies"] = data.get("companies", [])
        if company and company != data.get("company"):
            self.call("set_company", company=company, token=data["token"])
            state.company = company
        state.phase = self.spec.get("phase", "authenticated")
        state.note(self.name, f"authenticated as {state.user_email} @ {state.company} ({state.role})")
        return state


class FleetAgent(Agent):
    name = "FleetAgent"

    def build_tools(self) -> list[Tool]:
        return [
            Tool("list_vessels", "List tenant vessels", lambda: ToolResult(ok=True, data=self.backend.list_vessels(_token(self._state)))),
            Tool("fleet_positions", "Live fleet map positions", lambda: ToolResult(ok=True, data=self.backend.fleet_positions(_token(self._state)))),
        ]

    def run(self, state: SessionState, vessel_id: str | None = None) -> SessionState:
        self._state = state
        vessels = self.backend.list_vessels(_token(state))
        positions = self.backend.fleet_positions(_token(state))
        state.artifacts["vessels"] = vessels
        state.artifacts["fleet"] = positions
        chosen = None
        if vessel_id:
            chosen = next((v for v in vessels if v["id"] == vessel_id), None)
        if not chosen and vessels and self.spec.get("pick", "first") == "first":
            chosen = vessels[0]
        if chosen:
            state.vessel_id = chosen["id"]
            state.vessel_name = chosen.get("name")
        state.phase = self.spec.get("phase", "fleet_ready")
        state.note(self.name, f"fleet={len(vessels)} selected={state.vessel_id} ({state.vessel_name})")
        return state


class VoyageAgent(Agent):
    name = "VoyageAgent"

    def build_tools(self) -> list[Tool]:
        return [
            Tool("create_voyage", "Create voyage", self._create),
            Tool("list_voyages", "List voyages", self._list),
            Tool("save_route", "Save route kind", self._save_route),
        ]

    def _create(self, token: str, payload: dict) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.create_voyage(token, payload))

    def _list(self, token: str, vessel_id: str | None = None) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.list_voyages(token, vessel_id))

    def _save_route(self, token: str, voyage_id: str, kind: str, waypoints: list) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.save_route(token, voyage_id, kind, waypoints))

    def run(
        self,
        state: SessionState,
        departure: str | None = None,
        destination: str | None = None,
        route: list | None = None,
        voyage_number: str | None = None,
    ) -> SessionState:
        if not state.vessel_id:
            state.note(self.name, "no vessel selected — abort")
            return state
        departure = departure or self.spec.get("departure", "Singapore")
        destination = destination or self.spec.get("destination", "Hong Kong")
        default_route = route or list(self.spec.get("route") or [])
        if not default_route:
            default_route = [
                {"lat": 1.25, "lon": 103.85, "name": "Singapore"},
                {"lat": 5.0, "lon": 108.0, "name": "via SCS"},
                {"lat": 22.3, "lon": 114.2, "name": "Hong Kong"},
            ]
        payload = {
            "vesselId": state.vessel_id,
            "departure": departure,
            "destination": destination,
            "voyageNumber": voyage_number,
            "route": default_route,
        }
        voy = self.backend.create_voyage(_token(state), payload)
        state.voyage_id = voy["id"]
        state.voyage_number = voy["voyageNumber"]
        state.master_route = default_route
        self.backend.save_route(_token(state), state.voyage_id, "master", default_route)
        state.phase = self.spec.get("phase", "voyage_open")
        state.note(self.name, f"opened {state.voyage_number} ({state.voyage_id}) {departure}→{destination}")
        return state


class RouteOptimizationAgent(Agent):
    name = "RouteOptimizationAgent"

    def build_tools(self) -> list[Tool]:
        return [
            Tool("navapi_spine", "Get NavAPI corridor", self._spine),
            Tool("optimize", "Run VO optimizer", self._opt),
            Tool("evaluate", "Estimate fuel+ETA", self._eval),
        ]

    def _spine(self, token: str, start: dict, end: dict) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.navapi_spine(token, start, end))

    def _opt(self, token: str, objective: str, waypoints: list, weather: dict | None = None) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.optimize_route(token, objective, waypoints, weather))

    def _eval(self, token: str, waypoints: list) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.evaluate_route(token, waypoints))

    def run(self, state: SessionState, objectives: list[str] | None = None) -> SessionState:
        objectives = objectives or list(self.spec.get("objectives") or ["shortest", "fuel", "fastest", "safest"])
        route = state.master_route or state.artifacts.get("spine", {}).get("waypoints", [])
        if len(route) >= 2:
            spine = self.backend.navapi_spine(_token(state), route[0], route[-1])
            state.artifacts["spine"] = spine
            route = spine.get("waypoints", route)
        results = {}
        origin, dest = route[0], route[-1]
        for obj in objectives:
            opt = self.backend.optimize_route(
                _token(state), obj, route, weather=state.weather_summary or None
            )
            wps = pin_route_endpoints(opt.get("waypoints") or route, origin, dest)
            opt = {**opt, "waypoints": wps}
            if wps and not score_route_land(wps, clearance_nm=settings.land_clearance_nm)["sea_clear"]:
                state.note(self.name, f"{obj} rejected — crosses landmass")
                continue
            results[obj] = opt
        state.optimized_routes = results
        preferred = self.spec.get("preferred", "fuel")
        best = results.get(preferred)
        if not best:
            for key in self.spec.get("preferred_fallbacks") or ["lowest-fuel"]:
                best = results.get(key)
                if best:
                    break
        if not best and results:
            best = next(iter(results.values()))
        state.suggested_route = best["waypoints"] if best else []
        if state.voyage_id and state.suggested_route:
            self.backend.save_route(_token(state), state.voyage_id, "suggested", state.suggested_route)
            self.backend.save_route(_token(state), state.voyage_id, "temp", state.suggested_route)
        state.phase = self.spec.get("phase", "optimized")
        state.note(
            self.name,
            f"optimized objectives={list(results)} suggested_fuelMt={(best or {}).get('fuelMt')} etaH={(best or {}).get('etaHours')}",
        )
        return state


class WeatherAgent(Agent):
    name = "WeatherAgent"

    def build_tools(self) -> list[Tool]:
        return [
            Tool("weather_route", "Weather along waypoints", self._route),
            Tool("weather_point", "Single point weather", self._point),
        ]

    def _route(self, token: str, waypoints: list) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.weather_along_route(token, waypoints))

    def _point(self, token: str, lat: float, lon: float) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.weather_point(token, lat, lon))

    def run(self, state: SessionState) -> SessionState:
        prefer = self.spec.get("prefer_route", "suggested")
        if prefer == "suggested":
            pts = state.suggested_route or state.master_route
        else:
            pts = state.master_route or state.suggested_route
        if not pts:
            state.note(self.name, "no route points — skip")
            return state
        wx = self.backend.weather_along_route(_token(state), pts)
        state.weather_summary = {
            "pointCount": len(wx.get("points", [])),
            "hardCount": len(wx.get("hardRegions", [])),
            "provider": wx.get("provider"),
        }
        state.hard_regions = wx.get("hardRegions", [])
        state.artifacts["weather_points"] = wx.get("points", [])
        state.phase = self.spec.get("phase", "weathered")
        state.note(self.name, f"weather points={state.weather_summary['pointCount']} hard={state.weather_summary['hardCount']}")
        return state


class AlertAgent(Agent):
    name = "AlertAgent"

    def build_tools(self) -> list[Tool]:
        return [
            Tool("configure", "Create alert rules", self._cfg),
            Tool("evaluate", "Run alert evaluation", self._eval),
            Tool("advisory", "Create advisory", self._adv),
        ]

    def _cfg(self, token: str, voyage_id: str, rules: list) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.configure_alerts(token, voyage_id, rules))

    def _eval(self, token: str, voyage_id: str) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.evaluate_alerts(token, voyage_id))

    def _adv(self, token: str, voyage_id: str, text: str) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.create_advisory(token, voyage_id, text))

    def run(self, state: SessionState, rules: list[dict] | None = None) -> SessionState:
        if not state.voyage_id:
            state.note(self.name, "no voyage — skip")
            return state
        rules = rules or list(self.spec.get("rules") or [])
        created = self.backend.configure_alerts(_token(state), state.voyage_id, rules)
        issued = self.backend.evaluate_alerts(_token(state), state.voyage_id)
        state.alerts = issued
        tmpl = self.spec.get(
            "advisory_template",
            "Hard weather regions: {hard_count}. Suggested route published. Monitor ETA/fuel/geofence.",
        )
        text = tmpl.format(hard_count=len(state.hard_regions))
        adv = self.backend.create_advisory(_token(state), state.voyage_id, text)
        state.advisories = [adv]
        state.phase = self.spec.get("phase", "alerted")
        state.note(self.name, f"rules={len(created)} issued={len(issued)} advisory={adv['id']}")
        return state


class StormGeofenceAgent(Agent):
    name = "StormGeofenceAgent"

    def build_tools(self) -> list[Tool]:
        return [
            Tool("storms", "List storms", self._storms),
            Tool("watcher", "Run storm watcher", self._watch),
            Tool("geofence", "Check geofence", self._geo),
            Tool("reoptimize", "Storm re-optimize", self._reopt),
        ]

    def _storms(self, token: str) -> ToolResult:
        if hasattr(self.backend, "storm_map_layer"):
            try:
                from vpm_agents.tools.storm_normalize import normalize_active_storms

                return ToolResult(ok=True, data=normalize_active_storms(self.backend.storm_map_layer(token)))
            except Exception:
                pass
        return ToolResult(ok=True, data=self.backend.storm_registry(token))

    def _watch(self, token: str) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.run_storm_watcher(token))

    def _geo(self, token: str, voyage_id: str, position: dict) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.geofence_check(token, voyage_id, position))

    def _reopt(self, token: str, voyage_id: str, waypoints: list) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.geofence_optimize(token, voyage_id, waypoints))

    def run(self, state: SessionState, position: dict | None = None) -> SessionState:
        self.backend.run_storm_watcher(_token(state))
        state.storms = self.backend.storm_registry(_token(state))
        if not state.voyage_id:
            state.note(self.name, f"storms={len(state.storms)} (no voyage)")
            return state
        pos = position or (state.master_route[0] if state.master_route else dict(self.spec.get("position") or {"lat": 10.0, "lon": 110.0}))
        check = self.backend.geofence_check(_token(state), state.voyage_id, pos)
        state.artifacts["geofence"] = check
        storm_key = self.spec.get("storm_route_key", "storm-safest")
        if check.get("reoptimize"):
            result = self.backend.geofence_optimize(
                _token(state), state.voyage_id, state.suggested_route or state.master_route
            )
            state.suggested_route = result["route"]["waypoints"]
            state.optimized_routes[storm_key] = result["route"]
            state.note(self.name, f"storm re-optimize published suggested; hits={check['hits']}")
        else:
            state.note(self.name, f"storms={len(state.storms)} geofence clear")
        state.phase = self.spec.get("phase", "storm_checked")
        return state


class PerformanceReportAgent(Agent):
    name = "PerformanceReportAgent"

    def build_tools(self) -> list[Tool]:
        return [
            Tool("noon", "Ingest noon report", self._noon),
            Tool("cii", "Compute CII", self._cii),
            Tool("eov", "Compute EOV", self._eov),
            Tool("performance", "Voyage KPIs", self._perf),
        ]

    def _noon(self, token: str, voyage_id: str, report: dict) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.ingest_noon(token, voyage_id, report))

    def _cii(self, token: str, voyage_id: str) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.compute_cii(token, voyage_id))

    def _eov(self, token: str, voyage_id: str) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.compute_eov(token, voyage_id))

    def _perf(self, token: str, voyage_id: str) -> ToolResult:
        return ToolResult(ok=True, data=self.backend.voyage_performance(token, voyage_id))

    def run(self, state: SessionState, noon_reports: list[dict] | None = None) -> SessionState:
        if not state.voyage_id:
            state.note(self.name, "no voyage — skip")
            return state
        reports = noon_reports or list(self.spec.get("noon_reports") or [])
        ingested = [self.backend.ingest_noon(_token(state), state.voyage_id, r) for r in reports]
        state.noon_reports = ingested
        state.cii = self.backend.compute_cii(_token(state), state.voyage_id)
        state.eov = self.backend.compute_eov(_token(state), state.voyage_id)
        state.performance = self.backend.voyage_performance(_token(state), state.voyage_id)
        state.phase = self.spec.get("phase", "reported")
        state.note(
            self.name,
            f"noon={len(ingested)} cii={state.cii.get('rating')} savingsMt={state.eov.get('savingsMt')}",
        )
        return state
