"""HTTP client for live voyagepm_be — same method names as MockBackend."""

from __future__ import annotations

from typing import Any

import httpx

from vpm_agents.config import settings


class LiveBackend:
    """Thin REST adapter. Cookie jar holds auth-token / company-name like the FE."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.base_url).rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=60.0, follow_redirects=True)
        self.token: str | None = None

    def login(self, email: str, password: str) -> dict:
        # ponytail: skip BE auth for now (localhost often down); ceiling: no session cookies;
        # upgrade: restore POST /login when voyagepm_be auth is required again
        self.token = "skip"
        return {
            "token": self.token,
            "email": email,
            "company": settings.company or "orion",
            "role": "ops",
        }

    def set_company(self, token: str, company: str) -> dict:
        return {"company": company}

    def identify_company(self, email: str) -> list[str]:
        return [settings.company] if settings.company else []

    def list_vessels(self, token: str) -> list[dict]:
        r = self.client.get("/vessels/client")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data", [])

    def fleet_positions(self, token: str) -> list[dict]:
        r = self.client.get("/fleet")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data", [])

    def get_vessel(self, token: str, vessel_id: str) -> dict:
        r = self.client.get(f"/vessels/client/{vessel_id}")
        r.raise_for_status()
        return r.json()

    def create_voyage(self, token: str, payload: dict) -> dict:
        r = self.client.post("/voyages", json=payload)
        r.raise_for_status()
        return r.json()

    def list_voyages(self, token: str, vessel_id: str | None = None) -> list[dict]:
        params = {"vesselId": vessel_id} if vessel_id else {}
        r = self.client.get("/voyages", params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data", [])

    def get_voyage(self, token: str, voyage_id: str) -> dict:
        r = self.client.get(f"/voyages/{voyage_id}")
        r.raise_for_status()
        return r.json()

    def save_route(self, token: str, voyage_id: str, kind: str, waypoints: list) -> dict:
        path = {"master": "/masterRoutes", "suggested": "/suggestedRoutes", "temp": "/tempRoutes"}.get(kind, "/tempRoutes")
        r = self.client.post(path, json={"voyageId": voyage_id, "route": waypoints})
        r.raise_for_status()
        return r.json() if r.content else {"ok": True}

    def get_routes(self, token: str, voyage_id: str) -> dict:
        return {"master": [], "suggested": [], "temp": []}

    def navapi_spine(self, token: str, start: dict, end: dict) -> dict:
        r = self.client.get(
            "/calc/SingleRoute",
            params={"startLat": start["lat"], "startLon": start["lon"], "endLat": end["lat"], "endLon": end["lon"]},
        )
        r.raise_for_status()
        return r.json()

    def chartworld_route(self, token: str, waypoints: list) -> dict:
        r = self.client.post("/routeCreation/create", json={"waypoints": waypoints})
        r.raise_for_status()
        return r.json()

    def optimize_route(
        self,
        token: str,
        objective: str,
        waypoints: list,
        weather: dict | None = None,
        storms: list | None = None,
    ) -> dict:
        # Live default: local conventional/LLM — no voyagepm_be required.
        # Set VPM_ROUTE_OPT_METHOD=backend to use BE VO endpoints instead.
        method = (settings.route_opt_method or "conventional").strip().lower()
        if method != "backend":
            from vpm_agents.tools.route_opt_dispatch import optimize_local

            return optimize_local(objective, waypoints, weather, storms)

        path_map = {
            "shortest": "/voyage-optimization/optimize/shortest-route",
            "fuel": "/voyage-optimization/optimize/fuel-efficient-route",
            "fastest": "/voyage-optimization/optimize/fastest-route",
            "safest": "/route-optimization/ro-safest/calculate",
            "lowest-fuel": "/route-optimization/ro-lowest-fuel/calculate",
        }
        path = path_map.get(objective, "/route-optimization/calculate")
        body: dict[str, Any] = {"waypoints": waypoints, "weather": weather or {}}
        if storms:
            body["storms"] = storms
        r = self.client.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def evaluate_route(self, token: str, waypoints: list) -> dict:
        r = self.client.post("/voyage-optimization/evaluate/route", json={"waypoints": waypoints})
        r.raise_for_status()
        return r.json()

    def weather_along_route(self, token: str, waypoints: list) -> dict:
        if (settings.weather_source or "live").strip().lower() == "backend":
            r = self.client.post("/weather/route", json={"waypoints": waypoints})
            r.raise_for_status()
            return r.json()
        from vpm_agents.tools.weather_live_api import fetch_weather_along_route

        return fetch_weather_along_route(waypoints)

    def weather_point(self, token: str, lat: float, lon: float) -> dict:
        if (settings.weather_source or "live").strip().lower() == "backend":
            r = self.client.get("/voyage-optimization/weather/point", params={"lat": lat, "lon": lon})
            r.raise_for_status()
            return r.json()
        from vpm_agents.tools.weather_live_api import fetch_weather_point

        return fetch_weather_point(lat, lon)

    def configure_alerts(self, token: str, voyage_id: str, rules: list[dict]) -> list[dict]:
        out = []
        for rule in rules:
            r = self.client.post("/alerts", json={"voyageId": voyage_id, **rule})
            r.raise_for_status()
            out.append(r.json())
        return out

    def evaluate_alerts(self, token: str, voyage_id: str) -> list[dict]:
        r = self.client.get("/executeSchedulars")
        r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else [r.json()]

    def create_advisory(self, token: str, voyage_id: str, text: str) -> dict:
        r = self.client.post("/advisories", json={"voyageId": voyage_id, "text": text})
        r.raise_for_status()
        return r.json()

    def storm_registry(self, token: str) -> list[dict]:
        r = self.client.get("/storm-pipeline/storms")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data", data.get("storms", []))

    def storm_map_layer(self, token: str) -> dict:
        """Active storms with positions / progressions (center + forecast track)."""
        r = self.client.get("/storm-pipeline/map-layer")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {"storms": data}

    def run_storm_watcher(self, token: str) -> dict:
        r = self.client.post("/storm-pipeline/watcher/run")
        r.raise_for_status()
        return r.json()

    def geofence_check(self, token: str, voyage_id: str, position: dict) -> dict:
        r = self.client.post("/geofence-check", json={"voyageId": voyage_id, **position})
        r.raise_for_status()
        return r.json()

    def geofence_optimize(self, token: str, voyage_id: str, waypoints: list) -> dict:
        r = self.client.post("/geofence-optimizer/run", json={"voyageId": voyage_id, "waypoints": waypoints})
        r.raise_for_status()
        return r.json()

    def ingest_noon(self, token: str, voyage_id: str, report: dict) -> dict:
        r = self.client.post("/noonReports", json={"voyageId": voyage_id, **report})
        r.raise_for_status()
        return r.json()

    def compute_cii(self, token: str, voyage_id: str) -> dict:
        r = self.client.get(f"/cii/voyage", params={"voyageId": voyage_id})
        r.raise_for_status()
        return r.json()

    def compute_eov(self, token: str, voyage_id: str) -> dict:
        r = self.client.post("/eov/compute", json={"voyageId": voyage_id})
        r.raise_for_status()
        return r.json()

    def voyage_performance(self, token: str, voyage_id: str) -> dict:
        r = self.client.get("/voyage-performance", params={"voyageId": voyage_id})
        r.raise_for_status()
        return r.json()


def get_backend() -> Any:
    from vpm_agents.config import settings
    from vpm_agents.tools.mock_backend import MockBackend

    if settings.mode == "live":
        return LiveBackend()
    return MockBackend()
