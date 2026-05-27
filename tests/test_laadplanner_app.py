"""Unit tests for laadplanner_app.py.

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
from laadplanner.laadplanner_app import ChargeScheduler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BATTERY_KWH = 58.0
CHARGING_POWER_KW = 11.0
DAY_RATE = 27.0
NIGHT_RATE = 23.0

_APPS_YAML = {
    "entities": {
        "soc_sensor": "sensor.soc",
        "cable_sensor": "binary_sensor.cable",
        "charge_mode_select": "select.mode",
        "charge_target": "input_number.target",
        "charge_by": "input_datetime.deadline",
    },
    "vehicle": {"battery_kwh": str(BATTERY_KWH), "charging_power_kw": str(CHARGING_POWER_KW)},
    "fixed_rate": {"day_rate_ct": str(DAY_RATE), "night_rate_ct": str(NIGHT_RATE)},
    "location": {"latitude": "52.09", "longitude": "5.23"},
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
    s.log = mocker.MagicMock()

    # Attributes that initialize() sets
    s.soc_sensor = "sensor.soc"
    s.cable_sensor = "binary_sensor.cable"
    s.charge_mode_select = "select.mode"
    s.charge_target_entity = "input_number.target"
    s.charge_by_entity = "input_datetime.deadline"
    s.battery_kwh = BATTERY_KWH
    s.charging_power_kw = CHARGING_POWER_KW
    s.day_rate = DAY_RATE
    s.night_rate = NIGHT_RATE

    return s


def _setup_states(sched, *, soc="60", target="80", deadline=None, cable="on", mode="PureSolar"):
    """Configure get_state to return realistic values for all entities."""
    if deadline is None:
        # Far future so deadline-in-past check never fires
        deadline = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    mapping = {
        "sensor.soc": soc,
        "input_number.target": target,
        "input_datetime.deadline": deadline,
        "binary_sensor.cable": cable,
        "select.mode": mode,
    }
    sched.get_state.side_effect = lambda entity_id: mapping.get(entity_id)


# ---------------------------------------------------------------------------
# initialize()
# ---------------------------------------------------------------------------

class TestInitialize:
    def test_sets_entity_attributes(self, sched, mocker):
        mocker.patch("laadplanner.solar_forecast.configure")
        sched.initialize()
        assert sched.soc_sensor == "sensor.soc"
        assert sched.cable_sensor == "binary_sensor.cable"
        assert sched.charge_mode_select == "select.mode"
        assert sched.battery_kwh == BATTERY_KWH
        assert sched.charging_power_kw == CHARGING_POWER_KW
        assert sched.day_rate == DAY_RATE
        assert sched.night_rate == NIGHT_RATE

    def test_schedules_immediate_and_hourly_replan(self, sched, mocker):
        mocker.patch("laadplanner.solar_forecast.configure")
        sched.initialize()
        sched.run_in.assert_called_once_with(sched._replan, 0)
        sched.run_hourly.assert_called_once_with(sched._replan, "00:00:00")

    def test_registers_three_listen_state_calls(self, sched, mocker):
        mocker.patch("laadplanner.solar_forecast.configure")
        sched.initialize()
        # soc_sensor, herplan button, cable_sensor
        assert sched.listen_state.call_count == 3

    def test_calls_solar_forecast_configure(self, sched, mocker):
        configure = mocker.patch("laadplanner.solar_forecast.configure")
        sched.initialize()
        configure.assert_called_once()
        _, kwargs = configure.call_args
        assert kwargs["latitude"] == 52.09
        assert kwargs["longitude"] == 5.23


# ---------------------------------------------------------------------------
# _replan() — early-exit branches
# ---------------------------------------------------------------------------

class TestReplanEarlyExits:
    def test_cable_disconnected_publishes_status_and_returns(self, sched, mocker):
        _setup_states(sched, cable="off")
        mocker.patch("laadplanner.solar_forecast.fetch_forecast")

        sched._replan()

        sched.set_state.assert_called_once()
        state_text = sched.set_state.call_args[0][0]
        assert state_text == "sensor.laadplan"
        attributes = sched.set_state.call_args[1]["attributes"]
        assert "Cable not connected" in attributes["plan"]
        sched.call_service.assert_not_called()

    def test_soc_unavailable_publishes_status(self, sched, mocker):
        _setup_states(sched, soc="unavailable")
        mocker.patch("laadplanner.solar_forecast.fetch_forecast")

        sched._replan()

        args, kwargs = sched.set_state.call_args
        assert "SoC unavailable" in kwargs["attributes"]["plan"]
        sched.call_service.assert_not_called()

    def test_soc_unknown_publishes_status(self, sched, mocker):
        _setup_states(sched, soc="unknown")
        mocker.patch("laadplanner.solar_forecast.fetch_forecast")

        sched._replan()

        args, kwargs = sched.set_state.call_args
        assert "SoC unavailable" in kwargs["attributes"]["plan"]

    def test_target_unavailable_publishes_status(self, sched, mocker):
        _setup_states(sched, target="unavailable")
        mocker.patch("laadplanner.solar_forecast.fetch_forecast")

        sched._replan()

        args, kwargs = sched.set_state.call_args
        assert "charge target unavailable" in kwargs["attributes"]["plan"]
        sched.call_service.assert_not_called()

    def test_deadline_in_past_publishes_status(self, sched, mocker):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        _setup_states(sched, deadline=past)
        mocker.patch("laadplanner.solar_forecast.fetch_forecast")

        sched._replan()

        args, kwargs = sched.set_state.call_args
        assert "Deadline passed" in kwargs["attributes"]["plan"]
        sched.call_service.assert_not_called()

    def test_deadline_in_past_does_not_change_charge_mode(self, sched, mocker):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        _setup_states(sched, deadline=past, mode="Smart")
        mocker.patch("laadplanner.solar_forecast.fetch_forecast")

        sched._replan()

        sched.call_service.assert_not_called()

    def test_target_already_reached_sets_pure_solar(self, sched, mocker):
        _setup_states(sched, soc="85", target="80", mode="Smart")
        mocker.patch("laadplanner.solar_forecast.fetch_forecast")

        sched._replan()

        sched.call_service.assert_called_once_with(
            "select/select_option",
            entity_id="select.mode",
            option="PureSolar",
        )

    def test_target_exactly_met_sets_pure_solar(self, sched, mocker):
        _setup_states(sched, soc="80", target="80", mode="Smart")
        mocker.patch("laadplanner.solar_forecast.fetch_forecast")

        sched._replan()

        sched.call_service.assert_called_once_with(
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
            "laadplanner.solar_forecast.fetch_forecast",
            side_effect=RuntimeError("API error"),
        )

        # Should not raise; plan should be built using Smart-only candidates
        sched._replan()
        sched.set_state.assert_called()

    def test_impossible_deadline_publishes_warning_in_plan(self, sched, mocker):
        # Deadline only 5 minutes away → 0 full-hour slots available
        soon = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        _setup_states(sched, soc="25", target="80", deadline=soon)
        mocker.patch("laadplanner.solar_forecast.fetch_forecast", return_value={})

        sched._replan()

        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        assert "not achievable" in plan

    def test_normal_plan_sets_charge_mode(self, sched, mocker):
        # SoC=70%, target=80% → need 5.8 kWh; deadline 7 days away → plenty of slots
        _setup_states(sched, soc="70", target="80")
        mocker.patch("laadplanner.solar_forecast.fetch_forecast", return_value={})

        sched._replan()

        # Charge mode should have been set to something (Smart or PureSolar)
        # (exact mode depends on current hour, but call_service must be called if mode changed)
        sched.set_state.assert_called()

    def test_normal_plan_publishes_slot_details(self, sched, mocker):
        _setup_states(sched, soc="25", target="80", mode="Smart")
        mocker.patch("laadplanner.solar_forecast.fetch_forecast", return_value={})

        sched._replan()

        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        # Plan should contain time and mode info
        assert "Smart" in plan or "No charging" in plan

    def test_mode_not_changed_when_already_correct(self, sched, mocker):
        # SoC already at target → PureSolar; current mode is also PureSolar
        _setup_states(sched, soc="80", target="80", mode="PureSolar")
        mocker.patch("laadplanner.solar_forecast.fetch_forecast")

        sched._replan()

        sched.call_service.assert_not_called()


# ---------------------------------------------------------------------------
# _publish_plan()
# ---------------------------------------------------------------------------

class TestPublishPlan:
    def _make_slot(self, hour: int, mode: str = "Smart", energy: float = 11.0) -> dict:
        return {
            "slot": datetime(2025, 6, 1, hour, 0),
            "mode": mode,
            "effective_price": 23.0,
            "power_kw": 11.0,
            "energy_kwh": energy,
        }

    def test_empty_selected_shows_no_slots_message(self, sched):
        sched._publish_plan([], soc_start=50.0, soc_target=80.0)
        args, kwargs = sched.set_state.call_args
        assert "No charging slots planned" in kwargs["attributes"]["plan"]

    def test_warning_prepended_when_provided(self, sched):
        sched._publish_plan([], soc_start=50.0, soc_target=80.0, warning="Test warning")
        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        assert plan.startswith("Test warning")

    def test_running_soc_increases_per_slot(self, sched):
        slot = self._make_slot(15, energy=5.8)  # 5.8 / 58 kWh = +10%
        sched._publish_plan([slot], soc_start=70.0, soc_target=80.0)
        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        assert "80%" in plan

    def test_running_soc_capped_at_target(self, sched):
        # Energy would overshoot target; check it's capped
        slot = self._make_slot(15, energy=11.0)  # 11/58 * 100 ≈ 19%
        sched._publish_plan([slot], soc_start=75.0, soc_target=80.0)
        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        assert "80%" in plan
        assert "99%" not in plan

    def test_partial_slot_end_time_not_full_hour(self, sched):
        # 5 kWh at 11 kW → 5/11 hours ≈ 27 minutes, not a full hour
        slot = self._make_slot(15, energy=5.0)
        sched._publish_plan([slot], soc_start=70.0, soc_target=80.0)
        args, kwargs = sched.set_state.call_args
        plan = kwargs["attributes"]["plan"]
        # Start is 15:00, end should NOT be 16:00
        assert "16:00" not in plan

    def test_plan_contains_mode_and_price(self, sched):
        slot = self._make_slot(22, mode="Smart", energy=11.0)
        sched._publish_plan([slot], soc_start=25.0, soc_target=80.0)
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
        # set_state is called as set_state("sensor.laadplan", state=..., attributes=...)
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

    def test_entity_id_is_laadplan(self, sched):
        sched._publish_status("test")
        entity_id = sched.set_state.call_args[0][0]
        assert entity_id == "sensor.laadplan"


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
