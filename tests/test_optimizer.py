"""Unit tests for optimizer.py — pure logic, no mocks required."""

from datetime import datetime, timedelta

import pytest

from charger.optimizer import (
    MIN_CHARGING_KW,
    SMART_SOLAR_MIN_PV_KWH,
    _best_per_slot,
    build_candidates,
    max_available_energy,
    mode_for_current_slot,
    select_slots,
    select_slots_forced,
)

NIGHT = 23.0
DAY = 27.0
POWER = 11.0
HOURLY_RATES = {h: (NIGHT if h < 6 or h >= 22 else DAY) for h in range(24)}

# Fixed reference times used across tests so results are deterministic.
NOW = datetime(2025, 6, 1, 14, 0)
NEXT_HOUR = datetime(2025, 6, 1, 15, 0)
# Solar forecast key for the slot starting at NOW (Forecast.Solar marks END of period).
NOW_SOLAR_KEY = "2025-06-01 15:00:00"


# ---------------------------------------------------------------------------
# build_candidates
# ---------------------------------------------------------------------------

class TestBuildCandidates:
    def test_deadline_in_past_returns_empty(self):
        deadline = NOW - timedelta(hours=1)
        assert build_candidates(NOW, deadline, {}, POWER, HOURLY_RATES) == []

    def test_deadline_equal_to_now_returns_empty(self):
        assert build_candidates(NOW, NOW, {}, POWER, HOURLY_RATES) == []

    def test_deadline_one_hour_away_produces_one_smart_slot(self):
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, {}, POWER, HOURLY_RATES)
        assert len(result) == 1
        assert result[0]["mode"] == "Smart"
        assert result[0]["slot"] == NOW

    def test_two_hours_produces_two_smart_slots(self):
        deadline = NOW + timedelta(hours=2)
        result = build_candidates(NOW, deadline, {}, POWER, HOURLY_RATES)
        assert len(result) == 2

    def test_first_slot_is_current_hour_with_partial_energy(self):
        now_with_minutes = datetime(2025, 6, 1, 14, 45)  # 15 minutes left in current hour
        deadline = datetime(2025, 6, 1, 16, 0)
        result = build_candidates(now_with_minutes, deadline, {}, POWER, HOURLY_RATES)
        first = next(c for c in result if c["slot"] == datetime(2025, 6, 1, 14, 0))
        assert first["energy_kwh"] == pytest.approx(POWER * 15 / 60)

    def test_first_slot_start_time_equals_now(self):
        now_with_minutes = datetime(2025, 6, 1, 14, 45)
        deadline = datetime(2025, 6, 1, 16, 0)
        result = build_candidates(now_with_minutes, deadline, {}, POWER, HOURLY_RATES)
        first = next(c for c in result if c["slot"] == datetime(2025, 6, 1, 14, 0))
        assert first["start_time"] == now_with_minutes

    def test_full_slot_start_time_equals_slot(self):
        now_on_hour = datetime(2025, 6, 1, 14, 0)
        deadline = datetime(2025, 6, 1, 16, 0)
        result = build_candidates(now_on_hour, deadline, {}, POWER, HOURLY_RATES)
        first = next(c for c in result if c["slot"] == now_on_hour)
        assert first["start_time"] == now_on_hour

    def test_no_solar_produces_only_smart_candidates(self):
        deadline = NOW + timedelta(hours=3)
        result = build_candidates(NOW, deadline, {}, POWER, HOURLY_RATES)
        assert all(c["mode"] == "Smart" for c in result)

    # --- SmartSolar (SMART_SOLAR_MIN_PV_KWH <= solar < MIN_CHARGING_KW) ---

    def test_solar_between_thresholds_adds_smart_solar(self):
        # 1.0 kWh is between SMART_SOLAR_MIN_PV_KWH (0.3) and MIN_CHARGING_KW (1.4)
        solar = {NOW_SOLAR_KEY: 1.0}
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, solar, POWER, HOURLY_RATES)
        modes = {c["mode"] for c in result}
        assert "SmartSolar" in modes
        assert "Smart" in modes
        assert "PureSolar" not in modes

    def test_solar_at_min_pv_threshold_creates_smart_solar(self):
        solar = {NOW_SOLAR_KEY: SMART_SOLAR_MIN_PV_KWH}
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, solar, POWER, HOURLY_RATES)
        assert any(c["mode"] == "SmartSolar" for c in result)

    def test_solar_just_below_min_pv_threshold_no_smart_solar(self):
        solar = {NOW_SOLAR_KEY: SMART_SOLAR_MIN_PV_KWH - 0.001}
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, solar, POWER, HOURLY_RATES)
        assert not any(c["mode"] == "SmartSolar" for c in result)

    def test_smart_solar_effective_price_formula(self):
        # solar = 1.0 kWh → grid_kw = 1.4 - 1.0 = 0.4; total = 1.4 (MIN_CHARGING_KW)
        solar_kwh = 1.0
        solar = {NOW_SOLAR_KEY: solar_kwh}
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, solar, POWER, HOURLY_RATES)
        smart_solar = next(c for c in result if c["mode"] == "SmartSolar")
        grid_kw = MIN_CHARGING_KW - solar_kwh
        expected_price = (grid_kw * DAY) / MIN_CHARGING_KW
        assert smart_solar["effective_price"] == pytest.approx(expected_price)
        assert smart_solar["power_kw"] == pytest.approx(MIN_CHARGING_KW)
        assert smart_solar["energy_kwh"] == pytest.approx(MIN_CHARGING_KW)

    # --- PureSolar (solar >= MIN_CHARGING_KW) ---

    def test_solar_at_min_charging_kw_creates_pure_solar(self):
        solar = {NOW_SOLAR_KEY: MIN_CHARGING_KW}
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, solar, POWER, HOURLY_RATES)
        assert any(c["mode"] == "PureSolar" for c in result)

    def test_solar_above_min_charging_kw_creates_pure_solar_not_smart_solar(self):
        solar = {NOW_SOLAR_KEY: 1.5}
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, solar, POWER, HOURLY_RATES)
        modes = {c["mode"] for c in result}
        assert "PureSolar" in modes
        assert "SmartSolar" not in modes

    def test_pure_solar_effective_price_is_zero(self):
        solar = {NOW_SOLAR_KEY: 2.0}
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, solar, POWER, HOURLY_RATES)
        pure = next(c for c in result if c["mode"] == "PureSolar")
        assert pure["effective_price"] == pytest.approx(0.0)

    def test_pure_solar_power_equals_solar_when_below_charger_max(self):
        solar_kwh = 2.0
        solar = {NOW_SOLAR_KEY: solar_kwh}
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, solar, POWER, HOURLY_RATES)
        pure = next(c for c in result if c["mode"] == "PureSolar")
        assert pure["power_kw"] == pytest.approx(solar_kwh)
        assert pure["energy_kwh"] == pytest.approx(solar_kwh)

    def test_pure_solar_power_capped_at_charger_max(self):
        # Solar exceeds charger capacity — should be capped
        solar = {NOW_SOLAR_KEY: POWER + 5.0}
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, solar, POWER, HOURLY_RATES)
        pure = next(c for c in result if c["mode"] == "PureSolar")
        assert pure["power_kw"] == pytest.approx(POWER)

    # --- partial hours ---

    def test_partial_first_slot_energy_proportional_to_remaining_time(self):
        now = datetime(2025, 6, 1, 14, 30)  # 30 minutes left in current hour
        deadline = datetime(2025, 6, 1, 16, 0)
        result = build_candidates(now, deadline, {}, POWER, HOURLY_RATES)
        first = next(c for c in result if c["slot"] == datetime(2025, 6, 1, 14, 0))
        assert first["energy_kwh"] == pytest.approx(POWER * 0.5)

    def test_partial_last_slot_energy_proportional_to_time_before_deadline(self):
        deadline = datetime(2025, 6, 1, 15, 30)  # deadline 30 min into the 15:00 slot
        result = build_candidates(NOW, deadline, {}, POWER, HOURLY_RATES)
        last = next(c for c in result if c["slot"] == datetime(2025, 6, 1, 15, 0))
        assert last["energy_kwh"] == pytest.approx(POWER * 0.5)

    def test_full_hours_between_partial_endpoints_have_full_energy(self):
        now = datetime(2025, 6, 1, 14, 30)
        deadline = datetime(2025, 6, 1, 17, 30)
        result = build_candidates(now, deadline, {}, POWER, HOURLY_RATES)
        mid = next(c for c in result if c["slot"] == datetime(2025, 6, 1, 15, 0) and c["mode"] == "Smart")
        assert mid["energy_kwh"] == pytest.approx(POWER)

    def test_power_kw_unchanged_for_partial_slot(self):
        # power_kw reflects charger rate, energy_kwh reflects actual delivery
        now = datetime(2025, 6, 1, 14, 30)
        deadline = datetime(2025, 6, 1, 15, 0)
        result = build_candidates(now, deadline, {}, POWER, HOURLY_RATES)
        assert result[0]["power_kw"] == pytest.approx(POWER)
        assert result[0]["energy_kwh"] == pytest.approx(POWER * 0.5)

    # --- shared ---

    def test_solar_key_must_be_slot_plus_one_hour(self):
        # Key at slot time (14:00) should NOT trigger any solar mode — key must be 15:00
        wrong_key_solar = {"2025-06-01 14:00:00": 2.0}
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, wrong_key_solar, POWER, HOURLY_RATES)
        assert not any(c["mode"] in ("SmartSolar", "PureSolar") for c in result)

    def test_smart_candidate_uses_charging_power(self):
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, {}, POWER, HOURLY_RATES)
        smart = result[0]
        assert smart["power_kw"] == POWER
        assert smart["energy_kwh"] == POWER

    def test_day_rate_applied_at_slot_in_day(self):
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, {}, POWER, HOURLY_RATES)
        assert result[0]["effective_price"] == DAY

    def test_night_rate_applied_at_slot_at_night(self):
        now_night = datetime(2025, 6, 1, 22, 0)
        deadline = datetime(2025, 6, 1, 23, 0)
        result = build_candidates(now_night, deadline, {}, POWER, HOURLY_RATES)
        assert result[0]["effective_price"] == NIGHT

    def test_candidate_keys_present(self):
        deadline = NOW + timedelta(hours=1)
        result = build_candidates(NOW, deadline, {}, POWER, HOURLY_RATES)
        required_keys = {"slot", "mode", "effective_price", "power_kw", "energy_kwh"}
        for c in result:
            assert required_keys <= c.keys()


# ---------------------------------------------------------------------------
# _best_per_slot
# ---------------------------------------------------------------------------

class TestBestPerSlot:
    def test_empty_returns_empty(self):
        assert _best_per_slot([]) == {}

    def test_single_candidate_returned(self):
        c = {"slot": NEXT_HOUR, "effective_price": 25.0, "mode": "Smart"}
        result = _best_per_slot([c])
        assert result == {NEXT_HOUR: c}

    def test_keeps_cheapest_when_two_candidates_same_slot(self):
        slot = NEXT_HOUR
        expensive = {"slot": slot, "effective_price": 30.0, "mode": "Smart"}
        cheap = {"slot": slot, "effective_price": 20.0, "mode": "SmartSolar"}
        result = _best_per_slot([expensive, cheap])
        assert result[slot] == cheap

    def test_different_slots_kept_separately(self):
        slot1 = datetime(2025, 6, 1, 15, 0)
        slot2 = datetime(2025, 6, 1, 16, 0)
        c1 = {"slot": slot1, "effective_price": 25.0}
        c2 = {"slot": slot2, "effective_price": 20.0}
        result = _best_per_slot([c1, c2])
        assert len(result) == 2
        assert result[slot1] == c1
        assert result[slot2] == c2

    def test_equal_price_keeps_first(self):
        c1 = {"slot": NEXT_HOUR, "effective_price": 25.0, "mode": "Smart"}
        c2 = {"slot": NEXT_HOUR, "effective_price": 25.0, "mode": "SmartSolar"}
        result = _best_per_slot([c1, c2])
        assert result[NEXT_HOUR] == c1


# ---------------------------------------------------------------------------
# select_slots
# ---------------------------------------------------------------------------

def _slot_at(hour: int) -> datetime:
    return datetime(2025, 6, 1, hour, 0)


def _candidate(hour: int, price: float, energy: float = 11.0, mode: str = "Smart") -> dict:
    return {
        "slot": _slot_at(hour),
        "mode": mode,
        "effective_price": price,
        "power_kw": 11.0,
        "energy_kwh": energy,
    }


class TestSelectSlots:
    def test_empty_candidates_returns_empty(self):
        assert select_slots([], 10.0) == []

    def test_zero_energy_needed_returns_empty(self):
        assert select_slots([_candidate(15, 25.0)], 0.0) == []

    def test_selects_single_cheapest_slot(self):
        candidates = [_candidate(15, 30.0), _candidate(16, 20.0)]
        result = select_slots(candidates, 11.0)
        assert len(result) == 1
        assert result[0]["slot"] == _slot_at(16)

    def test_selects_multiple_slots_in_chronological_order(self):
        candidates = [_candidate(15, 27.0), _candidate(16, 23.0), _candidate(17, 25.0)]
        result = select_slots(candidates, 30.0)
        slots = [c["slot"] for c in result]
        assert slots == sorted(slots)

    def test_partial_last_slot_capped_to_remaining_energy(self):
        result = select_slots([_candidate(15, 25.0, energy=11.0)], 5.0)
        assert len(result) == 1
        assert result[0]["energy_kwh"] == pytest.approx(5.0)

    def test_original_candidate_not_mutated_by_partial_fill(self):
        original = _candidate(15, 25.0, energy=11.0)
        select_slots([original], 5.0)
        assert original["energy_kwh"] == 11.0

    def test_total_energy_matches_need_exactly(self):
        candidates = [_candidate(h, 25.0) for h in range(14, 18)]
        energy_needed = 25.0
        result = select_slots(candidates, energy_needed)
        total = sum(c["energy_kwh"] for c in result)
        assert total == pytest.approx(energy_needed)

    def test_prefers_earlier_slot_when_prices_equal(self):
        # Both cost 25 ct/kWh; the earlier slot should be picked first
        candidates = [_candidate(16, 25.0), _candidate(14, 25.0)]
        result = select_slots(candidates, 11.0)
        assert result[0]["slot"] == _slot_at(14)

    def test_stops_once_energy_satisfied(self):
        candidates = [_candidate(h, 23.0) for h in range(14, 18)]
        result = select_slots(candidates, 11.0)
        assert len(result) == 1

    def test_can_fill_across_multiple_slots(self):
        candidates = [_candidate(22, 23.0), _candidate(23, 23.0)]
        result = select_slots(candidates, 15.0)
        total = sum(c["energy_kwh"] for c in result)
        assert total == pytest.approx(15.0)

    def test_smart_solar_preferred_over_smart_same_slot_if_cheaper(self):
        slot = _slot_at(15)
        smart = {"slot": slot, "mode": "Smart", "effective_price": 27.0, "power_kw": 11.0, "energy_kwh": 11.0}
        smart_solar = {"slot": slot, "mode": "SmartSolar", "effective_price": 18.0, "power_kw": 1.4, "energy_kwh": 1.4}
        result = select_slots([smart, smart_solar], 1.4)
        assert result[0]["mode"] == "SmartSolar"


class TestSelectSlotsWithMinimum:
    """Tests for the immediate_kwh parameter (minimum SoC feature)."""

    def test_zero_immediate_behaves_identically(self):
        candidates = [_candidate(15, DAY), _candidate(22, NIGHT)]
        assert select_slots(candidates, 11.0, 0.0) == select_slots(candidates, 11.0)

    def test_immediate_forces_earliest_smart_slot_regardless_of_price(self):
        # Without immediate, optimizer would pick 22:00 (cheaper night rate).
        # With immediate, 14:00 is forced first.
        candidates = [_candidate(14, DAY), _candidate(22, NIGHT)]
        result = select_slots(candidates, 11.0, immediate_kwh=11.0)
        assert len(result) == 1
        assert result[0]["slot"] == _slot_at(14)
        assert result[0]["mode"] == "Smart"

    def test_immediate_uses_smart_even_when_pure_solar_available_in_same_slot(self):
        slot = _slot_at(14)
        candidates = [
            {"slot": slot, "mode": "PureSolar", "effective_price": 0.0, "power_kw": 3.0, "energy_kwh": 3.0},
            {"slot": slot, "mode": "Smart", "effective_price": DAY, "power_kw": 11.0, "energy_kwh": 11.0},
            _candidate(15, DAY),
        ]
        result = select_slots(candidates, 14.0, immediate_kwh=5.0)
        at_14 = next(c for c in result if c["slot"] == slot)
        assert at_14["mode"] == "Smart"
        assert at_14["energy_kwh"] == pytest.approx(5.0)

    def test_immediate_partial_slot_blocks_rest_of_that_hour_for_optimization(self):
        # Force 5 kWh at 14:00 (partial). Remaining 10 kWh must come from 15:00,
        # not from the rest of the 14:00 slot.
        candidates = [_candidate(14, DAY), _candidate(15, NIGHT)]
        result = select_slots(candidates, 15.0, immediate_kwh=5.0)
        forced = next(c for c in result if c["slot"] == _slot_at(14))
        assert forced["energy_kwh"] == pytest.approx(5.0)
        rest = next(c for c in result if c["slot"] == _slot_at(15))
        assert rest["energy_kwh"] == pytest.approx(10.0)

    def test_immediate_covers_all_energy_selects_only_forced_slots(self):
        # immediate_kwh >= energy_needed_kwh: forced phase covers everything.
        candidates = [_candidate(14, DAY), _candidate(22, NIGHT)]
        result = select_slots(candidates, 5.0, immediate_kwh=11.0)
        assert len(result) == 1
        assert result[0]["slot"] == _slot_at(14)
        assert result[0]["energy_kwh"] == pytest.approx(5.0)

    def test_immediate_followed_by_cheapest_optimized_slots(self):
        # Forced: 14:00 Smart (expensive). Remaining 14 kWh optimized:
        # prefers 22:00 (cheap) over 15:00 (expensive).
        candidates = [_candidate(14, DAY), _candidate(15, DAY), _candidate(22, NIGHT)]
        result = select_slots(candidates, 25.0, immediate_kwh=11.0)
        total = sum(c["energy_kwh"] for c in result)
        assert total == pytest.approx(25.0)
        slots = [c["slot"] for c in result]
        assert _slot_at(14) in slots  # forced
        assert _slot_at(22) in slots  # cheapest available after 14 blocked

    def test_result_is_sorted_chronologically(self):
        candidates = [_candidate(h, DAY) for h in range(14, 19)]
        result = select_slots(candidates, 33.0, immediate_kwh=11.0)
        slots = [c["slot"] for c in result]
        assert slots == sorted(slots)


# ---------------------------------------------------------------------------
# max_available_energy
# ---------------------------------------------------------------------------

class TestMaxAvailableEnergy:
    def test_empty_returns_zero(self):
        assert max_available_energy([]) == pytest.approx(0.0)

    def test_single_candidate(self):
        assert max_available_energy([_candidate(15, 25.0, energy=11.0)]) == pytest.approx(11.0)

    def test_sums_best_per_slot(self):
        # slot 15: two candidates, cheapest has 5 kWh; slot 16: 11 kWh → total 16 kWh
        slot15 = _slot_at(15)
        slot16 = _slot_at(16)
        candidates = [
            {"slot": slot15, "effective_price": 30.0, "energy_kwh": 11.0},
            {"slot": slot15, "effective_price": 20.0, "energy_kwh": 5.0},
            {"slot": slot16, "effective_price": 25.0, "energy_kwh": 11.0},
        ]
        assert max_available_energy(candidates) == pytest.approx(16.0)

    def test_multiple_slots_summed(self):
        candidates = [_candidate(h, 25.0, energy=11.0) for h in range(14, 17)]
        assert max_available_energy(candidates) == pytest.approx(33.0)


# ---------------------------------------------------------------------------
# select_slots_forced
# ---------------------------------------------------------------------------

class TestSelectSlotsForced:
    def test_empty_returns_empty(self):
        assert select_slots_forced([]) == []

    def test_returns_all_slots_in_chronological_order(self):
        candidates = [_candidate(16, 27.0), _candidate(14, 23.0), _candidate(15, 25.0)]
        result = select_slots_forced(candidates)
        assert [c["slot"] for c in result] == [_slot_at(14), _slot_at(15), _slot_at(16)]

    def test_deduplicates_slots_keeping_cheapest(self):
        slot = _slot_at(15)
        candidates = [
            {"slot": slot, "effective_price": 30.0, "energy_kwh": 11.0, "mode": "Smart"},
            {"slot": slot, "effective_price": 20.0, "energy_kwh": 5.0, "mode": "SmartSolar"},
        ]
        result = select_slots_forced(candidates)
        assert len(result) == 1
        assert result[0]["effective_price"] == 20.0

    def test_returns_all_available_slots_regardless_of_energy(self):
        candidates = [_candidate(h, 25.0) for h in range(14, 20)]
        result = select_slots_forced(candidates)
        assert len(result) == 6


# ---------------------------------------------------------------------------
# mode_for_current_slot
# ---------------------------------------------------------------------------

class TestModeForCurrentSlot:
    def test_empty_list_returns_pure_solar(self):
        assert mode_for_current_slot([]) == "PureSolar"

    def test_no_matching_slot_returns_pure_solar(self):
        selected = [{"slot": _slot_at(16), "mode": "Smart"}]
        now = datetime(2025, 6, 1, 14, 30)
        assert mode_for_current_slot(selected, now) == "PureSolar"

    def test_returns_mode_for_current_hour(self):
        now = datetime(2025, 6, 1, 14, 45)
        selected = [{"slot": _slot_at(14), "mode": "SmartSolar"}]
        assert mode_for_current_slot(selected, now) == "SmartSolar"

    def test_ignores_minutes_and_seconds_in_now(self):
        now = datetime(2025, 6, 1, 14, 59, 59)
        selected = [{"slot": _slot_at(14), "mode": "Smart"}]
        assert mode_for_current_slot(selected, now) == "Smart"

    def test_picks_correct_slot_from_multiple(self):
        now = datetime(2025, 6, 1, 15, 10)
        selected = [
            {"slot": _slot_at(14), "mode": "Smart"},
            {"slot": _slot_at(15), "mode": "SmartSolar"},
            {"slot": _slot_at(16), "mode": "Smart"},
        ]
        assert mode_for_current_slot(selected, now) == "SmartSolar"

    def test_slot_in_future_returns_pure_solar(self):
        now = datetime(2025, 6, 1, 13, 0)
        selected = [{"slot": _slot_at(15), "mode": "Smart"}]
        assert mode_for_current_slot(selected, now) == "PureSolar"

    def test_slot_in_past_returns_pure_solar(self):
        now = datetime(2025, 6, 1, 16, 0)
        selected = [{"slot": _slot_at(14), "mode": "Smart"}]
        assert mode_for_current_slot(selected, now) == "PureSolar"


# ---------------------------------------------------------------------------
# End-to-end scenario tests
# ---------------------------------------------------------------------------

# Fixed "now": Sunday evening, no solar production.
_SCENARIO_NOW = datetime(2025, 6, 1, 20, 0)
_BATTERY_KWH = 77.0


def _solar_tomorrow(now: datetime, kwh_per_hour: float = 3.0) -> dict:
    """Simulated solar forecast: kwh_per_hour for slots 09:00-17:00 the next day."""
    forecast = {}
    tomorrow = (now + timedelta(days=1)).date()
    for h in range(9, 18):
        # Forecast.Solar keys mark the END of the period: slot H uses key H+1.
        key = datetime(tomorrow.year, tomorrow.month, tomorrow.day, h, 0).strftime("%Y-%m-%d %H:00:00")
        forecast[key] = kwh_per_hour
    return forecast


class TestScenarios:
    def test_small_topup_with_solar_tomorrow_plans_pure_solar_and_waits(self):
        # 75->80%, 2 days, solar 3 kWh/h tomorrow.
        # Expected: PureSolar slots tomorrow cover the need; do not charge tonight.
        now = _SCENARIO_NOW
        solar = _solar_tomorrow(now)
        energy_needed = (80 - 75) / 100 * _BATTERY_KWH

        candidates = build_candidates(now, now + timedelta(days=2), solar, POWER, HOURLY_RATES)
        selected = select_slots(candidates, energy_needed)

        assert mode_for_current_slot(selected, now) == "PureSolar"
        assert all(s["mode"] == "PureSolar" for s in selected)
        assert all(s["effective_price"] == pytest.approx(0.0) for s in selected)

    def test_urgent_charge_no_solar_switches_to_smart_immediately(self):
        # 30->80%, deadline 1 hour, no solar.
        # Expected: deadline not achievable, but mode for current hour is Smart.
        now = _SCENARIO_NOW
        energy_needed = (80 - 30) / 100 * _BATTERY_KWH

        candidates = build_candidates(now, now + timedelta(hours=1), {}, POWER, HOURLY_RATES)
        max_kwh = max_available_energy(candidates)

        assert max_kwh < energy_needed

        selected = select_slots_forced(candidates)
        assert mode_for_current_slot(selected, now) == "Smart"

    def test_solar_in_current_hour_selected_as_pure_solar_immediately(self):
        # Noon, 3 kWh solar available right now, small topup needed.
        # Expected: current slot selected as PureSolar — don't switch to Smart.
        now = datetime(2025, 6, 1, 12, 0)
        solar = {(now + timedelta(hours=1)).strftime("%Y-%m-%d %H:00:00"): 3.0}
        energy_needed = (80 - 75) / 100 * _BATTERY_KWH

        candidates = build_candidates(now, now + timedelta(days=2), solar, POWER, HOURLY_RATES)
        selected = select_slots(candidates, energy_needed)

        current_slot = next((s for s in selected if s["slot"] == now), None)
        assert current_slot is not None, "Current slot should be in the plan"
        assert current_slot["mode"] == "PureSolar"
        assert mode_for_current_slot(selected, now) == "PureSolar"

    def test_no_solar_prefers_night_rate_over_day_rate(self):
        # Evening (20:00, day rate), 2 days to charge 30->80%, no solar.
        # Expected: all selected slots at night rate (23 ct); don't charge now at 27 ct.
        now = _SCENARIO_NOW
        energy_needed = (80 - 30) / 100 * _BATTERY_KWH

        candidates = build_candidates(now, now + timedelta(days=2), {}, POWER, HOURLY_RATES)
        selected = select_slots(candidates, energy_needed)

        assert mode_for_current_slot(selected, now) == "PureSolar"
        assert all(s["effective_price"] == pytest.approx(NIGHT) for s in selected)

    def test_solar_after_deadline_forces_grid_charge_now(self):
        # Evening, large charge needed, deadline in 4 hours, solar only from tomorrow.
        # Expected: charges with Smart now — cannot wait for solar that arrives after deadline.
        now = _SCENARIO_NOW
        solar = _solar_tomorrow(now)
        energy_needed = (80 - 30) / 100 * _BATTERY_KWH

        candidates = build_candidates(now, now + timedelta(hours=4), solar, POWER, HOURLY_RATES)
        selected = select_slots(candidates, energy_needed)

        assert mode_for_current_slot(selected, now) == "Smart"
        assert not any(s["mode"] == "PureSolar" for s in selected)

    def test_deadline_before_sunrise_uses_night_not_solar(self):
        # Evening, small topup needed, deadline 07:00 tomorrow (solar starts 09:00).
        # Expected: picks cheap night slot — solar slots are excluded as they fall after deadline.
        now = _SCENARIO_NOW
        solar = _solar_tomorrow(now)
        deadline = datetime(2025, 6, 2, 7, 0)
        energy_needed = (80 - 75) / 100 * _BATTERY_KWH

        candidates = build_candidates(now, deadline, solar, POWER, HOURLY_RATES)
        selected = select_slots(candidates, energy_needed)

        assert not any(s["mode"] == "PureSolar" for s in selected)
        assert all(s["effective_price"] == pytest.approx(NIGHT) for s in selected)

    def test_partial_night_slot_covers_remainder_after_solar(self):
        # 40 kWh needed; solar covers 35 kWh (7 slots x 5 kWh); remainder via night grid.
        # Expected: 7 full PureSolar slots + 1 partial Smart slot trimmed to 5 kWh.
        now = _SCENARIO_NOW
        tomorrow = (now + timedelta(days=1)).date()
        solar = {
            datetime(tomorrow.year, tomorrow.month, tomorrow.day, h, 0).strftime("%Y-%m-%d %H:00:00"): 5.0
            for h in range(9, 16)  # keys 09:00-15:00 -> slots 08:00-14:00
        }

        candidates = build_candidates(now, now + timedelta(days=2), solar, POWER, HOURLY_RATES)
        selected = select_slots(candidates, 40.0)

        pure = [s for s in selected if s["mode"] == "PureSolar"]
        night = [s for s in selected if s["effective_price"] == pytest.approx(NIGHT)]

        assert len(pure) == 7
        assert sum(s["energy_kwh"] for s in pure) == pytest.approx(35.0)
        assert len(night) == 1
        assert night[0]["energy_kwh"] == pytest.approx(5.0)
        assert sum(s["energy_kwh"] for s in selected) == pytest.approx(40.0)
