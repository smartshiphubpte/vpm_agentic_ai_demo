"""Normalize master / waypoint / track+weather JSON to [[lat, lon], ...]."""

from __future__ import annotations

from typing import Any


def parse_route_points(data: Any) -> list[list[float]]:
    """Accept master [[lat,lon],…], plan [{lat,lon,…},…], {track:[…]}, or weather {points:[…]}."""
    if isinstance(data, dict):
        if "track" in data:
            data = data["track"]
        elif "waypoints" in data:
            data = data["waypoints"]
        elif "master_waypoints" in data:
            data = data["master_waypoints"]
        elif "points" in data:
            data = data["points"]
        else:
            raise ValueError("route object needs track, waypoints, points, or master_waypoints")

    if not isinstance(data, list) or not data:
        raise ValueError("route must be a non-empty list")

    first = data[0]
    if isinstance(first, (list, tuple)) and len(first) >= 2:
        return [[float(p[0]), float(p[1])] for p in data]
    if isinstance(first, dict) and "lat" in first and "lon" in first:
        return [[float(p["lat"]), float(p["lon"])] for p in data]
    raise ValueError("unsupported route point format")
