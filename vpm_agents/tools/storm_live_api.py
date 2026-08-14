"""Live tropical cyclone feeds — NOAA NHC JSON + JTWC warning products.

Replaces mock/backend-only storm snapshots with the same public sources
voyagepm_be scrapes (JTWC) plus the official NHC CurrentStorms.json API.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx

from vpm_agents.config import settings

NHC_CURRENT_STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
JTWC_RSS_URL = "https://www.metoc.navy.mil/jtwc/rss/jtwc.rss"
JTWC_ORIGIN = "https://www.metoc.navy.mil"

_UA = "voyagepm-agentic/1.0 (storm watch; +https://github.com/voyagepm)"
_PRODUCT_RE = re.compile(
    r"(?:https?://[^\"'\s<>]+)?/jtwc/products/([a-z]{2})(\d{2})(\d{2})web\.txt", re.I
)
_POS_RE = re.compile(
    r"(?P<lat>\d{1,2}\.\d{1,2})\s*([°\s]*)?(?P<ns>[NS])\s*(?:/)?\s*"
    r"(?P<lon>\d{1,3}\.\d{1,2})\s*([°\s]*)?(?P<ew>[EW])",
    re.I,
)
_WARN_POS_LINE = re.compile(
    r"(\d{6})Z\s+---\s+(?:NEAR\s+)?(\d{1,2}\.\d{1,2}[NS])\s+(\d{1,3}\.\d{1,2}[EW])",
    re.I,
)
_HRS_RE = re.compile(r"^(\d{1,3})\s+HRS,\s+VALID\s+AT", re.I)
_WINDS_RE = re.compile(
    r"(?:MAX\s+SUSTAINED\s+WINDS|WINDS)\s*-\s*(\d{2,3})\s*KT(?:,\s*GUSTS\s*(\d{2,3})\s*KT)?",
    re.I,
)
_NAME_RE = re.compile(
    r"(?:TROPICAL\s+)?(?:CYCLONE|DEPRESSION|STORM|TYPHOON|SUPER\s+TYPHOON|HURRICANE)"
    r"\s+(\d{1,2}[A-Z])(?:\s+\(([^)]+)\))?",
    re.I,
)
_TCFA_CENTER_RE = re.compile(
    r"CIRCULATION\s+CENTER\s+IS\s+LOCATED\s+NEAR\s+(\d{1,2}\.\d{1,2}[NS])\s+(\d{1,3}\.\d{1,2}[EW])",
    re.I,
)
_TCFA_LINE_RE = re.compile(
    r"LINE\s+FROM\s+(\d{1,2}\.\d{1,2}[NS])\s+(\d{1,3}\.\d{1,2}[EW])\s+TO\s+"
    r"(\d{1,2}\.\d{1,2}[NS])\s+(\d{1,3}\.\d{1,2}[EW])",
    re.I,
)
_RAD34_HDR = re.compile(r"RADIUS\s+OF\s+0?34\s+KT\s+WINDS", re.I)
_NM_RE = re.compile(r"(\d{1,5})\s*NM", re.I)


def fetch_live_storms() -> list[dict[str, Any]]:
    """Fetch + normalize active storms from NHC JSON and JTWC warning texts."""
    storms: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        storms.extend(_fetch_nhc_storms())
    except Exception as e:
        errors.append(f"nhc: {e}")

    try:
        storms.extend(_fetch_jtwc_storms())
    except Exception as e:
        errors.append(f"jtwc: {e}")

    # Dedup by id (prefer entry with more progression points)
    by_id: dict[str, dict[str, Any]] = {}
    for s in storms:
        sid = str(s.get("id") or "").upper()
        if not sid:
            continue
        prev = by_id.get(sid)
        if not prev or len(s.get("positions") or []) > len(prev.get("positions") or []):
            by_id[sid] = s

    out = list(by_id.values())
    if errors and not out:
        raise RuntimeError("; ".join(errors))
    return out


def _http_get_text(url: str, timeout: float = 30.0) -> str:
    headers = {"User-Agent": _UA, "Accept": "*/*"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.text
    except Exception:
        # Fallback urllib for environments where httpx TLS path misbehaves
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed gov URLs
            return resp.read().decode("utf-8", "replace")


def _http_get_json(url: str) -> Any:
    text = _http_get_text(url)
    import json

    return json.loads(text)


def _parse_hemi(lat_s: str, lon_s: str) -> tuple[float, float] | None:
    m = _POS_RE.search(f"{lat_s} {lon_s}")
    if not m:
        return None
    lat = float(m.group("lat"))
    lon = float(m.group("lon"))
    if m.group("ns").upper() == "S":
        lat = -lat
    if m.group("ew").upper() == "W":
        lon = -lon
    return lat, lon


def _corridor_nm(wind_kn: float | None, rad34: dict | None = None) -> float:
    if rad34:
        vals = [float(rad34[k]) for k in ("ne", "se", "sw", "nw") if rad34.get(k) is not None]
        if vals:
            return max(vals)
    w = float(wind_kn or 0)
    if w >= 64:
        return 70.0
    if w >= 50:
        return 55.0
    return 40.0


def _valid_at_from_ddhhmm(token: str, now: datetime | None = None) -> str | None:
    """JTWC timeUtc like 111800Z → ISO (assume current month/year)."""
    if not token or len(token) < 7:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        dd = int(token[0:2])
        hh = int(token[2:4])
        mm = int(token[4:6])
        year, month = now.year, now.month
        # month wrap if day far from today
        if dd > now.day + 20:
            month -= 1
            if month < 1:
                month = 12
                year -= 1
        elif dd < now.day - 20:
            month += 1
            if month > 12:
                month = 1
                year += 1
        dt = datetime(year, month, dd, hh, mm, tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


# ── NHC ─────────────────────────────────────────────────────


def _fetch_nhc_storms() -> list[dict[str, Any]]:
    data = _http_get_json(NHC_CURRENT_STORMS_URL)
    active = data.get("activeStorms") or []
    out: list[dict[str, Any]] = []
    for s in active:
        if not isinstance(s, dict):
            continue
        entry = _nhc_to_storm(s)
        if entry:
            out.append(entry)
    return out


def _nhc_to_storm(s: dict[str, Any]) -> dict[str, Any] | None:
    lat = s.get("latitudeNumeric")
    lon = s.get("longitudeNumeric")
    if lat is None:
        lat = s.get("latitude_numeric")
    if lon is None:
        lon = s.get("longitude_numeric")
    if lat is None or lon is None:
        return None

    try:
        wind = float(s.get("intensity") or 0)
    except (TypeError, ValueError):
        wind = 0.0

    sid = str(s.get("id") or "").upper()
    name = s.get("name") or sid
    last = s.get("lastUpdate")
    radius = _corridor_nm(wind)

    positions: list[dict[str, Any]] = [
        {
            "lat": float(lat),
            "lon": float(lon),
            "radius_nm": radius,
            "valid_time": last,
            "validAtIso": last,
            "isPresent": True,
            "trackPhase": "live",
            "winds": wind,
            "label": "Current",
        }
    ]

    # Progression from forecast advisory text when available
    adv = s.get("forecastAdvisory") or {}
    url = adv.get("url")
    if url:
        try:
            text = _http_get_text(url)
            positions.extend(_parse_nhc_forecast_advisory(text, radius))
        except Exception:
            pass

    return {
        "id": sid,
        "name": name,
        "lat": float(lat),
        "lon": float(lon),
        "radius_nm": radius,
        "dangerCorridorRadiusNm": radius,
        "wind_kn": wind,
        "category": s.get("classification"),
        "status": "active",
        "source": "nhc",
        "scrapedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "positions": positions,
        "publicAdvisoryUrl": (s.get("publicAdvisory") or {}).get("url"),
        "forecastAdvisoryUrl": url,
    }


def _parse_nhc_forecast_advisory(text: str, default_radius: float) -> list[dict[str, Any]]:
    """Pull FORECAST VALID … lines from NHC TCM / advisory HTML or text."""
    # Strip tags lightly
    plain = re.sub(r"<[^>]+>", "\n", text)
    positions: list[dict[str, Any]] = []
    # FORECAST VALID 22/1200Z 17.5N  59.5W
    for m in re.finditer(
        r"FORECAST\s+VALID\s+(\d{2}/\d{4}Z)\s+(\d{1,2}\.\d)([NS])\s+(\d{1,3}\.\d)([EW])",
        plain,
        re.I,
    ):
        lat = float(m.group(2)) * (1 if m.group(3).upper() == "N" else -1)
        lon = float(m.group(4)) * (1 if m.group(5).upper() == "E" else -1)
        positions.append(
            {
                "lat": lat,
                "lon": lon,
                "radius_nm": default_radius,
                "valid_time": m.group(1),
                "isPresent": False,
                "trackPhase": "forecast",
                "label": m.group(1),
            }
        )
    return positions


# ── JTWC ────────────────────────────────────────────────────


def _fetch_jtwc_storms() -> list[dict[str, Any]]:
    xml = _http_get_text(JTWC_RSS_URL)
    urls = _jtwc_warning_urls(xml)
    out: list[dict[str, Any]] = []
    for url in urls:
        try:
            text = _http_get_text(url)
        except (HTTPError, URLError, httpx.HTTPError, OSError):
            continue
        entry = _jtwc_text_to_storm(text, url)
        if entry:
            out.append(entry)
    return out


def _jtwc_warning_urls(rss_xml: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for m in _PRODUCT_RE.finditer(rss_xml):
        basin, num, yy = m.group(1).lower(), m.group(2), m.group(3)
        key = f"{basin}{num}{yy}"
        if key in seen:
            continue
        seen.add(key)
        urls.append(f"{JTWC_ORIGIN}/jtwc/products/{basin}{num}{yy}web.txt")
    return urls


def _jtwc_text_to_storm(text: str, url: str) -> dict[str, Any] | None:
    upper = text.upper()
    is_tcfa = "FORMATION ALERT" in upper or "TROPICAL CYCLONE FORMATION ALERT" in upper

    name_m = _NAME_RE.search(text)
    storm_token = name_m.group(1).upper() if name_m else None
    storm_name = (name_m.group(2) or storm_token or "").strip() if name_m else ""
    if not storm_name:
        inv = re.search(r"\bINVEST\s+(\d{2}[A-Z])\b", text, re.I)
        subj = re.search(r"SUBJ/([^/\n]+)", text, re.I)
        if inv:
            storm_token = storm_token or inv.group(1).upper()
            storm_name = f"Invest {inv.group(1).upper()}"
        elif subj:
            storm_name = subj.group(1).strip()[:80]
        else:
            storm_name = storm_token or "Unknown"

    # id from URL wp1426 → WP142026
    pm = re.search(r"/products/([a-z]{2})(\d{2})(\d{2})web\.txt", url, re.I)
    if pm:
        sid = f"{pm.group(1).upper()}{pm.group(2)}20{pm.group(3)}"
    elif storm_token:
        sid = storm_token
    else:
        sid = url.rsplit("/", 1)[-1]

    if is_tcfa:
        return _jtwc_tcfa_storm(text, sid, storm_name, url)

    rows = _parse_jtwc_forecast_table(text)
    if not rows:
        return None

    positions: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        coords = row.get("coords")
        if not coords:
            continue
        wind = row.get("winds")
        rad34 = row.get("rad34Nm")
        radius = _corridor_nm(wind, rad34)
        is_present = i == 0
        positions.append(
            {
                "lat": coords[0],
                "lon": coords[1],
                "radius_nm": radius,
                "valid_time": row.get("timeUtc"),
                "validAtIso": _valid_at_from_ddhhmm(row.get("timeUtc") or ""),
                "isPresent": is_present,
                "trackPhase": "live" if is_present else "forecast",
                "winds": wind,
                "gusts": row.get("gusts"),
                "rad34Nm": rad34,
                "label": row.get("time") or ("Current" if is_present else "Forecast"),
            }
        )

    if not positions:
        return None

    present = positions[0]
    return {
        "id": sid,
        "name": storm_name if storm_name != "Unknown" else sid,
        "lat": present["lat"],
        "lon": present["lon"],
        "radius_nm": present["radius_nm"],
        "dangerCorridorRadiusNm": present["radius_nm"],
        "wind_kn": present.get("winds"),
        "category": "TCFA" if is_tcfa else _classify_wind(present.get("winds")),
        "status": "active",
        "source": "jtwc",
        "warningTextUrl": url,
        "scrapedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "positions": positions,
    }


def _jtwc_tcfa_storm(text: str, sid: str, name: str, url: str) -> dict[str, Any] | None:
    m = _TCFA_CENTER_RE.search(text)
    if m:
        coords = _parse_hemi(m.group(1), m.group(2))
    else:
        m2 = _TCFA_LINE_RE.search(text)
        if not m2:
            return None
        a = _parse_hemi(m2.group(1), m2.group(2))
        b = _parse_hemi(m2.group(3), m2.group(4))
        if not a or not b:
            return None
        coords = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    if not coords:
        return None
    wind_m = re.search(r"(\d{2})\s+TO\s+(\d{2})\s+KNOTS", text, re.I)
    wind = float(wind_m.group(2)) if wind_m else 25.0
    radius = _corridor_nm(wind)
    lat, lon = coords
    pos = {
        "lat": lat,
        "lon": lon,
        "radius_nm": radius,
        "isPresent": True,
        "trackPhase": "live",
        "winds": wind,
        "label": "Current",
        "validAtIso": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return {
        "id": sid,
        "name": name or sid,
        "lat": lat,
        "lon": lon,
        "radius_nm": radius,
        "dangerCorridorRadiusNm": radius,
        "wind_kn": wind,
        "category": "TCFA",
        "status": "formation_alert",
        "source": "jtwc",
        "warningTextUrl": url,
        "scrapedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "positions": [pos],
    }


def _parse_jtwc_forecast_table(text: str) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def push() -> None:
        nonlocal current
        if current and current.get("coords"):
            rows.append(current)
        current = None

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        if "WARNING POSITION:" in line.upper():
            push()
            current = {"time": "Current"}
            i += 1
            continue
        hm = _HRS_RE.match(line)
        if hm:
            push()
            current = {"time": f"{hm.group(1)} HRS"}
            i += 1
            continue
        if current is None:
            i += 1
            continue

        tp = _WARN_POS_LINE.search(line)
        if tp and "coords" not in current:
            current["timeUtc"] = tp.group(1) if tp.group(1).upper().endswith("Z") else f"{tp.group(1)}Z"
            coords = _parse_hemi(tp.group(2), tp.group(3))
            if coords:
                current["coords"] = coords

        wm = _WINDS_RE.search(line)
        if wm:
            current["winds"] = float(wm.group(1))
            if wm.group(2):
                current["gusts"] = float(wm.group(2))

        if _RAD34_HDR.search(line):
            quads = []
            for j in range(4):
                if i + j < len(lines):
                    nm = _NM_RE.search(lines[i + j])
                    quads.append(float(nm.group(1)) if nm else 0.0)
            if len(quads) == 4:
                current["rad34Nm"] = {"ne": quads[0], "se": quads[1], "sw": quads[2], "nw": quads[3]}
        i += 1

    push()
    return rows


def _classify_wind(wind: float | None) -> str:
    w = float(wind or 0)
    if w >= 64:
        return "TY"
    if w >= 34:
        return "TS"
    return "TD"


def storm_source_mode() -> str:
    return (settings.storm_source or "live").strip().lower()
