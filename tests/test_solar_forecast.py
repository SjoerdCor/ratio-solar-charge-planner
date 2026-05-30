"""Tests for solar_forecast.py — geometry helpers, OpenMeteo fetch, and merge logic."""
# pylint: disable=protected-access,missing-function-docstring,missing-class-docstring

import json
import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
import requests

import charger.solar_forecast as sf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _configure(tmp_path, planes=None):
    if planes is None:
        planes = [{"kwp": 4.0, "tilt": 35, "azimuth": 0}]
    sf.configure(
        latitude=52.0,
        longitude=5.0,
        roof_planes=planes,
        cache_dir=tmp_path,
    )


def _openmeteo_response(times, ghi_values, dni_values, diffuse_values):
    """Build a minimal OpenMeteo API response dict."""
    return {
        "hourly": {
            "time": times,
            "shortwave_radiation": ghi_values,
            "direct_normal_irradiance": dni_values,
            "diffuse_radiation": diffuse_values,
        }
    }


# ---------------------------------------------------------------------------
# _solar_declination
# ---------------------------------------------------------------------------

class TestSolarDeclination:
    def test_summer_solstice_positive(self):
        # ~21 June (doy 172) → declination ≈ +23.45°
        dt = datetime(2026, 6, 21, 12, 0)
        decl_deg = math.degrees(sf._solar_declination(dt))
        assert 22.0 < decl_deg < 24.0

    def test_winter_solstice_negative(self):
        # ~21 Dec (doy 355) → declination ≈ -23.45°
        dt = datetime(2026, 12, 21, 12, 0)
        decl_deg = math.degrees(sf._solar_declination(dt))
        assert -24.0 < decl_deg < -22.0

    def test_march_equinox_near_zero(self):
        # ~21 March (doy 80) → declination ≈ 0°
        dt = datetime(2026, 3, 21, 12, 0)
        decl_deg = math.degrees(sf._solar_declination(dt))
        assert -2.0 < decl_deg < 2.0


# ---------------------------------------------------------------------------
# _solar_hour_angle
# ---------------------------------------------------------------------------

class TestSolarHourAngle:
    def test_solar_noon_at_prime_meridian(self):
        # At 12:00 UTC, lon=0°: true solar noon → hour angle = 0
        dt = datetime(2026, 6, 21, 12, 0)
        omega = sf._solar_hour_angle(dt, lon_deg=0.0)
        assert abs(omega) < 0.01  # < 0.6°

    def test_morning_is_negative(self):
        # 08:00 UTC at lon=0°: sun is east of south → negative hour angle
        dt = datetime(2026, 6, 21, 8, 0)
        assert sf._solar_hour_angle(dt, lon_deg=0.0) < 0

    def test_afternoon_is_positive(self):
        # 16:00 UTC at lon=0°: sun is west of south → positive hour angle
        dt = datetime(2026, 6, 21, 16, 0)
        assert sf._solar_hour_angle(dt, lon_deg=0.0) > 0


# ---------------------------------------------------------------------------
# _cos_aoi
# ---------------------------------------------------------------------------

class TestCosAoi:
    def test_horizontal_panel_sun_overhead(self):
        # Horizontal panel (tilt=0), sun straight up (decl=lat, omega=0 → zenith=0)
        # Simplest check: tilt=0, panel_az irrelevant → cos_aoi = cos(zenith)
        lat = math.radians(52.0)
        decl = math.radians(52.0)  # sun directly overhead at this latitude
        omega = 0.0
        tilt = 0.0
        result = sf._cos_aoi(lat, decl, omega, tilt, panel_az=0.0)
        assert result == pytest.approx(1.0, abs=0.01)

    def test_sun_behind_panel_returns_zero(self):
        # Panel faces south (az=0, tilt=45°), sun is due north → cos_aoi < 0 → clamped to 0
        lat = math.radians(52.0)
        decl = math.radians(-23.0)  # winter, sun low in south
        omega = math.radians(180.0)  # "midnight" equivalent — sun behind
        tilt = math.radians(45.0)
        result = sf._cos_aoi(lat, decl, omega, tilt, panel_az=0.0)
        assert result == 0.0

    def test_south_facing_panel_at_noon_summer(self):
        # South-facing panel, sun due south at noon in summer → positive cos_aoi
        lat = math.radians(52.0)
        decl = math.radians(23.0)
        omega = 0.0  # solar noon
        tilt = math.radians(35.0)
        panel_az = 0.0  # south
        result = sf._cos_aoi(lat, decl, omega, tilt, panel_az)
        assert 0.0 < result <= 1.0


# ---------------------------------------------------------------------------
# _fetch_openmeteo_forecast
# ---------------------------------------------------------------------------

PLANES = [{"kwp": 4.0, "tilt": 35, "azimuth": 0}]


class TestFetchOpenmeteoForecast:
    def test_daytime_hours_produce_positive_kwh(self, tmp_path, mocker):
        _configure(tmp_path, planes=PLANES)
        mock_resp = MagicMock()
        mock_resp.json.return_value = _openmeteo_response(
            times=["2026-06-01T10:00"],  # UTC
            ghi_values=[600.0],
            dni_values=[500.0],
            diffuse_values=[100.0],
        )
        mocker.patch("charger.solar_forecast.requests.get", return_value=mock_resp)

        result = sf._fetch_openmeteo_forecast()

        # Key = UTC T10:00 + 1h shift + local UTC offset; just assert some key has positive value
        assert any(v > 0 for v in result.values())

    def test_nighttime_hours_produce_zero(self, tmp_path, mocker):
        _configure(tmp_path, planes=PLANES)
        mock_resp = MagicMock()
        mock_resp.json.return_value = _openmeteo_response(
            times=["2026-06-01T23:00"],  # UTC, night everywhere
            ghi_values=[0.0],
            dni_values=[0.0],
            diffuse_values=[0.0],
        )
        mocker.patch("charger.solar_forecast.requests.get", return_value=mock_resp)

        result = sf._fetch_openmeteo_forecast()
        assert all(v == 0.0 for v in result.values())

    def test_timestamp_shifted_to_period_end_local(self, tmp_path, mocker):
        # UTC T14:00 is a period-start; the key must be shifted by at least +1h.
        _configure(tmp_path, planes=PLANES)
        mock_resp = MagicMock()
        mock_resp.json.return_value = _openmeteo_response(
            times=["2026-06-01T14:00"],
            ghi_values=[500.0],
            dni_values=[400.0],
            diffuse_values=[100.0],
        )
        mocker.patch("charger.solar_forecast.requests.get", return_value=mock_resp)

        result = sf._fetch_openmeteo_forecast()

        assert len(result) == 1
        assert "2026-06-01 14:00:00" not in result  # UTC period-start must not appear

    def test_no_panels_returns_zero(self, tmp_path, mocker):
        _configure(tmp_path, planes=[])
        mock_resp = MagicMock()
        mock_resp.json.return_value = _openmeteo_response(
            times=["2026-06-01T12:00"],
            ghi_values=[700.0],
            dni_values=[600.0],
            diffuse_values=[100.0],
        )
        mocker.patch("charger.solar_forecast.requests.get", return_value=mock_resp)

        result = sf._fetch_openmeteo_forecast()
        assert all(v == 0.0 for v in result.values())

    def test_uses_cache_when_fresh(self, tmp_path, mocker):
        _configure(tmp_path, planes=PLANES)
        cached = {"2026-06-01 11:00:00": 1.23}
        cache_file = tmp_path / "openmeteo.json"
        cache_file.write_text(
            json.dumps({"saved_at": datetime.now().isoformat(), "result": cached}),
            encoding="utf-8",
        )
        get = mocker.patch("charger.solar_forecast.requests.get")

        result = sf._fetch_openmeteo_forecast()

        get.assert_not_called()
        assert result == cached

    def test_ignores_stale_cache(self, tmp_path, mocker):
        _configure(tmp_path, planes=PLANES)
        stale_time = (datetime.now() - timedelta(hours=4)).isoformat()
        cache_file = tmp_path / "openmeteo.json"
        cache_file.write_text(
            json.dumps({"saved_at": stale_time, "result": {"old": 0.0}}),
            encoding="utf-8",
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = _openmeteo_response(
            times=["2026-06-01T12:00"],
            ghi_values=[500.0],
            dni_values=[400.0],
            diffuse_values=[100.0],
        )
        mocker.patch("charger.solar_forecast.requests.get", return_value=mock_resp)

        result = sf._fetch_openmeteo_forecast()

        assert "old" not in result


# ---------------------------------------------------------------------------
# fetch_forecast (merge logic)
# ---------------------------------------------------------------------------

class TestFetchForecast:
    def test_solar_overwrites_openmeteo_for_overlapping_timestamps(self, tmp_path, mocker):
        _configure(tmp_path)
        mocker.patch(
            "charger.solar_forecast._fetch_openmeteo_forecast",
            return_value={
                "2026-06-01 10:00:00": 1.0,
                "2026-06-05 10:00:00": 2.0,
            },
        )
        mocker.patch(
            "charger.solar_forecast._fetch_solar_forecast",
            return_value={"2026-06-01 10:00:00": 3.5},
        )

        result = sf.fetch_forecast()

        assert result["2026-06-01 10:00:00"] == pytest.approx(3.5)  # solar wins
        assert result["2026-06-05 10:00:00"] == pytest.approx(2.0)   # openmeteo for far future

    def test_falls_back_to_solar_when_openmeteo_fails(self, tmp_path, mocker):
        _configure(tmp_path)
        mocker.patch(
            "charger.solar_forecast._fetch_openmeteo_forecast",
            side_effect=requests.exceptions.ConnectionError("network error"),
        )
        mocker.patch(
            "charger.solar_forecast._fetch_solar_forecast",
            return_value={"2026-06-01 10:00:00": 1.5},
        )

        result = sf.fetch_forecast()
        assert result == {"2026-06-01 10:00:00": pytest.approx(1.5)}

    def test_falls_back_to_openmeteo_when_solar_fails(self, tmp_path, mocker):
        _configure(tmp_path)
        mocker.patch(
            "charger.solar_forecast._fetch_openmeteo_forecast",
            return_value={"2026-06-05 10:00:00": 2.0},
        )
        mocker.patch(
            "charger.solar_forecast._fetch_solar_forecast",
            side_effect=sf.SolarForecastError("API down"),
        )

        result = sf.fetch_forecast()
        assert result == {"2026-06-05 10:00:00": pytest.approx(2.0)}

    def test_raises_solar_forecast_error_when_both_fail(self, tmp_path, mocker):
        _configure(tmp_path)
        mocker.patch(
            "charger.solar_forecast._fetch_openmeteo_forecast",
            side_effect=requests.exceptions.ConnectionError("network error"),
        )
        mocker.patch(
            "charger.solar_forecast._fetch_solar_forecast",
            side_effect=sf.SolarForecastError("API down"),
        )

        with pytest.raises(sf.SolarForecastError):
            sf.fetch_forecast()
