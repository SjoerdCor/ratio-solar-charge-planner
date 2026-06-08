"""Unit tests for tariff.py — pure logic, no mocks required."""

from datetime import datetime

import pytest

from charger.tariff import (
    TariffSchedule,
    _day_number,
    _parse_days,
    _parse_hour_range,
    parse_tariff,
)

# Reference dates for querying a schedule by weekday.
MONDAY = datetime(2026, 6, 8)
SATURDAY = datetime(2026, 6, 13)
SUNDAY = datetime(2026, 6, 14)


def _at(day: datetime, hour: int) -> datetime:
    return day.replace(hour=hour)


# ---------------------------------------------------------------------------
# parse_tariff
# ---------------------------------------------------------------------------

class TestParseTariff:
    def test_raises_for_unsupported_type(self):
        with pytest.raises(ValueError, match="Unsupported tariff type"):
            parse_tariff({"type": "dynamic", "price": 0.27})

    def test_raises_when_type_missing(self):
        with pytest.raises(ValueError):
            parse_tariff({"price": 0.27})

    def test_no_zones_all_hours_get_default(self):
        sched = parse_tariff({"type": "fixed", "price": 0.27})
        assert all(v == pytest.approx(27.0) for v in sched.rates.values())

    def test_price_converted_from_eur_to_ct(self):
        sched = parse_tariff({"type": "fixed", "price": 0.30})
        assert sched.rate_for(_at(MONDAY, 0)) == pytest.approx(30.0)

    def test_zone_overrides_affected_hours(self):
        sched = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [{"hours": "6-22", "price": 0.30}],
        })
        for h in range(6, 22):
            assert sched.rate_for(_at(MONDAY, h)) == pytest.approx(30.0)
        for h in list(range(0, 6)) + list(range(22, 24)):
            assert sched.rate_for(_at(MONDAY, h)) == pytest.approx(27.0)

    def test_midnight_crossing_zone(self):
        sched = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [{"hours": "22-6", "price": 0.23}],
        })
        for h in list(range(0, 6)) + list(range(22, 24)):
            assert sched.rate_for(_at(MONDAY, h)) == pytest.approx(23.0)
        for h in range(6, 22):
            assert sched.rate_for(_at(MONDAY, h)) == pytest.approx(27.0)

    def test_first_zone_wins_when_hours_overlap(self):
        sched = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [
                {"hours": "6-22", "price": 0.30},
                {"hours": "10-14", "price": 0.20},
            ],
        })
        for h in range(10, 14):
            assert sched.rate_for(_at(MONDAY, h)) == pytest.approx(30.0)

    def test_zone_without_hours_key_applies_to_all_hours(self):
        sched = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [{"price": 0.20}],
        })
        assert all(v == pytest.approx(20.0) for v in sched.rates.values())

    def test_empty_zones_list_uses_default(self):
        sched = parse_tariff({"type": "fixed", "price": 0.27, "zones": []})
        assert all(v == pytest.approx(27.0) for v in sched.rates.values())

    def test_returns_full_week_grid(self):
        sched = parse_tariff({"type": "fixed", "price": 0.27})
        assert set(sched.rates.keys()) == {(d, h) for d in range(7) for h in range(24)}

    # --- day-of-week support ------------------------------------------------

    def test_zone_restricted_to_weekdays_leaves_weekend_at_default(self):
        sched = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [{"days": "Mon-Fri", "hours": "22-6", "price": 0.23}],
        })
        # Night rate on a weekday...
        assert sched.rate_for(_at(MONDAY, 2)) == pytest.approx(23.0)
        # ...but the same hour on Saturday keeps the default.
        assert sched.rate_for(_at(SATURDAY, 2)) == pytest.approx(27.0)

    def test_whole_weekend_zone_applies_all_hours(self):
        sched = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [{"days": "Sat,Sun", "price": 0.23}],
        })
        for h in range(24):
            assert sched.rate_for(_at(SATURDAY, h)) == pytest.approx(23.0)
            assert sched.rate_for(_at(SUNDAY, h)) == pytest.approx(23.0)
        # Weekday is untouched.
        assert sched.rate_for(_at(MONDAY, 12)) == pytest.approx(27.0)

    def test_dutch_off_peak_weekday_nights_plus_full_weekend(self):
        # The motivating case: dal at night on weekdays AND all weekend long.
        sched = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [
                {"days": "Mon-Fri", "hours": "23-7", "price": 0.23},
                {"days": "Sat,Sun", "price": 0.23},
            ],
        })
        assert sched.rate_for(_at(MONDAY, 3)) == pytest.approx(23.0)   # weekday night
        assert sched.rate_for(_at(MONDAY, 12)) == pytest.approx(27.0)  # weekday day
        assert sched.rate_for(_at(SATURDAY, 12)) == pytest.approx(23.0)  # weekend midday
        assert sched.rate_for(_at(SUNDAY, 18)) == pytest.approx(23.0)    # weekend evening

    def test_zone_without_days_applies_to_every_weekday(self):
        sched = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [{"hours": "22-6", "price": 0.23}],
        })
        for day in (MONDAY, SATURDAY, SUNDAY):
            assert sched.rate_for(_at(day, 2)) == pytest.approx(23.0)

    def test_raises_for_unknown_day_name(self):
        with pytest.raises(ValueError, match="Unknown day name"):
            parse_tariff({
                "type": "fixed",
                "price": 0.27,
                "zones": [{"days": "Funday", "price": 0.23}],
            })


# ---------------------------------------------------------------------------
# TariffSchedule
# ---------------------------------------------------------------------------

class TestTariffSchedule:
    def test_rate_for_uses_weekday_and_hour(self):
        sched = TariffSchedule({(d, h): float(d * 100 + h) for d in range(7) for h in range(24)})
        # Monday=0, hour 9 → 9; Sunday=6, hour 9 → 609.
        assert sched.rate_for(_at(MONDAY, 9)) == pytest.approx(9.0)
        assert sched.rate_for(_at(SUNDAY, 9)) == pytest.approx(609.0)


# ---------------------------------------------------------------------------
# _parse_hour_range
# ---------------------------------------------------------------------------

class TestParseHourRange:
    def test_normal_range_daytime(self):
        assert _parse_hour_range("6-22") == list(range(6, 22))

    def test_normal_range_does_not_include_end(self):
        result = _parse_hour_range("6-22")
        assert 22 not in result
        assert 6 in result

    def test_midnight_crossing_range(self):
        result = _parse_hour_range("22-6")
        assert result == [22, 23, 0, 1, 2, 3, 4, 5]

    def test_midnight_crossing_does_not_include_end(self):
        result = _parse_hour_range("22-6")
        assert 6 not in result
        assert 22 in result

    def test_single_hour_span(self):
        assert _parse_hour_range("14-15") == [14]


# ---------------------------------------------------------------------------
# _parse_days
# ---------------------------------------------------------------------------

class TestParseDays:
    def test_single_day(self):
        assert _parse_days("Mon") == [0]

    def test_inclusive_range(self):
        assert _parse_days("Mon-Fri") == [0, 1, 2, 3, 4]

    def test_comma_list(self):
        assert _parse_days("Sat,Sun") == [5, 6]

    def test_range_includes_end(self):
        assert _parse_days("Sat-Sun") == [5, 6]

    def test_wraps_across_week_boundary(self):
        assert _parse_days("Fri-Mon") == [4, 5, 6, 0]

    def test_case_insensitive_and_whitespace_tolerant(self):
        assert _parse_days(" mon , TUE ") == [0, 1]

    def test_accepts_long_names(self):
        assert _parse_days("Monday-Friday") == [0, 1, 2, 3, 4]


class TestDayNumber:
    def test_known_days(self):
        assert _day_number("Mon") == 0
        assert _day_number("Sun") == 6

    def test_raises_for_unknown(self):
        with pytest.raises(ValueError, match="Unknown day name"):
            _day_number("Xyz")
