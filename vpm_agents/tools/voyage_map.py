"""Voyage map image using the same basemap API as VoyagePM GUI (OSM.de tiles).

GUI (`smartshipweb.com/voyagepm`) renders Leaflet with:
  https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png
then screenshots via leaflet-simple-map-screenshoter.

Server-side we stitch those tiles and overlay the voyage track (no browser).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from vpm_agents.tools.agent_log import progress

# Same tile template the FE uses for "Normal Map"
_TILE_TMPL = "https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png"
_SUBS = ("a", "b", "c")
_UA = "VoyagePM-AgenticFramework/1.0 (EOV report map; contact voyage@smartshiphub.com)"

# Master + four objective colours (match GUI-style contrast on OSM.de)
_ALT_COLORS = (
    (230, 126, 34),
    (142, 68, 173),
    (39, 174, 96),
    (22, 160, 133),
)


@dataclass(frozen=True)
class _MapLine:
    points: list[tuple[float, float]]
    color: tuple[int, int, int]
    width: int
    label: str = ""


def _as_latlon(p: Any) -> tuple[float, float] | None:
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        return float(p[0]), float(p[1])
    if isinstance(p, dict) and p.get("lat") is not None and p.get("lon") is not None:
        return float(p["lat"]), float(p["lon"])
    return None


def _points_from_raw(raw: list[Any]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in raw:
        ll = _as_latlon(p)
        if ll:
            out.append(ll)
    return out


def _deg2num(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    lat_r = math.radians(lat)
    n = 2.0**zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return xtile, ytile


def _num2deg(xtile: float, ytile: float, zoom: int) -> tuple[float, float]:
    """NW corner of tile → (lat, lon)."""
    n = 2.0**zoom
    lon = xtile / n * 360.0 - 180.0
    lat_r = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    return math.degrees(lat_r), lon


def _pick_zoom(lats: list[float], lons: list[float], *, pad: float, max_tiles: int) -> int:
    """Smallest zoom (most zoomed-out) that keeps the bbox under ~max_tiles."""
    if not lats or not lons:
        return 4
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    lat_span = max(lat_max - lat_min, 0.5)
    lon_span = max(lon_max - lon_min, 0.5)
    lat_min -= lat_span * pad
    lat_max += lat_span * pad
    lon_min -= lon_span * pad
    lon_max += lon_span * pad
    for z in range(7, 1, -1):
        x0, y1 = _deg2num(lat_min, lon_min, z)
        x1, y0 = _deg2num(lat_max, lon_max, z)
        nx = abs(x1 - x0) + 1
        ny = abs(y1 - y0) + 1
        if nx * ny <= max_tiles:
            return z
    return 2


def _fetch_tile(client: httpx.Client, z: int, x: int, y: int, i: int) -> bytes | None:
    from PIL import Image

    url = _TILE_TMPL.format(s=_SUBS[i % len(_SUBS)], z=z, x=x, y=y)
    try:
        r = client.get(url, timeout=20.0)
        r.raise_for_status()
        Image.open(io.BytesIO(r.content)).verify()
        return r.content
    except Exception:
        return None


def _render_lines(
    lines: list[_MapLine],
    dest: Path,
    *,
    voyage_number: str = "",
    endpoint_labels: tuple[str, str] | None = None,
    title: str | None = None,
    pad: float = 0.25,
    max_tiles: int = 80,
) -> Path | None:
    all_pts = [p for ln in lines for p in ln.points]
    if len(all_pts) < 2:
        return None

    from PIL import Image, ImageDraw, ImageFont

    lats = [p[0] for p in all_pts]
    lons = [p[1] for p in all_pts]
    zoom = _pick_zoom(lats, lons, pad=pad, max_tiles=max_tiles)
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    lat_span = max(lat_max - lat_min, 0.5)
    lon_span = max(lon_max - lon_min, 0.5)
    lat_min -= lat_span * pad
    lat_max += lat_span * pad
    lon_min -= lon_span * pad
    lon_max += lon_span * pad

    x0, y_bottom = _deg2num(lat_min, lon_min, zoom)
    x1, y_top = _deg2num(lat_max, lon_max, zoom)
    x_min, x_max = min(x0, x1), max(x0, x1)
    y_min, y_max = min(y_top, y_bottom), max(y_top, y_bottom)

    tile = 256
    w = (x_max - x_min + 1) * tile
    h = (y_max - y_min + 1) * tile
    canvas = Image.new("RGB", (w, h), (220, 230, 240))

    with httpx.Client(headers={"User-Agent": _UA}, follow_redirects=True) as client:
        i = 0
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                raw = _fetch_tile(client, zoom, x, y, i)
                i += 1
                if not raw:
                    continue
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                canvas.paste(img, ((x - x_min) * tile, (y - y_min) * tile))

    nw_lat, nw_lon = _num2deg(x_min, y_min, zoom)
    se_lat, se_lon = _num2deg(x_max + 1, y_max + 1, zoom)

    def to_px(lat: float, lon: float) -> tuple[int, int]:
        px = int((lon - nw_lon) / (se_lon - nw_lon) * w) if se_lon != nw_lon else 0
        py = int((nw_lat - lat) / (nw_lat - se_lat) * h) if nw_lat != se_lat else 0
        return px, py

    draw = ImageDraw.Draw(canvas)
    endpoint_pts: list[tuple[float, float]] = []
    for ln in lines:
        px_line = [to_px(lat, lon) for lat, lon in ln.points]
        if len(px_line) >= 2:
            draw.line(px_line, fill=ln.color, width=ln.width)
        if ln.points:
            endpoint_pts.extend([ln.points[0], ln.points[-1]])

    dep = lines[0].points[0] if lines and lines[0].points else all_pts[0]
    arr = lines[0].points[-1] if lines and lines[0].points else all_pts[-1]
    d_px = to_px(*dep)
    a_px = to_px(*arr)
    r = 7
    draw.ellipse((d_px[0] - r, d_px[1] - r, d_px[0] + r, d_px[1] + r), fill=(34, 139, 34))
    draw.ellipse((a_px[0] - r, a_px[1] - r, a_px[0] + r, a_px[1] + r), fill=(200, 40, 40))

    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    dep_lbl, arr_lbl = endpoint_labels or ("Departure", "Arrival")
    head = title or f"Voyage routes{f' — {voyage_number}' if voyage_number else ''}"
    draw.rectangle((8, 8, min(w - 8, 12 + 7 * len(head)), 28), fill=(255, 255, 255))
    draw.text((12, 12), head, fill=(20, 20, 20), font=font)
    draw.text((d_px[0] + 10, d_px[1] - 10), dep_lbl[:40], fill=(20, 90, 20), font=font)
    draw.text((a_px[0] + 10, a_px[1] - 10), arr_lbl[:40], fill=(140, 20, 20), font=font)

    # Legend — master + alternatives
    legend_y = h - 8 - 14 * (len(lines) + 1)
    legend_y = max(36, legend_y)
    legend_h = 8 + 14 * len(lines)
    draw.rectangle((8, legend_y, min(w - 8, 320), legend_y + legend_h), fill=(255, 255, 255))
    for i, ln in enumerate(lines):
        y = legend_y + 6 + i * 14
        draw.line((16, y + 6, 36, y + 6), fill=ln.color, width=max(2, ln.width // 2))
        draw.text((42, y), ln.label[:36], fill=(20, 20, 20), font=font)

    # ponytail: cap huge mosaic size for email/PDF embed
    max_w = 2400
    if w > max_w:
        ratio = max_w / w
        canvas = canvas.resize((max_w, int(h * ratio)), Image.Resampling.LANCZOS)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, format="PNG")
    progress(
        "VoyageMap",
        f"{voyage_number or 'map'} OSM.de z={zoom} routes={len(lines)} "
        f"tiles={(x_max-x_min+1)*(y_max-y_min+1)} → {dest.name}",
    )
    return dest


def render_voyage_map(
    points: list[dict[str, Any]],
    dest: str | Path,
    *,
    voyage_number: str = "",
    labels: tuple[str, str] | None = None,
) -> Path | None:
    """Stitch OSM.de tiles + draw one route. Returns PNG path or None."""
    pts = _points_from_raw(points)
    if len(pts) < 2:
        return None
    return _render_lines(
        [_MapLine(pts, (11, 61, 145), 4, "Master route")],
        Path(dest),
        voyage_number=voyage_number,
        endpoint_labels=labels,
        pad=0.2,
        max_tiles=48,
    )


def render_routes_map(
    master: list[Any],
    alternatives: list[tuple[str, list[Any]]],
    dest: str | Path,
    *,
    voyage_number: str = "",
    labels: tuple[str, str] | None = None,
) -> Path | None:
    """Master + alternative routes on one OSM.de map (full voyage visible)."""
    lines: list[_MapLine] = []
    for i, (label, wps) in enumerate(alternatives):
        pts = _points_from_raw(wps)
        if len(pts) >= 2:
            color = _ALT_COLORS[i % len(_ALT_COLORS)]
            lines.append(_MapLine(pts, color, 3, label or f"Alt {i + 1}"))
    master_pts = _points_from_raw(master)
    if len(master_pts) >= 2:
        lines.append(_MapLine(master_pts, (11, 61, 145), 5, "Master route"))
    if len(lines) < 2:
        return render_voyage_map(
            [{"lat": lat, "lon": lon} for lat, lon in (master_pts or [])],
            dest,
            voyage_number=voyage_number,
            labels=labels,
        )
    return _render_lines(
        lines,
        Path(dest),
        voyage_number=voyage_number,
        endpoint_labels=labels,
        title=f"Pre-voyage routes{f' — {voyage_number}' if voyage_number else ''}",
        pad=0.25,
        max_tiles=80,
    )


if __name__ == "__main__":
    out = Path("/tmp/voyage_map_selfcheck.png")
    p = render_voyage_map(
        [
            {"lat": 13.1, "lon": 100.8},
            {"lat": 14.0, "lon": 105.0},
            {"lat": 18.0, "lon": 110.0},
            {"lat": 22.0, "lon": 113.5},
        ],
        out,
        voyage_number="SELFCHECK",
        labels=("Koh Sichang", "Guangzhou"),
    )
    assert p and p.is_file() and p.stat().st_size > 10_000

    alt_out = Path("/tmp/voyage_routes_selfcheck.png")
    p2 = render_routes_map(
        [
            {"lat": 13.1, "lon": 100.8},
            {"lat": 22.0, "lon": 113.5},
        ],
        [
            ("Fastest", [{"lat": 13.1, "lon": 100.8}, {"lat": 20.0, "lon": 108.0}, {"lat": 22.0, "lon": 113.5}]),
            ("Safest", [{"lat": 13.1, "lon": 100.8}, {"lat": 16.0, "lon": 112.0}, {"lat": 22.0, "lon": 113.5}]),
        ],
        alt_out,
        voyage_number="SELFCHECK",
    )
    assert p2 and p2.is_file() and p2.stat().st_size > 10_000
    print("voyage_map self-check ok", p.stat().st_size, p2.stat().st_size)
