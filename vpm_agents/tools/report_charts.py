"""Time-series charts for templated reports (matplotlib Agg). Missing lib → skip."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _mpl():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _ys(rows: list[dict[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r.get(key)
        out.append(float(v) if v is not None else float("nan"))
    return out


def _has_data(ys: list[float]) -> bool:
    return any(y == y for y in ys)  # not NaN


def _short_t(raw: Any) -> str:
    s = str(raw or "")
    if "T" in s:
        return s.replace("T", " ")[5:16]
    return s[:16]


def _line_chart(
    path: Path,
    title: str,
    labels: list[str],
    left: tuple[str, list[float], str],
    right: tuple[str, list[float], str] | None = None,
) -> Path | None:
    if not _has_data(left[1]) and not (right and _has_data(right[1])):
        return None
    try:
        plt = _mpl()
    except ImportError:
        return None
    xs = list(range(len(labels)))
    fig, ax = plt.subplots(figsize=(10.5, 3.4))
    ax.plot(xs, left[1], "o-", color="#1f4e79", label=left[0], markersize=3)
    ax.set_ylabel(left[2])
    ax.set_title(title)
    if right and _has_data(right[1]):
        ax2 = ax.twinx()
        ax2.plot(xs, right[1], "s--", color="#c45c26", label=right[0], markersize=3)
        ax2.set_ylabel(right[2])
        lines, labs = ax.get_legend_handles_labels()
        l2, lab2 = ax2.get_legend_handles_labels()
        ax.legend(lines + l2, labs + lab2, loc="upper right")
    else:
        ax.legend(loc="upper right")
    step = max(1, len(xs) // 10)
    ax.set_xticks(xs[::step], [labels[i] for i in xs[::step]], rotation=30, ha="right")
    ax.grid(True, alpha=0.3)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _bar_chart(path: Path, title: str, labels: list[str], values: list[float], ylabel: str) -> Path | None:
    if not values or not any(v == v for v in values):
        return None
    try:
        plt = _mpl()
    except ImportError:
        return None
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.bar(labels, values, color="#2c5f8a")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def weather_series_charts(out_dir: Path, rows: list[dict[str, Any]], *, stem: str) -> list[Path]:
    """Wind BF / wave-swell / pressure-current charts from passage or port weather rows."""
    if len(rows) < 2:
        return []
    labels = [_short_t(r.get("date_utc")) for r in rows]
    out_dir = Path(out_dir)
    paths: list[Path] = []
    for fn, title, left, right in (
        (
            f"{stem}_wind_bf.png",
            "Wind speed and Beaufort force",
            ("Wind", _ys(rows, "wind_kn"), "kn"),
            ("Beaufort", _ys(rows, "beaufort"), "BF"),
        ),
        (
            f"{stem}_wave_swell.png",
            "Significant wave and swell",
            ("Wave", _ys(rows, "wave_m"), "m"),
            ("Swell", _ys(rows, "swell_m"), "m"),
        ),
        (
            f"{stem}_pressure_current.png",
            "Pressure and current factor",
            ("Pressure", _ys(rows, "pressure_hpa"), "hPa"),
            ("Current factor", _ys(rows, "current_factor_kn"), "kn"),
        ),
    ):
        p = _line_chart(out_dir / fn, title, labels, left, right)
        if p:
            paths.append(p)
    return paths


def cyclone_charts(
    out_dir: Path,
    *,
    wind_kn: float | None,
    wave_m: float | None,
    swell_m: float | None,
    proximity_labels: list[str],
    proximity_nm: list[float],
    stem: str = "cyclone",
) -> list[Path]:
    """Bar of sea state at nearest fix + CPA trend vs system progressions."""
    out_dir = Path(out_dir)
    paths: list[Path] = []
    bars = [("Wind kn", wind_kn), ("Wave m", wave_m), ("Swell m", swell_m)]
    labels = [a for a, v in bars if v is not None]
    values = [float(v) for _, v in bars if v is not None]
    p = _bar_chart(out_dir / f"{stem}_seastate.png", "Wind / sea at nearest track point", labels, values, "value")
    if p:
        paths.append(p)
    if len(proximity_nm) >= 2:
        p2 = _line_chart(
            out_dir / f"{stem}_proximity.png",
            "Vessel proximity to system center",
            proximity_labels or [str(i) for i in range(len(proximity_nm))],
            ("CPA", proximity_nm, "NM"),
        )
        if p2:
            paths.append(p2)
    return paths


if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    rows = [
        {
            "date_utc": "2026-08-01T00:00:00Z",
            "wind_kn": 12,
            "beaufort": 4,
            "wave_m": 0.4,
            "swell_m": 0.2,
            "pressure_hpa": 1012,
            "current_factor_kn": 0.1,
        },
        {
            "date_utc": "2026-08-01T12:00:00Z",
            "wind_kn": 40,
            "beaufort": 8,
            "wave_m": 2.5,
            "swell_m": 1.8,
            "pressure_hpa": 1004,
            "current_factor_kn": -0.2,
        },
    ]
    with TemporaryDirectory() as d:
        charts = weather_series_charts(Path(d), rows, stem="selfcheck")
        assert len(charts) == 3, charts
        cyc = cyclone_charts(
            Path(d),
            wind_kn=35,
            wave_m=2.0,
            swell_m=1.5,
            proximity_labels=["t0", "t1", "t2"],
            proximity_nm=[120.0, 80.0, 55.0],
            stem="selfcheck",
        )
        assert len(cyc) == 2, cyc
    print("report_charts self-check ok")
