"""
solar_forecast.py
=================
Fetches hourly solar production forecasts and combines them into a single
7-day total per roof plane.

Two sources are merged:
  - OpenMeteo (days 1–7): rough estimate from irradiance + panel geometry.
  - Forecast.Solar (days 1–2): accurate per-plane forecast; overwrites OpenMeteo.

Forecast.Solar azimuth convention: 0=south, negative=east, positive=west.
  South-east: az=-45   North-east: az=-135

Free Forecast.Solar tier: max 12 requests/hour — results are cached to disk.

Call configure() before using any other function in this module.
"""

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List

import requests

log = logging.getLogger(__name__)

FORECAST_SOLAR_BASE = "https://api.forecast.solar/estimate"
OPENMETEO_BASE = "https://api.open-meteo.com/v1/forecast"
_SYSTEM_EFFICIENCY = 0.80  # inverter + wiring + temperature losses
_GROUND_ALBEDO = 0.20
# Empirical correction for the simplified geometric model's systematic overestimation.
# Measured ~54% overestimation vs Forecast.Solar on 2026-05-30; 0.70 brings it close.
# Revisit after tracking for a week — see data/forecast_comparison.csv.
_OPENMETEO_HAIRCUT = 0.70


class SolarForecastError(Exception):
    """Raised when a solar forecast cannot be fetched or parsed."""

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
    _state.cache_dir.mkdir(parents=True, exist_ok=True)
    _state.max_age = timedelta(minutes=max_age_minutes)
    log.info(
        "Configured: %.4f°N %.4f°E, %d roof plane(s), cache=%s (max age %d min)",
        latitude, longitude, len(roof_planes), _state.cache_dir, max_age_minutes,
    )


# ---------------------------------------------------------------------------
# Solar geometry helpers
# ---------------------------------------------------------------------------

def _solar_declination(dt: datetime) -> float:
    """Solar declination in radians (Spencer 1971 approximation)."""
    day_angle = 2 * math.pi * (dt.timetuple().tm_yday - 1) / 365
    return (0.006918 - 0.399912 * math.cos(day_angle) + 0.070257 * math.sin(day_angle)
            - 0.006758 * math.cos(2 * day_angle) + 0.000907 * math.sin(2 * day_angle)
            - 0.002697 * math.cos(3 * day_angle) + 0.00148 * math.sin(3 * day_angle))


def _solar_hour_angle(dt: datetime, lon_deg: float) -> float:
    """Hour angle in radians. dt must be in UTC; lon_deg is the site longitude."""
    solar_time = dt.hour + dt.minute / 60 + lon_deg / 15.0
    return math.radians(15.0 * (solar_time - 12.0))


def _cos_aoi(lat_rad: float, decl: float, omega: float, tilt: float, panel_az: float) -> float:
    """Cosine of angle of incidence on a tilted surface (all angles in radians).

    Duffie & Beckman eq. 1.6.2. panel_az: 0=south, negative=east, positive=west.
    Returns 0 when the sun is behind the panel.
    """
    return max(0.0, (
        math.sin(decl) * math.sin(lat_rad) * math.cos(tilt)
        - math.sin(decl) * math.cos(lat_rad) * math.sin(tilt) * math.cos(panel_az)
        + math.cos(decl) * math.cos(lat_rad) * math.cos(tilt) * math.cos(omega)
        + math.cos(decl) * math.sin(lat_rad) * math.sin(tilt) * math.cos(panel_az) * math.cos(omega)
        + math.cos(decl) * math.sin(tilt) * math.sin(panel_az) * math.sin(omega)
    ))


def _hour_kwh(dni: float, diffuse: float, ghi: float, decl: float, omega: float) -> float:
    """Estimated kWh summed over all configured roof planes for one hour of irradiance."""
    lat_rad = math.radians(_state.latitude)
    total = 0.0
    for plane in _state.roof_planes:
        tilt = math.radians(plane["tilt"])
        panel_az = math.radians(plane["azimuth"])
        cos_aoi = _cos_aoi(lat_rad, decl, omega, tilt, panel_az)
        sky_view = (1 + math.cos(tilt)) / 2
        ground_ref = ghi * _GROUND_ALBEDO * (1 - math.cos(tilt)) / 2
        poa = dni * cos_aoi + diffuse * sky_view + ground_ref
        total += plane["kwp"] * (poa / 1000) * _SYSTEM_EFFICIENCY * _OPENMETEO_HAIRCUT
    return total


# ---------------------------------------------------------------------------
# Forecast.Solar (days 1–2)
# ---------------------------------------------------------------------------

def _fetch_api(power_kw: float, tilt: int, azimuth: int) -> dict:
    """Fetch raw Forecast.Solar API response for one roof plane, using disk cache."""
    cache_file = _state.cache_dir / f"forecast_{tilt}_{azimuth}_{power_kw}.json"

    if cache_file.exists():
        try:
            stored = json.loads(cache_file.read_text(encoding="utf-8"))
            age = datetime.now() - datetime.fromisoformat(stored["opgeslagen_op"])
            if age < _state.max_age:
                log.debug("Cache hit: %s (age %ds)", cache_file.name, age.seconds)
                return stored["result"]
            log.info("Cache stale (%ds old): %s — fetching from API", age.seconds, cache_file.name)
        except (KeyError, ValueError) as exc:
            log.warning("Cache file corrupt (%s): %s — fetching from API", cache_file.name, exc)
    else:
        log.info("No cache for %s — fetching from API", cache_file.name)

    url = (
        f"{FORECAST_SOLAR_BASE}"
        f"/{_state.latitude}/{_state.longitude}/{tilt}/{azimuth}/{power_kw}"
    )
    log.info("GET %s", url)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    result = resp.json()["result"]

    try:
        cache_file.write_text(
            json.dumps({"opgeslagen_op": datetime.now().isoformat(), "result": result}),
            encoding="utf-8",
        )
        log.debug("Cache written: %s", cache_file.name)
    except OSError as exc:
        log.warning("Could not write cache file %s: %s", cache_file, exc)

    return result


def fetch_roof_plane_forecast(
    power_kw: float, tilt: int, azimuth: int
) -> Dict[str, float]:
    """Return per-period energy production for one roof plane (kWh per period).

    Uses watt_hours_period (not watt_hours, which is cumulative).
    """
    raw = _fetch_api(power_kw, tilt, azimuth)["watt_hours_period"]
    return {ts: wh / 1000 for ts, wh in raw.items()}


def _fetch_solar_forecast() -> Dict[str, float]:
    """Combine all roof planes into one Forecast.Solar total (kWh per period).

    Raises SolarForecastError on any network, HTTP, or parsing failure.
    """
    try:
        total: Dict[str, float] = {}
        for plane in _state.roof_planes:
            plane_forecast = fetch_roof_plane_forecast(
                plane["kwp"], plane["tilt"], plane["azimuth"]
            )
            for ts, kwh in plane_forecast.items():
                total[ts] = total.get(ts, 0.0) + kwh
        log.info("Forecast.Solar ready: %d periods", len(total))
        return total
    except (requests.exceptions.RequestException, KeyError, ValueError) as exc:
        raise SolarForecastError(str(exc)) from exc


# ---------------------------------------------------------------------------
# OpenMeteo (days 1–7)
# ---------------------------------------------------------------------------

def _load_openmeteo_cache(cache_file: Path):
    """Return cached OpenMeteo result if still fresh, else None."""
    if not cache_file.exists():
        log.info("No OpenMeteo cache — fetching")
        return None
    try:
        stored = json.loads(cache_file.read_text(encoding="utf-8"))
        age = datetime.now() - datetime.fromisoformat(stored["saved_at"])
        if age < timedelta(hours=3):
            log.debug("OpenMeteo cache hit (age %ds)", age.seconds)
            return stored["result"]
        log.info("OpenMeteo cache stale (%ds) — fetching", age.seconds)
    except (KeyError, ValueError) as exc:
        log.warning("OpenMeteo cache corrupt: %s — fetching", exc)
    return None


def _fetch_openmeteo_forecast() -> Dict[str, float]:
    """Fetch 7-day hourly irradiance from OpenMeteo and convert to kWh per period.

    Timestamps are shifted +1h to match the period-end convention of Forecast.Solar.
    Raises requests.exceptions.RequestException or KeyError/ValueError on failure.
    """
    cache_file = _state.cache_dir / "openmeteo.json"
    cached = _load_openmeteo_cache(cache_file)
    if cached is not None:
        return cached

    # Derive the host's UTC offset so we can convert UTC timestamps to local time,
    # matching the period-end local-time keys that Forecast.Solar returns.
    utc_offset_h = round(
        (datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 3600
    )

    url = (
        f"{OPENMETEO_BASE}"
        f"?latitude={_state.latitude}&longitude={_state.longitude}"
        "&hourly=shortwave_radiation,diffuse_radiation,direct_normal_irradiance"
        "&forecast_days=7&timezone=UTC"
    )
    log.info("GET %s", url)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    hourly = resp.json()["hourly"]

    result: Dict[str, float] = {}
    for i, ts_str in enumerate(hourly["time"]):
        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M")  # UTC
        # +1h: OpenMeteo period-start → period-end. +utc_offset_h: UTC → local time.
        key = (dt + timedelta(hours=1 + utc_offset_h)).strftime("%Y-%m-%d %H:00:00")
        ghi = hourly["shortwave_radiation"][i] or 0.0
        if ghi <= 0 or not _state.roof_planes:
            result[key] = 0.0
            continue
        decl = _solar_declination(dt)
        omega = _solar_hour_angle(dt, _state.longitude)
        result[key] = _hour_kwh(
            hourly["direct_normal_irradiance"][i] or 0.0,
            hourly["diffuse_radiation"][i] or 0.0,
            ghi, decl, omega,
        )

    try:
        cache_file.write_text(
            json.dumps({"saved_at": datetime.now().isoformat(), "result": result}),
            encoding="utf-8",
        )
        log.debug("OpenMeteo cache written")
    except OSError as exc:
        log.warning("Could not write OpenMeteo cache: %s", exc)

    log.info("OpenMeteo forecast: %d periods", len(result))
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_forecast() -> Dict[str, float]:
    """Combined 7-day forecast: OpenMeteo base with Forecast.Solar overlay for days 1–2.

    Forecast.Solar overwrites OpenMeteo for any overlapping timestamps.
    Falls back to the other source if one fails; raises SolarForecastError if both fail.
    """
    openmeteo_ok = False
    try:
        combined = _fetch_openmeteo_forecast()
        openmeteo_ok = True
    except (requests.exceptions.RequestException, KeyError, ValueError) as exc:
        log.warning("OpenMeteo forecast failed: %s — using Forecast.Solar only", exc)
        combined = {}

    try:
        combined.update(_fetch_solar_forecast())
    except SolarForecastError as exc:
        if not openmeteo_ok:
            raise
        log.warning("Forecast.Solar failed: %s — using OpenMeteo only", exc)

    log.info("Combined forecast: %d periods", len(combined))
    return combined


def power_at(timestamp: datetime) -> float:
    """Return expected solar power in kW at a given timestamp.

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
