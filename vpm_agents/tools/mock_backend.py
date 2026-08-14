"""In-memory VoyagePM backend — mirrors the real REST surface for offline demos."""

from __future__ import annotations

import math
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _haversine_nm(a: dict, b: dict) -> float:
    r = 3440.065  # Earth radius in nautical miles
    lat1, lon1 = math.radians(a["lat"]), math.radians(a["lon"])
    lat2, lon2 = math.radians(b["lat"]), math.radians(b["lon"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class MockBackend:
    """Simulates voyagepm_be dual-DB + VO + weather + alerts + storms."""

    def __init__(self):
        self.companies = {
            "orion": {"name": "orion", "domains": ["nova", "orionship"]},
            "jsw": {"name": "jsw", "domains": ["jsw", "jswsteel"]},
            "smartship": {"name": "smartship", "domains": ["smartshiphub", "gmail"]},
        }
        self.users = {
            "ops@smartshiphub.com": {
                "password": "demo",
                "role": "voyagepmoperator",
                "company": "smartship",
                "companies": ["orion", "jsw", "smartship"],
            },
            "fleet@orionship.com": {
                "password": "demo",
                "role": "Fleet Manager",
                "company": "orion",
                "companies": ["orion"],
            },
        }
        self.sessions: dict[str, dict] = {}
        self.vessels = {
            "orion": [
                {
                    "id": "v-nova-01",
                    "name": "MV Nova Star",
                    "imo": "9123456",
                    "lat": 1.25,
                    "lon": 103.85,
                    "speed": 12.4,
                },
                {
                    "id": "v-nova-02",
                    "name": "MV Pacific Trader",
                    "imo": "9234567",
                    "lat": 5.0,
                    "lon": 95.0,
                    "speed": 11.1,
                },
            ],
            "jsw": [
                {
                    "id": "v-jsw-01",
                    "name": "JSW Mariner",
                    "imo": "9345678",
                    "lat": 18.9,
                    "lon": 72.8,
                    "speed": 10.2,
                },
            ],
        }
        self.voyages: dict[str, list[dict]] = {"orion": [], "jsw": [], "smartship": []}
        self.routes: dict[str, dict] = {}  # voyage_id -> master/suggested/temp
        self.alerts: dict[str, list[dict]] = {}
        self.advisories: dict[str, list[dict]] = {}
        self.storms = [
            {
                "id": "JTWC-26W",
                "name": "Tropical Storm Mira",
                "lat": 12.5,
                "lon": 118.0,
                "wind_kn": 55,
                "category": "TS",
                "radius_nm": 70,
                "positions": [
                    {
                        "lat": 11.0,
                        "lon": 116.5,
                        "label": "Past",
                        "isPresent": False,
                        "trackPhase": "past",
                        "validAtIso": (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(),
                        "winds": 45,
                        "rad34Nm": {"ne": 80, "se": 60, "sw": 60, "nw": 70},
                    },
                    {
                        "lat": 12.5,
                        "lon": 118.0,
                        "label": "Current",
                        "isPresent": True,
                        "trackPhase": "live",
                        "validAtIso": datetime.now(timezone.utc).isoformat(),
                        "winds": 55,
                        "rad34Nm": {"ne": 100, "se": 80, "sw": 80, "nw": 90},
                    },
                    {
                        "lat": 14.0,
                        "lon": 119.5,
                        "label": "12H",
                        "isPresent": False,
                        "trackPhase": "forecast",
                        "validAtIso": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
                        "winds": 60,
                        "rad34Nm": {"ne": 110, "se": 90, "sw": 90, "nw": 100},
                    },
                    {
                        "lat": 15.5,
                        "lon": 121.0,
                        "label": "24H",
                        "isPresent": False,
                        "trackPhase": "forecast",
                        "validAtIso": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
                        "winds": 65,
                        "rad34Nm": {"ne": 120, "se": 100, "sw": 100, "nw": 110},
                    },
                ],
                "dangerCorridorRadiusNm": 70,
            }
        ]
        self.noon: dict[str, list[dict]] = {}
        self.weather_cache: dict[str, Any] = {}

    # ── Auth ──────────────────────────────────────────────
    def login(self, email: str, password: str) -> dict:
        user = self.users.get(email)
        if not user or user["password"] != password:
            raise ValueError("invalid credentials")
        token = str(uuid.uuid4())
        self.sessions[token] = {
            "email": email,
            "role": user["role"],
            "company": user["company"],
            "companies": user["companies"],
        }
        return {
            "token": token,
            "email": email,
            "role": user["role"],
            "company": user["company"],
            "companies": user["companies"],
        }

    def set_company(self, token: str, company: str) -> dict:
        sess = self._sess(token)
        if company not in sess["companies"]:
            raise ValueError(f"company {company} not allowed for user")
        sess["company"] = company
        return {"company": company}

    def identify_company(self, email: str) -> list[str]:
        domain = email.split("@")[-1].split(".")[0].lower() if "@" in email else ""
        hits = []
        for c, meta in self.companies.items():
            if domain in [d.lower() for d in meta["domains"]]:
                hits.append(c)
        user = self.users.get(email)
        if user:
            return user["companies"]
        return hits or ["orion"]

    # ── Fleet ─────────────────────────────────────────────
    def list_vessels(self, token: str) -> list[dict]:
        company = self._company(token)
        return deepcopy(self.vessels.get(company, []))

    def fleet_positions(self, token: str) -> list[dict]:
        return [
            {"id": v["id"], "name": v["name"], "lat": v["lat"], "lon": v["lon"], "speed": v["speed"]}
            for v in self.list_vessels(token)
        ]

    def get_vessel(self, token: str, vessel_id: str) -> dict:
        for v in self.list_vessels(token):
            if v["id"] == vessel_id:
                return deepcopy(v)
        raise ValueError(f"vessel not found: {vessel_id}")

    # ── Voyages ───────────────────────────────────────────
    def create_voyage(self, token: str, payload: dict) -> dict:
        company = self._company(token)
        voyage = {
            "id": f"voy-{uuid.uuid4().hex[:8]}",
            "voyageNumber": payload.get("voyageNumber") or f"VYG-{datetime.now().year}-{len(self.voyages[company])+1:03d}",
            "vesselId": payload["vesselId"],
            "departure": payload.get("departure", ""),
            "destination": payload.get("destination", ""),
            "etd": payload.get("etd", _now()),
            "route": payload.get("route", []),
            "status": "active",
            "createdAt": _now(),
        }
        self.voyages.setdefault(company, []).append(voyage)
        self.routes[voyage["id"]] = {"master": [], "suggested": [], "temp": []}
        self.alerts[voyage["id"]] = []
        self.advisories[voyage["id"]] = []
        self.noon[voyage["id"]] = []
        return deepcopy(voyage)

    def list_voyages(self, token: str, vessel_id: str | None = None) -> list[dict]:
        company = self._company(token)
        items = self.voyages.get(company, [])
        if vessel_id:
            items = [v for v in items if v["vesselId"] == vessel_id]
        return deepcopy(items)

    def get_voyage(self, token: str, voyage_id: str) -> dict:
        company = self._company(token)
        for v in self.voyages.get(company, []):
            if v["id"] == voyage_id:
                return deepcopy(v)
        raise ValueError(f"voyage not found: {voyage_id}")

    def save_route(self, token: str, voyage_id: str, kind: str, waypoints: list) -> dict:
        self.get_voyage(token, voyage_id)
        kind = kind if kind in ("master", "suggested", "temp") else "temp"
        self.routes.setdefault(voyage_id, {"master": [], "suggested": [], "temp": []})[kind] = deepcopy(waypoints)
        return {"voyageId": voyage_id, "kind": kind, "count": len(waypoints)}

    def get_routes(self, token: str, voyage_id: str) -> dict:
        self.get_voyage(token, voyage_id)
        return deepcopy(self.routes.get(voyage_id, {"master": [], "suggested": [], "temp": []}))

    # ── Routing / optimization ────────────────────────────
    def navapi_spine(self, token: str, start: dict, end: dict) -> dict:
        self._sess(token)
        mid = {
            "lat": (start["lat"] + end["lat"]) / 2,
            "lon": (start["lon"] + end["lon"]) / 2,
            "name": "spine-mid",
        }
        pts = [start, mid, end]
        return {"waypoints": pts, "distanceNm": round(_haversine_nm(start, end), 1), "provider": "NavAPI-mock"}

    def chartworld_route(self, token: str, waypoints: list) -> dict:
        self._sess(token)
        dist = 0.0
        for i in range(len(waypoints) - 1):
            dist += _haversine_nm(waypoints[i], waypoints[i + 1])
        return {"waypoints": waypoints, "distanceNm": round(dist, 1), "provider": "ChartWorld-mock"}

    def optimize_route(
        self,
        token: str,
        objective: str,
        waypoints: list,
        weather: dict | None = None,
        storms: list | None = None,
    ) -> dict:
        self._sess(token)
        base = deepcopy(waypoints) or [{"lat": 1.25, "lon": 103.85}, {"lat": 22.3, "lon": 114.2}]
        # ponytail: O(1) mock bend — swap for real Python VO subprocess when VPM_MODE=live
        bend = {"shortest": 0.0, "fuel": 0.4, "fastest": -0.2, "safest": 0.8, "lowest-fuel": 0.5}.get(objective, 0.3)
        # Stronger northward bias for safest when storms present so mock clearance can succeed
        storm_nudge = 1.2 if storms and objective == "safest" else (0.5 if storms else 0.0)
        from vpm_agents.tools.land_mask import nudge_off_land

        out = []
        last_i = len(base) - 1
        for i, p in enumerate(base):
            # Hard rule: origin/destination lat/lon never move
            if i == 0 or i == last_i:
                out.append({"lat": float(p["lat"]), "lon": float(p["lon"]), "seq": i})
                continue
            lat = p["lat"] + bend * 0.05 * (1 if i % 2 == 0 else -1)
            lon = p["lon"] + bend * 0.05 * (1 if i % 2 else -1)
            if storms:
                lat, lon = self._nudge_off_storms(lat, lon, storms, storm_nudge)
            # Hard rule for ships: mock bends must not leave the sea
            lat, lon = nudge_off_land(lat, lon)
            out.append({"lat": round(lat, 4), "lon": round(lon, 4), "seq": i})
        dist = sum(_haversine_nm(out[i], out[i + 1]) for i in range(len(out) - 1)) if len(out) > 1 else 0
        fuel = round(dist * (0.18 if "fuel" in objective or objective == "lowest-fuel" else 0.22), 1)
        eta_h = round(dist / (14.5 if objective == "fastest" else 12.0), 1)
        return {
            "objective": objective,
            "waypoints": out,
            "distanceNm": round(dist, 1),
            "fuelMt": fuel,
            "etaHours": eta_h,
            "weatherAware": bool(weather),
            "stormAware": bool(storms),
            "provider": f"vo-{objective}-mock",
        }

    def _nudge_off_storms(self, lat: float, lon: float, storms: list, strength: float) -> tuple[float, float]:
        """Push point away from nearest storm track point (mock avoidance)."""
        from vpm_agents.config import settings
        from vpm_agents.tools.storm_normalize import normalize_active_storms
        from vpm_agents.tools.storm_proximity import point_violates_storm

        storms_n = normalize_active_storms(storms)
        if not storms_n:
            return lat, lon
        # Iterate a few times to clear buffers
        for _ in range(4):
            worst = None
            for s in storms_n:
                for pos in s.get("positions") or [{"lat": s["lat"], "lon": s["lon"], "radius_nm": s.get("radius_nm")}]:
                    check = point_violates_storm(
                        lat,
                        lon,
                        float(pos["lat"]),
                        float(pos["lon"]),
                        float(pos.get("radius_nm") or s.get("radius_nm") or 0),
                    )
                    if check["violates"]:
                        need = max(
                            settings.storm_center_buffer_nm - check["distance_to_center_nm"],
                            settings.storm_edge_buffer_nm - check["distance_to_edge_nm"],
                            0,
                        )
                        if worst is None or need > worst[0]:
                            worst = (need, float(pos["lat"]), float(pos["lon"]))
            if not worst:
                break
            need, slat, slon = worst
            # Move roughly north/south away from storm (degrees ≈ NM/60)
            dlat = lat - slat
            dlon = lon - slon
            norm = math.hypot(dlat, dlon) or 1.0
            step_deg = (need + 50) / 60.0 * max(0.3, strength)
            lat = lat + (dlat / norm) * step_deg
            lon = lon + (dlon / norm) * step_deg
        return lat, lon


    def evaluate_route(self, token: str, waypoints: list) -> dict:
        opt = self.optimize_route(token, "evaluate", waypoints)
        return {"fuelMt": opt["fuelMt"], "etaHours": opt["etaHours"], "distanceNm": opt["distanceNm"]}

    # ── Weather ───────────────────────────────────────────
    def weather_along_route(self, token: str, waypoints: list) -> dict:
        self._sess(token)
        points = []
        hard = []
        for i, p in enumerate(waypoints):
            wind = 18 + (i * 7) % 40
            wave = 1.2 + (i % 5) * 0.7
            wind_dir = (90 + i * 23) % 360
            wp = {
                "lat": p["lat"],
                "lon": p["lon"],
                "windKn": wind,
                "windDirDeg": wind_dir,
                "pressureHpa": 1010 - (i % 8),
                "tempC": round(28.0 + (i % 4) * 0.8, 1),
                "waveM": round(wave, 1),
                "waveDirDeg": (wind_dir + 5) % 360,
                "swellM": round(wave * 0.8, 1),
                "swellDirDeg": (wind_dir + 15) % 360,
                "currentKn": round(((i % 5) - 2) * 0.3, 2),
                "currentDirDeg": (180 + i * 10) % 360,
                "validTime": (datetime.now(timezone.utc) + timedelta(hours=6 * i)).isoformat(),
            }
            points.append(wp)
            if wind >= 35 or wave >= 4.0:
                hard.append({"index": i, "reason": "wind" if wind >= 35 else "wave", "sample": wp})
        return {"points": points, "hardRegions": hard, "provider": "Spire/NOAA-mock"}

    def weather_point(self, token: str, lat: float, lon: float) -> dict:
        self._sess(token)
        return {"lat": lat, "lon": lon, "windKn": 22, "waveM": 2.1, "provider": "point-mock"}

    # ── Alerts / advisories ───────────────────────────────
    def configure_alerts(self, token: str, voyage_id: str, rules: list[dict]) -> list[dict]:
        self.get_voyage(token, voyage_id)
        created = []
        for r in rules:
            alert = {
                "id": f"al-{uuid.uuid4().hex[:6]}",
                "voyageId": voyage_id,
                "type": r.get("type", "ETA"),
                "threshold": r.get("threshold"),
                "active": True,
            }
            created.append(alert)
            self.alerts.setdefault(voyage_id, []).append(alert)
        return created

    def evaluate_alerts(self, token: str, voyage_id: str) -> list[dict]:
        self.get_voyage(token, voyage_id)
        issued = []
        for a in self.alerts.get(voyage_id, []):
            if a["type"] in ("GeoFence", "RecommendedRoute", "ETADeviation", "Weather"):
                issued.append(
                    {
                        "alertId": a["id"],
                        "type": a["type"],
                        "issuedAt": _now(),
                        "message": f"{a['type']} triggered for {voyage_id}",
                    }
                )
        return issued

    def create_advisory(self, token: str, voyage_id: str, text: str) -> dict:
        self.get_voyage(token, voyage_id)
        adv = {"id": f"adv-{uuid.uuid4().hex[:6]}", "voyageId": voyage_id, "text": text, "at": _now()}
        self.advisories.setdefault(voyage_id, []).append(adv)
        return adv

    # ── Storms / geofence ─────────────────────────────────
    def storm_registry(self, token: str) -> list[dict]:
        self._sess(token)
        return deepcopy(self.storms)

    def storm_map_layer(self, token: str) -> dict:
        """Active storms with progressions — mirrors GET /storm-pipeline/map-layer."""
        self._sess(token)
        storms = []
        for s in self.storms:
            storms.append(
                {
                    "stormId": s["id"],
                    "stormName": s.get("name"),
                    "displayName": s.get("name"),
                    "type": s.get("category"),
                    "status": "active",
                    "scrapedAt": _now(),
                    "positions": deepcopy(s.get("positions") or []),
                    "dangerCorridorRadiusNm": s.get("dangerCorridorRadiusNm") or s.get("radius_nm") or 55,
                }
            )
        return {"storms": storms, "fetchedAt": _now()}

    def run_storm_watcher(self, token: str) -> dict:
        self._sess(token)
        return {"ranAt": _now(), "storms": len(self.storms), "replicatedTenants": list(self.companies)}

    def geofence_check(self, token: str, voyage_id: str, position: dict) -> dict:
        self.get_voyage(token, voyage_id)
        hits = []
        for s in self.storms:
            d = _haversine_nm(position, s)
            if d < 500:
                hits.append({"storm": s["id"], "distanceNm": round(d, 1)})
        return {"voyageId": voyage_id, "hits": hits, "reoptimize": bool(hits)}

    def geofence_optimize(self, token: str, voyage_id: str, waypoints: list) -> dict:
        safe = self.optimize_route(token, "safest", waypoints)
        self.save_route(token, voyage_id, "suggested", safe["waypoints"])
        return {"voyageId": voyage_id, "route": safe, "published": "suggested"}

    # ── Reports ───────────────────────────────────────────
    def ingest_noon(self, token: str, voyage_id: str, report: dict) -> dict:
        self.get_voyage(token, voyage_id)
        row = {
            "at": report.get("at", _now()),
            "lat": report["lat"],
            "lon": report["lon"],
            "speed": report.get("speed", 12.0),
            "foMt": report.get("foMt", 18.5),
            "distanceNm": report.get("distanceNm", 280),
        }
        self.noon.setdefault(voyage_id, []).append(row)
        return row

    def compute_cii(self, token: str, voyage_id: str) -> dict:
        reports = self.noon.get(voyage_id, [])
        fuel = sum(r.get("foMt", 0) for r in reports) or 120.0
        dist = sum(r.get("distanceNm", 0) for r in reports) or 2400.0
        attained = round(fuel * 3.114 / max(dist, 1) * 1e6 / 50000, 2)  # toy formula
        return {
            "voyageId": voyage_id,
            "attained": attained,
            "required": 6.5,
            "rating": "B" if attained <= 6.5 else "C",
            "fuelMt": fuel,
            "distanceNm": dist,
        }

    def compute_eov(self, token: str, voyage_id: str) -> dict:
        cii = self.compute_cii(token, voyage_id)
        return {
            "voyageId": voyage_id,
            "generatedAt": _now(),
            "avgSpeed": 12.1,
            "totalFuelMt": cii["fuelMt"],
            "totalDistanceNm": cii["distanceNm"],
            "cii": cii,
            "savingsMt": round(cii["fuelMt"] * 0.04, 1),
        }

    def voyage_performance(self, token: str, voyage_id: str) -> dict:
        eov = self.compute_eov(token, voyage_id)
        return {
            "voyageId": voyage_id,
            "kpi": {
                "fuelIndex": 0.96,
                "etaVarianceHours": -2.5,
                "ciiRating": eov["cii"]["rating"],
                "savingsMt": eov["savingsMt"],
            },
        }

    # ── helpers ───────────────────────────────────────────
    def _sess(self, token: str) -> dict:
        if token not in self.sessions:
            raise ValueError("not authenticated")
        return self.sessions[token]

    def _company(self, token: str) -> str:
        return self._sess(token)["company"]
