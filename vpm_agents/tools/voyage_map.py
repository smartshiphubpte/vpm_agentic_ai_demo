"""Voyage map image using the same basemap API as VoyagePM GUI (OSM.de tiles).

GUI (`smartshipweb.com/voyagepm`) renders Leaflet with:
  https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png
then screenshots via leaflet-simple-map-screenshoter.

Server-side we stitch those tiles and overlay the voyage track (no browser).
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import httpx

from vpm_agents.tools.agent_log import progress

# Same tile template the FE uses for "Normal Map"
_TILE_TMPL = "https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png"
_SUBS = ("a", "b", "c")
_UA = "VoyagePM-AgenticFramework/1.0 (EOV report map; contact voyage@smartshiphub.com)"


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


def _pick_zoom(lats: list[float], lons: list[float], *, max_tiles: int = 48) -> int:
    """Largest zoom that keeps the bbox under ~max_tiles tiles."""
    pad = 0.15
    lat_min, lat_max = min(lats) - pad, max(lats) + pad
    lon_min, lon_max = min(lons) - pad, max(lons) + pad
    for z in range(8, 3, -1):
        x0, y1 = _deg2num(lat_min, lon_min, z)
        x1, y0 = _deg2num(lat_max, lon_max, z)
        nx = abs(x1 - x0) + 1
        ny = abs(y1 - y0) + 1
        if nx * ny <= max_tiles:
            return z
    return 4


def _fetch_tile(client: httpx.Client, z: int, x: int, y: int, i: int) -> bytes | None:
    from PIL import Image

    url = _TILE_TMPL.format(s=_SUBS[i % len(_SUBS)], z=z, x=x, y=y)
    try:
        r = client.get(url, timeout=20.0)
        r.raise_for_status()
        # validate it's an image
        Image.open(io.BytesIO(r.content)).verify()
        return r.content
    except Exception:
        return None


def render_voyage_map(
    points: list[dict[str, Any]],
    dest: str | Path,
    *,
    voyage_number: str = "",
    labels: tuple[str, str] | None = None,
) -> Path | None:
    """Stitch OSM.de tiles + draw route. Returns PNG path or None."""
    pts = [
        (float(p["lat"]), float(p["lon"]))
        for p in points
        if p.get("lat") is not None and p.get("lon") is not None
    ]
    if len(pts) < 2:
        return None

    from PIL import Image, ImageDraw, ImageFont

    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    zoom = _pick_zoom(lats, lons)
    pad = 0.15
    lat_min, lat_max = min(lats) - pad, max(lats) + pad
    lon_min, lon_max = min(lons) - pad, max(lons) + pad

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

    # NW corner of mosaic in lat/lon
    nw_lat, nw_lon = _num2deg(x_min, y_min, zoom)
    se_lat, se_lon = _num2deg(x_max + 1, y_max + 1, zoom)

    def to_px(lat: float, lon: float) -> tuple[int, int]:
        # linear in web-mercator tile space ≈ fine at voyage scale
        px = int((lon - nw_lon) / (se_lon - nw_lon) * w) if se_lon != nw_lon else 0
        # lat decreases downward in image
        py = int((nw_lat - lat) / (nw_lat - se_lat) * h) if nw_lat != se_lat else 0
        return px, py

    draw = ImageDraw.Draw(canvas)
    line = [to_px(lat, lon) for lat, lon in pts]
    if len(line) >= 2:
        draw.line(line, fill=(11, 61, 145), width=4)
    # departure / arrival
    d_px = to_px(*pts[0])
    a_px = to_px(*pts[-1])
    r = 7
    draw.ellipse((d_px[0] - r, d_px[1] - r, d_px[0] + r, d_px[1] + r), fill=(34, 139, 34))
    draw.ellipse((a_px[0] - r, a_px[1] - r, a_px[0] + r, a_px[1] + r), fill=(200, 40, 40))

    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    dep_lbl, arr_lbl = labels or ("Departure", "Arrival")
    title = f"Voyage track{f' — {voyage_number}' if voyage_number else ''}"
    draw.rectangle((8, 8, min(w - 8, 12 + 7 * len(title)), 28), fill=(255, 255, 255))
    draw.text((12, 12), title, fill=(20, 20, 20), font=font)
    draw.text((d_px[0] + 10, d_px[1] - 10), dep_lbl[:40], fill=(20, 90, 20), font=font)
    draw.text((a_px[0] + 10, a_px[1] - 10), arr_lbl[:40], fill=(140, 20, 20), font=font)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, format="PNG")
    progress(
        "VoyageMap",
        f"{voyage_number or 'map'} OSM.de z={zoom} tiles={(x_max-x_min+1)*(y_max-y_min+1)} → {dest.name}",
    )
    return dest


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
    print("voyage_map self-check ok", p, p.stat().st_size)
