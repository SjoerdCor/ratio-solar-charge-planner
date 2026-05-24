"""
solar_forecast.py
=================
Fetches hourly solar production forecasts via the Forecast.Solar API
and combines multiple roof planes into a single total forecast.

Forecast.Solar azimuth convention: 0=south, negative=east, positive=west.
  South-east: az=-45   North-east: az=-135

Free API tier: max 12 requests/hour — results are cached to disk.
TODO: add OpenMeteo fallback for days 2-7.

Call configure() before using any other function in this module.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List

import requests

log = logging.getLogger(__name__)

FORECAST_SOLAR_BASE = "https://api.forecast.solar/estimate"

_state = SimpleNamespace(
    latitude=0.0,
    longitude=0.0,
    roof_planes=[],
    cache_dir=Path("data/cache"),
    max_age=timedelta(minutes=60),
)


def configure(
    latitude: float,
    longitude: float,
    roof_planes: List[dict],
    cache_dir,
    max_age_minutes: int = 60,
) -> None:
    """Set location, roof planes and cache settings. Must be called before any fetch."""
    _state.latitude = latitude
    _state.longitude = longitude
    _state.roof_planes = roof_planes
    _state.cache_dir = Path(cache_dir)
    _state.max_age = timedelta(minutes=max_age_minutes)


def _fetch_api(power_kw: float, tilt: int, azimuth: int) -> dict:
    """Fetch raw API response for one roof plane, using disk cache when fresh."""
    cache_file = _state.cache_dir / f"forecast_{tilt}_{azimuth}_{power_kw}.json"
    if cache_file.exists():
        stored = json.loads(cache_file.read_text(encoding="utf-8"))
        age = datetime.now() - datetime.fromisoformat(stored["opgeslagen_op"])
        if age < _state.max_age:
            return stored["result"]

    url = (
        f"{FORECAST_SOLAR_BASE}"
        f"/{_state.latitude}/{_state.longitude}/{tilt}/{azimuth}/{power_kw}"
    )
    log.info("Fetching forecast: %s", url)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    result = resp.json()["result"]

    cache_file.write_text(
        json.dumps({"opgeslagen_op": datetime.now().isoformat(), "result": result}),
        encoding="utf-8",
    )
    return result


def fetch_roof_plane_forecast(
    power_kw: float, tilt: int, azimuth: int
) -> Dict[str, float]:
    """
    Return per-period energy production for one roof plane (kWh per period).

    Uses watt_hours_period (not watt_hours, which is cumulative).
    """
    raw = _fetch_api(power_kw, tilt, azimuth)["watt_hours_period"]
    return {ts: wh / 1000 for ts, wh in raw.items()}


def fetch_forecast() -> Dict[str, float]:
    """Combine all roof planes into one total forecast (kWh per period)."""
    total: Dict[str, float] = {}
    for plane in _state.roof_planes:
        plane_forecast = fetch_roof_plane_forecast(
            plane["kwp"], plane["tilt"], plane["azimuth"]
        )
        for ts, kwh in plane_forecast.items():
            total[ts] = total.get(ts, 0.0) + kwh
    return total


def power_at(timestamp: datetime) -> float:
    """
    Return expected solar power in kW at a given timestamp.
    Linearly interpolates between the two nearest hourly averages.
    """
    total_kw: Dict[str, float] = {}
    for plane in _state.roof_planes:
        plane_watts = _fetch_api(plane["kwp"], plane["tilt"], plane["azimuth"])["watts"]
        for ts, w in plane_watts.items():
            total_kw[ts] = total_kw.get(ts, 0.0) + w / 1000

    keys = sorted(total_kw)
    ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

    before = None
    after = None
    for k in keys:
        if k <= ts_str:
            before = k
        elif after is None:
            after = k
            break

    if before is None:
        return 0.0
    if after is None:
        return total_kw[before]

    t0 = datetime.strptime(before, "%Y-%m-%d %H:%M:%S")
    t1 = datetime.strptime(after, "%Y-%m-%d %H:%M:%S")
    frac = (timestamp - t0).total_seconds() / (t1 - t0).total_seconds()
    return total_kw[before] + frac * (total_kw[after] - total_kw[before])
