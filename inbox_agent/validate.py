"""Pre-voyage sanity checks before registry / prevoyage_db ingest."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from vpm_agents.tools.geo import haversine_nm
from vpm_agents.tools.voyage_registry import compact_voyage_number, is_valid_voyage_number

# Max consecutive-leg jump (~real Pre-Dep routes can have 4000+ NM ocean legs).
_MAX_LEG_NM = 5000.0
# First / last waypoint must be near the port anchor named in the route.
_MAX_PORT_ENDPOINT_NM = 250.0

_SAMPLE_PORT_MARKERS = (
    "select from list",
    "dd-mm-yyyy",
    "sample format",
    "rizhao, china",
)
_CONDITIONS = frozenset({"ballast", "laden"})
_PORT_FMT = re.compile(r"^[^,]{2,},\s*[A-Za-z][A-Za-z\s.'-]{1,}$")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _port_tokens(port: str) -> list[str]:
    """'BSANTOS, BRAZIL' → ['bsantos', 'santos', 'brazil']."""
    raw = _norm(port).replace(".", " ")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: list[str] = []
    for p in parts:
        out.append(p)
        cleaned = re.sub(r"^[a-z]", "", p) if len(p) > 4 else p
        if cleaned and cleaned not in out:
            out.append(cleaned)
        for w in p.split():
            if len(w) >= 4 and w not in out:
                out.append(w)
    return out


def _parse_ts(raw: Any) -> datetime | None:
    from prevoyage_db.mapper import _parse_ts as _mapper_ts

    return _mapper_ts(raw)


def _port_anchor(
    port: str,
    waypoints: list[list[float]],
    wp_names: list[str],
) -> tuple[int, float, float] | None:
    tokens = _port_tokens(port)
    if not tokens:
        return None
    best: tuple[int, float, float, int] | None = None
    for i, name in enumerate(wp_names):
        nn = _norm(name)
        if not nn:
            continue
        score = max((len(t) for t in tokens if t in nn), default=0)
        if score >= 4 and (best is None or score > best[3]):
            best = (i, waypoints[i][0], waypoints[i][1], score)
    return (best[0], best[1], best[2]) if best else None


def _check_port_label(port: str, role: str) -> str | None:
    p = str(port or "").strip()
    if not p:
        return f"{role}: empty"
    pl = _norm(p)
    if any(m in pl for m in _SAMPLE_PORT_MARKERS):
        return f"{role}: looks like a template placeholder ({p!r}), not a real port"
    if not _PORT_FMT.match(p):
        return (
            f"{role}: expected 'Port name, COUNTRY' (e.g. 'Santos, BRAZIL') — got {p!r}"
        )
    return None


def validate_pre_voyage(record: dict[str, Any], *, now: datetime | None = None) -> list[str]:
    """Return human-readable reject reasons; empty list = OK to ingest."""
    issues: list[str] = []
    now = now or datetime.now(timezone.utc)

    voy = str(record.get("voyage_number") or "").strip()
    key = compact_voyage_number(voy) if voy else ""
    if not key:
        issues.append("Voyage Number: empty")
    elif not is_valid_voyage_number(voy):
        issues.append(
            f"Voyage Number: invalid format {voy!r} "
            "(expected V… with optional -L / -L1 / -B / -B2 suffix, or none)"
        )

    for role, field in (("Departure port", "source_port"), ("Destination port", "dest_port")):
        err = _check_port_label(str(record.get(field) or ""), role)
        if err:
            issues.append(err)

    src, dst = str(record.get("source_port") or "").strip(), str(record.get("dest_port") or "").strip()
    if src and dst and _norm(src) == _norm(dst):
        issues.append(f"Departure and destination port are the same ({src!r})")

    cond_raw = str(record.get("condition") or "").strip()
    cond = _norm(cond_raw)
    if not cond:
        issues.append("Condition (Ballast/Laden): empty — must be Ballast or Laden")
    elif not any(c in cond for c in _CONDITIONS):
        issues.append(
            f"Condition (Ballast/Laden): must be Ballast or Laden — got {cond_raw!r}"
        )
    elif _as_float(cond_raw) is not None:
        issues.append(
            f"Condition (Ballast/Laden): must be Ballast or Laden, not a number ({cond_raw!r})"
        )

    for title, field, lo, hi in (
        ("Displacement (MT)", "displacement", 1000.0, 500_000.0),
        ("Max draft on departure (m)", "max_draft_on_departure", 1.0, 30.0),
    ):
        val = record.get(field)
        if val is None:
            issues.append(f"{title}: missing or not a number")
            continue
        n = _as_float(val)
        if n is None:
            issues.append(f"{title}: must be a number — got {val!r}")
        elif not (lo <= n <= hi):
            issues.append(f"{title}: {n} out of expected range ({lo}–{hi})")

    cargo = record.get("cargo_weight")
    if cargo is not None:
        cn = _as_float(cargo)
        if cn is None:
            issues.append(f"Cargo weight (MT): must be a number — got {cargo!r}")
        elif cn < 0:
            issues.append(f"Cargo weight (MT): cannot be negative ({cn})")
    if cond and "laden" in cond:
        cn = _as_float(cargo)
        if cn is None or cn <= 0:
            issues.append("Cargo weight (MT): required > 0 when condition is Laden")

    etd_raw = record.get("etd")
    etd = _parse_ts(etd_raw)
    if not str(etd_raw or "").strip():
        issues.append("Estimated departure time: empty")
    elif etd is None:
        issues.append(
            f"Estimated departure time: unparseable ({etd_raw!r}) — "
            "need a valid date/time with timezone (e.g. 15-Sept-2026 16:00 -3)"
        )
    elif etd < now:
        issues.append(
            f"Estimated departure time: {etd_raw!r} is in the past "
            f"(parsed UTC {etd.strftime('%Y-%m-%d %H:%M')})"
        )

    eta_raw = record.get("eta")
    if str(eta_raw or "").strip():
        eta = _parse_ts(eta_raw)
        if eta is None:
            issues.append(
                f"Estimated arrival time: unparseable ({eta_raw!r}) — "
                "need a valid date/time with timezone"
            )
        elif etd is not None and eta <= etd:
            issues.append(
                f"Estimated arrival time ({eta_raw!r}) must be after departure ({etd_raw!r})"
            )

    wps: list[list[float]] = list(record.get("master_waypoints") or [])
    names: list[str] = list(record.get("waypoint_names") or [])
    if len(wps) < 2:
        issues.append("Waypoints: need at least 2 points")
        return issues

    for i in range(len(wps) - 1):
        d = haversine_nm(wps[i][0], wps[i][1], wps[i + 1][0], wps[i + 1][1])
        if d > _MAX_LEG_NM:
            a = names[i] if i < len(names) else f"WP{i + 1}"
            b = names[i + 1] if i + 1 < len(names) else f"WP{i + 2}"
            issues.append(
                f"Waypoints: leg {i + 1} ({a!r} → {b!r}) is {d:.0f} NM — "
                f"exceeds {_MAX_LEG_NM:.0f} NM (likely a bad jump / missing points)"
            )

    if src:
        dep = _port_anchor(src, wps, names)
        if dep is None:
            issues.append(
                f"Waypoints: no waypoint name matches departure port {src!r} "
                "(expected e.g. 'SANTOS P/S' near Santos)"
            )
        else:
            d0 = haversine_nm(wps[0][0], wps[0][1], dep[1], dep[2])
            if d0 > _MAX_PORT_ENDPOINT_NM:
                issues.append(
                    f"Waypoints: first point is {d0:.0f} NM from {src!r} "
                    f"(waypoint {names[dep[0]] if dep[0] < len(names) else dep[0] + 1!r}) — "
                    f"expected within {_MAX_PORT_ENDPOINT_NM:.0f} NM"
                )

    if dst:
        arr = _port_anchor(dst, wps, names)
        if arr is None:
            issues.append(
                f"Waypoints: no waypoint name matches destination port {dst!r} "
                "(expected e.g. 'CIGADING P/S' near Cigading)"
            )
        else:
            d1 = haversine_nm(wps[-1][0], wps[-1][1], arr[1], arr[2])
            if d1 > _MAX_PORT_ENDPOINT_NM:
                issues.append(
                    f"Waypoints: last point is {d1:.0f} NM from {dst!r} "
                    f"(waypoint {names[arr[0]] if arr[0] < len(names) else arr[0] + 1!r}) — "
                    f"expected within {_MAX_PORT_ENDPOINT_NM:.0f} NM"
                )

    speed = _as_float(record.get("cp_speed_kn"))
    if speed is None or speed <= 0 or speed > 25:
        issues.append(f"CP Speed: invalid ({record.get('cp_speed_kn')!r})")

    cons = record.get("cp_consumption_mt_day")
    if cons is not None:
        cn = _as_float(cons)
        if cn is None or cn <= 0 or cn > 100:
            issues.append(f"CP Consumption: invalid ({cons!r})")

    return issues


def _as_float(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "")
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", s):
        return float(s)
    return None


if __name__ == "__main__":
    ok = {
        "voyage_number": "V2610-02-L1",
        "source_port": "Testport, TEST",
        "dest_port": "Testport2, TEST",
        "condition": "Laden",
        "displacement": 84056,
        "cargo_weight": 67510,
        "max_draft_on_departure": 12.87,
        "etd": "15-JUN-2026 16:00 -3",
        "eta": "20-JUL-2026 08:00 +7",
        "cp_speed_kn": 11.0,
        "cp_consumption_mt_day": 21.43,
        "master_waypoints": [[1.0, 103.0], [1.05, 103.05], [1.1, 103.1]],
        "waypoint_names": ["TESTPORT P/S", "WP2", "TESTPORT2 P/S"],
    }
    assert not validate_pre_voyage(ok, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    bad = dict(ok, condition="12.87")
    assert any("Condition" in e for e in validate_pre_voyage(bad, now=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    assert _parse_ts("15-JUN-2026 16:00 -3") is not None
    assert _parse_ts("15-Sept-2026 16:00 -3") is not None
    print("validate self-check ok")
