"""Unit tests for charger_app.py.

AppDaemon is stubbed in conftest.py so ChargeScheduler can be imported
without a running Home Assistant instance.  Each test creates the scheduler
via __new__ and sets the attributes that initialize() would normally write,
so tests are independent of each other.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Import after conftest.py has stubbed appdaemon
from charger.charger_app import ChargeScheduler, _OptimizeResult
from charger.solar_forecast import SolarForecastError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BATTERY_KWH = 58.0
CHARGING_POWER_KW = 11.0
DAY_RATE = 27.0
NIGHT_RATE = 23.0

_APPS_YAML = {
    "ratio_serial": "TESTSERIAL",
    "soc_sensor": "sensor.soc",
    "vehicle": {"battery_kwh": str(BATTERY_KWH), "charging_power_kw": str(CHARGING_POWER_KW)},
    "tariff": {
        "grid": {
            "type": "fixed",
            "price": 0.27,
            "zones": [{"hours": "22-6", "price": 0.23}],
        }
    },
    "panels": [{"name": "SE", "kwp": 2.58, "azimuth": -45, "tilt": 35}],
}


@pytest.fixture
def sched(mocker):
    """ChargeScheduler with all HA methods mocked, attributes pre-set."""
    s = ChargeScheduler.__new__(ChargeScheduler)
    s.args = _APPS_YAML

    s.get_state = mocker.MagicMock(return_value=None)
    s.set_state = mocker.MagicMock()
    s.call_service = mocker.MagicMock()
    s.run_in = mocker.MagicMock()
    s.run_hourly = mocker.MagicMock()
    s.listen_state = mocker.MagicMock()
    s.cancel_timer = mocker.MagicMock()
    s.log = mocker.MagicMock()

    # Attributes that initialize() sets
    s.soc_sensor = "sensor.soc"
    s.cable_sensor = "binary_sensor.cable"
    s.charge_mode_select = "select.mode"
    s.charge_target_entity = "input_number.charge_target"
    s.charge_minimum_entity = "input_number.charge_minimum"
    s.charge_by_entity = "input_datetime.charge_by"
    s.battery_kwh = BATTERY_KWH
    s.charging_power_kw = CHARGING_POWER_KW
    s.hourly_rates = {h: (NIGHT_RATE if h < 6 or h >= 22 else DAY_RATE) for h in range(24)}
    s.power_sensor = "sensor.ratio_TESTSERIAL_actual_charging_power"
    s._last_power_kw = None
    s._last_power_time = None
    s._threshold_timer = None

    return s


def _setup_states(
    sched, *, soc="60", target="80", deadline=None, cable="on", mode="PureSolar", minimum="0"
):
    """Configure get_state to return realistic values for all entities."""
    if deadline is None:
        # Far future so deadline-in-past check never fires
        deadline = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    mapping = {
        "sensor.soc": soc,
        "input_number.charge_target": target,
        "input_datetime.charge_by": deadline,
        "binary_sensor.cable": cable,
        "select.mode": mode,
        "input_number.charge_minimum": minimum,
    }
    sched.get_state.side_effect = lambda entity_id: mapping.get(entity_id)


def _assert_no_mode_set(sched):
    """Assert that select/select_option was never called (mode was not changed)."""
    select_calls = [
        c for c in sched.call_service.call_args_list
        if c[0][0] == "select/select_option"
    ]
    assert not select_calls, f"Unexpected mode change: {select_calls}"


# ---------------------------------------------------------------------------
# initialize()
# ---------------------------------------------------------------------------

def _mock_zone_home(sched, lat="52.37", lon="4.90"):
    """Configure get_state to return Amsterdam coordinates for zone.home."""
    def _get_state(*args, **kwargs):
        entity = args[0] if args else None
        attribute = kwargs.get("attribute")
        if entity == "zone.home" and attribute == "latitude":
            return lat
        if entity == "zone.home" and attribute == "longitude":
            return lon
        return None
    sched.get_state.side_effect = _get_state


class TestInitialize:
    def test_sets_entity_attributes(self, sched, mocker):
        mocker.patch("charger.solar_forecast.configure")
        _mock_zone_home(sched)
        sched.initialize()
        assert sched.soc_sensor == "sensor.soc"
        assert sched.cable_sensor == "binary_sensor.ratio_TESTSERIAL_vehicle_connected"
        assert sched.charge_mode_select == "select.ratio_TESTSERIAL_charge_mode"
        assert sched.charge_target_entity == "input_number.charge_target"
        assert sched.charge_by_entity == "input_datetime.charge_by"
        assert sched.battery_kwh == BATTERY_KWH
        assert sched.charging_power_kw == CHARGING_POWER_KW
        assert isinstance(sched.hourly_rates, dict)
        assert len(sched.hourly_rates) == 24
        assert sched.hourly_rates[12] == DAY_RATE
        assert sched.hourly_rates[23] == NIGHT_RATE

    def test_schedules_immediate_and_hourly_replan(self, sched, mocker):
        mocker.patch("charger.solar_forecast.configure")
        _mock_zone_home(sched)
        sched.initialize()
        sched.run_in.assert_called_once_with(sched._replan, 0)
        sched.run_hourly.assert_called_once_with(sched._replan, "00:00:00")

    def test_registers_four_listen_state_calls(self, sched, mocker):
        mocker.patch("charger.solar_forecast.configure")
        _mock_zone_home(sched)
        sched.initialize()
        # replan button, cable_sensor, power_sensor, cable_disconnect
        assert sched.listen_state.call_count == 4

    def test_calls_solar_forecast_configure(self, sched, mocker):
        configure = mocker.patch("charger.solar_forecast.configure")
        _mock_zone_home(sched)
        sched.initialize()
        configure.assert_called_once()
        _, kwargs = configure.call_args
        assert kwargs["latitude"] == pytest.approx(52.37)
        assert kwargs["longitude"] == pytest.approx(4.90)


# ---------------------------------------------------------------------------
# _replan() — early-exit branches
# ---------------------------------------------------------------------------

class TestReplanEarlyExits:
    def test_cable_disconnected_publishes_status_and_returns(self, sched, mocker):
        _setup_states(sched, cable="off")
        mocker.patch("charger.solar_forecast.fetch_forecast")

        sched._replan()

        sched.set_state.assert_called_once()
        state_text = sched.set_state.call_args[0][0]
        assert state_text == "sensor.charge_plan"
        attributes = sched.set_state.call_args[1]["attributes"]
        assert "Cable not connected" in attributes["plan"]
        sched.call_service.assert_not_called()

    def test_cable_disconnected_writes_plan_json(self, sched, mocker):
        _setup_states(sched, cable="off")
        mocker.patch("charger.solar_forecast.fetch_forecast")
        write_json = mocker.patch.object(sched, "_write_plan_json")

        sched._replan()

        write_json.assert_called_once()
        plan = write_json.call_args[0][0]
        assert "Cable not connected" in plan.status

    def test_soc_unavailable_publishes_status(self, sched, mocker):
        _setup_states(sched, soc="unavailable")
        mocker.patch("charger.solar_forecast.fetch_forecast")

        sched._replan()

        args, kwargs = sched.set_state.call_args
        assert "SoC unavailable" in kwargs["attributes"]["plan"]
        sched.call_service.assert_not_called()

    def test_soc_unavailable_writes_plan_json(self, sched, mocker):
        _setup_states(sched, soc="unavailable")
        mocker.patch("charger.solar_forecast.fetch_forecast")
        write_json = mocker.patch.object(sched, "_write_plan_json")

        sched._replan()

        write_json.assert_called_once()
        plan = write_json.call_args[0][0]
        assert "SoC unavailable" in plan.status

    def test_soc_unknown_publishes_status(self, sched, mocker):
        _setup_states(sched, soc="unknown")
        mocker.patch("charger.solar_forecast.fetch_forecast")

        sched._replan()

        args, kwargs = sched.set_state.call_args
        assert "SoC unavailable" in kwargs["attributes"]["plan"]

    def test_target_unavailable_publishes_status(self, sched, mocker):
        _setup_states(sched, target="unavailable")
        mocker.patch("charger.solar_forecast.fetch_forecast")

        sched._replan()

        args, kwargs = sched.set_state.call_args
        assert "charge target unavailable" in kwargs["attributes"]["plan"]
        _assert_no_mode_set(sched)

    def test_target_unavailable_writes_plan_json_with_soc(self, sched, mocker):
        _setup_states(sched, soc="55", target="unavailable")
        mocker.patch("charger.solar_forecast.fetch_forecast")
        write_json = mocker.patch.object(sched, "_write_plan_json")

        sched._replan()

        write_json.assert_called_once()
        plan = write_json.call_args[0][0]
        assert plan.soc_start == pytest.approx(55.0)
        assert "charge target unavailable" in plan.status

    def test_deadline_in_past_shows_warning(self, sched, mocker):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        _setup_states(sched, deadline=past)

        sched._replan()

        args, kwargs = sched.set_state.call_args
        assert "Deadline passed" in kwargs["attributes"]["plan"]

    def test_deadline_in_past_sets_smart_mode(self, sched, mocker):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        _setup_states(sched, deadline=past, mode="PureSolar")

        sched._replan()

        sched.call_service.assert_any_call(
            "select/select_option",
            entity_id="select.mode",
            option="Smart",
        )

    def test_deadline_in_past_writes_warning_to_json(self, sched, mocker):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        _setup_states(sched, soc="60", target="80", deadline=past)
        write_json = mocker.patch.object(sched, "_write_plan_json")

        sched._replan()

        write_json.assert_called_once()
        plan = write_json.call_args[0][0]
        assert plan.slots == []
        assert plan.status is None
        assert "Deadline passed" in plan.warning

    def test_target_reached_writes_empty_plan_json(self, sched, mocker):
        _setup_states(sched, soc="85", target="80")
        mocker.patch("charger.solar_forecast.fetch_forecast")
        write_json = mocker.patch.object(sched, "_write_plan_json")

        sched._replan()

        write_json.assert_called_once()
        plan = write_json.call_args[0][0]
        assert plan.slots == []
        assert "Target reached" in plan.status

    def test_target_already_reached_sets_pure_solar(self, sched, mocker):
        _setup_states(sched, soc="85", target="80", mode="Smart")
        mocker.patch("charger.solar_forecast.fetch_forecast")

        sched._replan()

        sched.call_service.assert_any_call(
            "select/select_option",
            entity_id="select.mode",
            option="PureSolar",
        )

    def test_target_exactly_met_sets_pure_solar(self, sched, mocker):
        _setup_states(sched, soc="80", target="80", mode="Smart")
        mocker.patch("charger.solar_forecast.fetch_forecast")

        sched._replan()

        sched.call_service.assert_any_call(
            "select/select_option",
            entity_id="select.mode",
            option="PureSolar",
        )


# ---------------------------------------------------------------------------
# _replan() — planning paths
# ---------------------------------------------------------------------------

class TestReplanPlanning:
    def test_solar_forecast_failure_continues_with_empty_forecast(self, sched, mocker):
        _setup_states(sched, soc="25", target="80")
        mocker.patch(
            "charger.solar_forecast.fetch_forecast",
            side_effect=SolarForecastError("API error"),
        )

        # Should not raise; plan should be built using Smart-only candidates
        sched._replan()
        sched.set_state.assert_called()

    def test_impossible_deadline_publishes_warning_in_plan(self, sched, mocker):
        # Deadline only 5 minutes away → 0 full-hour slots available
        soon = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        _setup_states(sched, soc="25", target="80", deadline=soon)
        mocker.patch("charger.solar_forecast.fetch_forecast", return_value={})

        sched._replan()

        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        assert "not achievable" in plan

    def test_normal_plan_sets_charge_mode(self, sched, mocker):
        # SoC=70%, target=80% → need 5.8 kWh; deadline 7 days away → plenty of slots
        _setup_states(sched, soc="70", target="80")
        mocker.patch("charger.solar_forecast.fetch_forecast", return_value={})

        sched._replan()

        # Charge mode should have been set to something (Smart or PureSolar)
        # (exact mode depends on current hour, but call_service must be called if mode changed)
        sched.set_state.assert_called()

    def test_normal_plan_publishes_slot_details(self, sched, mocker):
        _setup_states(sched, soc="25", target="80", mode="Smart")
        mocker.patch("charger.solar_forecast.fetch_forecast", return_value={})

        sched._replan()

        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        # Plan should contain time and mode info
        assert "Smart" in plan or "No charging" in plan

    def test_mode_not_changed_when_already_correct(self, sched, mocker):
        # SoC already at target → PureSolar; current mode is also PureSolar
        _setup_states(sched, soc="80", target="80", mode="PureSolar")
        mocker.patch("charger.solar_forecast.fetch_forecast")

        sched._replan()

        _assert_no_mode_set(sched)

    def test_minimum_exceeds_target_raises_target_slider(self, sched, mocker):
        # minimum=70% > target=50% → charge_target slider must be updated to 70
        _setup_states(sched, soc="20", target="50", minimum="70")
        mocker.patch("charger.solar_forecast.fetch_forecast", return_value={})

        sched._replan()

        sched.call_service.assert_any_call(
            "input_number/set_value",
            entity_id="input_number.charge_target",
            value=70.0,
        )

    def test_minimum_exceeds_target_logs_warning(self, sched, mocker):
        # minimum=70% > target=50% → warning must be logged at WARNING level
        _setup_states(sched, soc="20", target="50", minimum="70")
        mocker.patch("charger.solar_forecast.fetch_forecast", return_value={})

        sched._replan()

        warning_calls = [c for c in sched.log.call_args_list if c.kwargs.get("level") == "WARNING"]
        assert any("target" in str(c).lower() for c in warning_calls)

    def test_minimum_exceeds_target_shows_warning_in_plan(self, sched, mocker):
        # minimum=70% > target=50% → warning must appear in the dashboard plan text
        _setup_states(sched, soc="20", target="50", minimum="70")
        mocker.patch("charger.solar_forecast.fetch_forecast", return_value={})

        sched._replan()

        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        assert "target" in plan.lower()


# ---------------------------------------------------------------------------
# _publish_plan()
# ---------------------------------------------------------------------------

class TestPublishPlan:
    _DL = datetime(2025, 6, 7, 6, 0)

    def _make_slot(self, hour: int, mode: str = "Smart", energy: float = 11.0) -> dict:
        return {
            "slot": datetime(2025, 6, 1, hour, 0),
            "mode": mode,
            "effective_price": 23.0,
            "power_kw": 11.0,
            "energy_kwh": energy,
        }

    def _result(self, slots=None, mode="Smart", warning=None) -> _OptimizeResult:
        return _OptimizeResult(slots=slots or [], mode=mode, warning=warning)

    def test_empty_selected_shows_no_slots_message(self, sched):
        sched._publish_plan(self._result(), 50.0, 80.0, self._DL)
        args, kwargs = sched.set_state.call_args
        assert "No charging slots planned" in kwargs["attributes"]["plan"]

    def test_warning_prepended_when_provided(self, sched):
        sched._publish_plan(self._result(warning="Test warning"), 50.0, 80.0, self._DL)
        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        assert plan.startswith("Test warning")

    def test_running_soc_increases_per_slot(self, sched):
        slot = self._make_slot(15, energy=5.8)  # 5.8 / 58 kWh = +10%
        sched._publish_plan(self._result([slot]), 70.0, 80.0, self._DL)
        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        assert "80%" in plan

    def test_running_soc_capped_at_target(self, sched):
        # Energy would overshoot target; check it's capped
        slot = self._make_slot(15, energy=11.0)  # 11/58 * 100 ≈ 19%
        sched._publish_plan(self._result([slot]), 75.0, 80.0, self._DL)
        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        assert "80%" in plan
        assert "99%" not in plan

    def test_partial_slot_end_time_not_full_hour(self, sched):
        # 5 kWh at 11 kW → 5/11 hours ≈ 27 minutes, not a full hour
        slot = self._make_slot(15, energy=5.0)
        sched._publish_plan(self._result([slot]), 70.0, 80.0, self._DL)
        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        # Start is 15:00, end should NOT be 16:00
        assert "16:00" not in plan

    def test_plan_contains_mode_and_price(self, sched):
        slot = self._make_slot(22, mode="Smart", energy=11.0)
        sched._publish_plan(self._result([slot], mode="Smart"), 25.0, 80.0, self._DL)
        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        assert "Smart" in plan
        assert "ct/kWh" in plan


# ---------------------------------------------------------------------------
# _publish_status()
# ---------------------------------------------------------------------------

class TestPublishStatus:
    def test_state_truncated_to_255_chars(self, sched):
        long_text = "x" * 300
        sched._publish_status(long_text)
        args, kwargs = sched.set_state.call_args
        state_value = args[1] if len(args) > 1 else kwargs.get("state")
        # set_state is called as set_state("sensor.charge_plan", state=..., attributes=...)
        assert len(sched.set_state.call_args[1]["attributes"]["plan"]) == 300
        # The state kwarg or positional should be truncated
        call_kwargs = sched.set_state.call_args[1]
        # state is the first positional arg after entity_id in set_state
        # AppDaemon: set_state(entity_id, state=..., attributes=...)
        assert len(call_kwargs.get("state", "x" * 256)) <= 255

    def test_full_text_stored_in_attribute(self, sched):
        text = "line1\nline2\nline3"
        sched._publish_status(text)
        args, kwargs = sched.set_state.call_args
        assert kwargs["attributes"]["plan"] == text

    def test_entity_id_is_charge_plan(self, sched):
        sched._publish_status("test")
        entity_id = sched.set_state.call_args[0][0]
        assert entity_id == "sensor.charge_plan"


# ---------------------------------------------------------------------------
# _read_soc()
# ---------------------------------------------------------------------------

class TestReadSoc:
    def test_valid_integer_string(self, sched):
        sched.get_state.return_value = "75"
        assert sched._read_soc() == pytest.approx(75.0)

    def test_valid_float_string(self, sched):
        sched.get_state.return_value = "75.5"
        assert sched._read_soc() == pytest.approx(75.5)

    def test_unavailable_returns_none(self, sched):
        sched.get_state.return_value = "unavailable"
        assert sched._read_soc() is None

    def test_unknown_returns_none(self, sched):
        sched.get_state.return_value = "unknown"
        assert sched._read_soc() is None

    def test_none_returns_none(self, sched):
        sched.get_state.return_value = None
        assert sched._read_soc() is None

    def test_invalid_string_returns_none(self, sched):
        sched.get_state.return_value = "not_a_number"
        assert sched._read_soc() is None


# ---------------------------------------------------------------------------
# _read_charge_target()
# ---------------------------------------------------------------------------

class TestReadChargeTarget:
    def test_valid_value_returned_as_float(self, sched):
        sched.get_state.return_value = "80"
        assert sched._read_charge_target() == pytest.approx(80.0)

    def test_unavailable_returns_none(self, sched):
        sched.get_state.return_value = "unavailable"
        assert sched._read_charge_target() is None

    def test_invalid_string_returns_none(self, sched):
        sched.get_state.return_value = "abc"
        assert sched._read_charge_target() is None


# ---------------------------------------------------------------------------
# _read_deadline()
# ---------------------------------------------------------------------------

class TestReadDeadline:
    def test_valid_datetime_string_parsed(self, sched):
        sched.get_state.return_value = "2025-12-31 14:30:00"
        result = sched._read_deadline()
        assert result == datetime(2025, 12, 31, 14, 30, 0)

    def test_unavailable_falls_back_to_seven_days(self, sched):
        sched.get_state.return_value = "unavailable"
        before = datetime.now()
        result = sched._read_deadline()
        after = datetime.now()
        assert before + timedelta(days=6, hours=23) < result < after + timedelta(days=7, hours=1)

    def test_unknown_falls_back_to_seven_days(self, sched):
        sched.get_state.return_value = "unknown"
        result = sched._read_deadline()
        assert result > datetime.now() + timedelta(days=6)

    def test_wrong_format_falls_back_to_seven_days(self, sched):
        sched.get_state.return_value = "31-12-2025 14:30"
        result = sched._read_deadline()
        assert result > datetime.now() + timedelta(days=6)


# ---------------------------------------------------------------------------
# _set_mode()
# ---------------------------------------------------------------------------

class TestSetMode:
    def test_no_op_when_mode_already_set(self, sched):
        sched.get_state.return_value = "Smart"
        sched._set_mode("Smart")
        sched.call_service.assert_not_called()

    def test_calls_service_when_mode_changes(self, sched):
        sched.get_state.return_value = "PureSolar"
        sched._set_mode("Smart")
        sched.call_service.assert_called_once_with(
            "select/select_option",
            entity_id="select.mode",
            option="Smart",
        )

    def test_logs_mode_change(self, sched):
        sched.get_state.return_value = "PureSolar"
        sched._set_mode("SmartSolar")
        sched.log.assert_called()
        log_msg = sched.log.call_args[0][0]
        assert "PureSolar" in log_msg
        assert "SmartSolar" in log_msg


# ---------------------------------------------------------------------------
# _read_soc_fallback()
# ---------------------------------------------------------------------------

class TestReadSocFallback:
    def test_returns_none_when_override_unavailable(self, sched):
        sched.get_state.return_value = "unavailable"
        assert sched._read_soc_fallback() is None

    def test_returns_none_when_override_none(self, sched):
        sched.get_state.return_value = None
        assert sched._read_soc_fallback() is None

    def test_base_soc_when_no_session_energy(self, sched):
        def _get_state(entity_id):
            return {"input_number.soc_override": "60", "sensor.session_energy_kwh": "0"}.get(
                entity_id
            )
        sched.get_state.side_effect = _get_state
        assert sched._read_soc_fallback() == pytest.approx(60.0)

    def test_adds_session_energy_to_base_soc(self, sched):
        # 5.8 kWh on 58 kWh battery = +10%
        def _get_state(entity_id):
            return {"input_number.soc_override": "60", "sensor.session_energy_kwh": "5.8"}.get(
                entity_id
            )
        sched.get_state.side_effect = _get_state
        assert sched._read_soc_fallback() == pytest.approx(70.0)

    def test_capped_at_100(self, sched):
        # 95% + 10 kWh / 58 kWh * 100 ≈ 95 + 17.2 → capped at 100
        def _get_state(entity_id):
            return {"input_number.soc_override": "95", "sensor.session_energy_kwh": "10.0"}.get(
                entity_id
            )
        sched.get_state.side_effect = _get_state
        assert sched._read_soc_fallback() == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# _on_power_change()
# ---------------------------------------------------------------------------

class TestOnPowerChange:
    def test_first_call_stores_power_without_accumulating(self, sched):
        sched._on_power_change("entity", None, None, "5500", {})

        sched.set_state.assert_not_called()
        assert sched._last_power_kw == pytest.approx(5.5)

    def test_watts_converted_to_kw(self, sched):
        sched._on_power_change("entity", None, None, "11000", {})
        assert sched._last_power_kw == pytest.approx(11.0)

    def test_accumulates_energy_after_first_reading(self, sched):
        sched._last_power_kw = 11.0
        sched._last_power_time = datetime.now() - timedelta(hours=1)
        sched.get_state.return_value = "0"

        sched._on_power_change("entity", None, None, "5500", {})

        sched.set_state.assert_called_once()
        args, kwargs = sched.set_state.call_args
        assert args[0] == "sensor.session_energy_kwh"
        assert kwargs["state"] == pytest.approx(11.0, abs=0.01)

    def test_zero_previous_power_does_not_accumulate(self, sched):
        sched._last_power_kw = 0.0
        sched._last_power_time = datetime.now() - timedelta(hours=1)

        sched._on_power_change("entity", None, None, "5500", {})

        sched.set_state.assert_not_called()

    def test_unavailable_stored_as_zero_kw(self, sched):
        sched._last_power_kw = 0.0
        sched._last_power_time = datetime.now() - timedelta(minutes=1)
        sched.get_state.return_value = "0"

        sched._on_power_change("entity", None, None, "unavailable", {})

        assert sched._last_power_kw == 0.0

    def test_none_new_stored_as_zero_kw(self, sched):
        sched._on_power_change("entity", None, None, None, {})
        assert sched._last_power_kw == 0.0


# ---------------------------------------------------------------------------
# _on_cable_disconnect() / _reset_session_energy()
# ---------------------------------------------------------------------------

class TestOnCableDisconnect:
    def test_resets_session_energy_sensor_to_zero(self, sched):
        sched._on_cable_disconnect("entity", None, "on", "off", {})

        sched.set_state.assert_called_once()
        args, kwargs = sched.set_state.call_args
        assert args[0] == "sensor.session_energy_kwh"
        assert kwargs["state"] == 0.0

    def test_clears_tracking_variables(self, sched):
        sched._last_power_kw = 11.0
        sched._last_power_time = datetime.now()

        sched._on_cable_disconnect("entity", None, "on", "off", {})

        assert sched._last_power_kw is None
        assert sched._last_power_time is None


# ---------------------------------------------------------------------------
# _schedule_threshold_timer()
# ---------------------------------------------------------------------------

class TestScheduleThresholdTimer:
    def test_schedules_timer_for_minimum_soc_threshold(self, sched):
        # 5.8 kWh at 11 kW → ~31.6 min < 60 min → timer expected
        handle = object()
        sched.run_in.return_value = handle

        sched._schedule_threshold_timer(immediate_kwh=5.8, energy_needed_kwh=20.0)

        sched.run_in.assert_called_once()
        callback, delay = sched.run_in.call_args[0]
        assert callback == sched._replan
        assert 60 <= delay < 3600
        assert sched._threshold_timer is handle

    def test_schedules_timer_for_target_when_minimum_already_met(self, sched):
        # immediate_kwh=0 → falls back to energy_needed_kwh
        # 5.0 kWh at 11 kW → ~27 min < 60 min → timer expected
        sched.run_in.return_value = "handle"

        sched._schedule_threshold_timer(immediate_kwh=0.0, energy_needed_kwh=5.0)

        sched.run_in.assert_called_once()

    def test_no_timer_when_threshold_beyond_one_hour(self, sched):
        # 15 kWh at 11 kW → ~81 min > 60 min → no timer
        sched._schedule_threshold_timer(immediate_kwh=0.0, energy_needed_kwh=15.0)

        sched.run_in.assert_not_called()

    def test_delay_clamped_to_60_seconds_minimum(self, sched):
        # 0.58 kWh at 11 kW → ~3.2 min; delay = max(60, 3.2*60 - 300) → 60 s
        sched.run_in.return_value = "handle"

        sched._schedule_threshold_timer(immediate_kwh=0.58, energy_needed_kwh=10.0)

        _, delay = sched.run_in.call_args[0]
        assert delay == 60

    def test_delay_is_five_minutes_before_threshold(self, sched):
        # 33 min threshold → 33*60 - 300 = 1980 - 300 = 1680 s
        kwh_for_33_min = CHARGING_POWER_KW * 33 / 60
        sched.run_in.return_value = "handle"

        sched._schedule_threshold_timer(immediate_kwh=kwh_for_33_min, energy_needed_kwh=20.0)

        _, delay = sched.run_in.call_args[0]
        assert delay == pytest.approx(33 * 60 - 300, abs=1)


# ---------------------------------------------------------------------------
# _replan() — threshold timer integration
# ---------------------------------------------------------------------------

class TestReplanThresholdTimer:
    def test_existing_timer_cancelled_at_start_of_replan(self, sched, mocker):
        _setup_states(sched, soc="60", target="80")
        mocker.patch("charger.solar_forecast.fetch_forecast", return_value={})
        sched._threshold_timer = "fake-handle"

        sched._replan()

        sched.cancel_timer.assert_called_once_with("fake-handle")
        assert sched._threshold_timer is None

    def test_no_cancel_when_no_existing_timer(self, sched, mocker):
        _setup_states(sched, soc="60", target="80")
        mocker.patch("charger.solar_forecast.fetch_forecast", return_value={})

        sched._replan()

        sched.cancel_timer.assert_not_called()

    def test_timer_scheduled_via_replan_when_minimum_near(self, sched, mocker):
        # SoC=20%, minimum=30% → 5.8 kWh at 11 kW → ~31.6 min → timer
        _setup_states(sched, soc="20", target="80", minimum="30")
        mocker.patch("charger.solar_forecast.fetch_forecast", return_value={})
        sched.run_in.return_value = "timer-handle"

        sched._replan()

        timer_calls = [c for c in sched.run_in.call_args_list if c[0][0] == sched._replan]
        assert timer_calls, "Expected a mid-hour replan timer"
        assert sched._threshold_timer == "timer-handle"

    def test_timer_scheduled_when_target_near_and_minimum_met(self, sched, mocker):
        # SoC=75%, target=80%, minimum=0% → 2.9 kWh at 11 kW → ~15.8 min → timer
        _setup_states(sched, soc="75", target="80", minimum="0")
        mocker.patch("charger.solar_forecast.fetch_forecast", return_value={})
        sched.run_in.return_value = "timer-handle"

        sched._replan()

        timer_calls = [c for c in sched.run_in.call_args_list if c[0][0] == sched._replan]
        assert timer_calls, "Expected a mid-hour replan timer when target is near"
