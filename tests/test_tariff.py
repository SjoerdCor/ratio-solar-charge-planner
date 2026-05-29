"""Unit tests for tariff.py — pure logic, no mocks required."""

import pytest

from charger.tariff import _parse_hour_range, parse_tariff


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

    def test_raises_when_zone_has_days(self):
        with pytest.raises(ValueError, match="'days'"):
            parse_tariff({
                "type": "fixed",
                "price": 0.27,
                "zones": [{"days": "Mo-Fr", "hours": "22-6", "price": 0.23}],
            })

    def test_no_zones_all_hours_get_default(self):
        rates = parse_tariff({"type": "fixed", "price": 0.27})
        assert len(rates) == 24
        assert all(v == pytest.approx(27.0) for v in rates.values())

    def test_price_converted_from_eur_to_ct(self):
        rates = parse_tariff({"type": "fixed", "price": 0.30})
        assert rates[0] == pytest.approx(30.0)

    def test_zone_overrides_affected_hours(self):
        rates = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [{"hours": "6-22", "price": 0.30}],
        })
        for h in range(6, 22):
            assert rates[h] == pytest.approx(30.0)
        for h in list(range(0, 6)) + list(range(22, 24)):
            assert rates[h] == pytest.approx(27.0)

    def test_midnight_crossing_zone(self):
        rates = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [{"hours": "22-6", "price": 0.23}],
        })
        for h in list(range(0, 6)) + list(range(22, 24)):
            assert rates[h] == pytest.approx(23.0)
        for h in range(6, 22):
            assert rates[h] == pytest.approx(27.0)

    def test_first_zone_wins_when_hours_overlap(self):
        rates = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [
                {"hours": "6-22", "price": 0.30},
                {"hours": "10-14", "price": 0.20},
            ],
        })
        for h in range(10, 14):
            assert rates[h] == pytest.approx(30.0)

    def test_zone_without_hours_key_applies_to_all_hours(self):
        rates = parse_tariff({
            "type": "fixed",
            "price": 0.27,
            "zones": [{"price": 0.20}],
        })
        assert all(v == pytest.approx(20.0) for v in rates.values())

    def test_empty_zones_list_uses_default(self):
        rates = parse_tariff({"type": "fixed", "price": 0.27, "zones": []})
        assert all(v == pytest.approx(27.0) for v in rates.values())

    def test_returns_all_24_hours(self):
        rates = parse_tariff({"type": "fixed", "price": 0.27})
        assert set(rates.keys()) == set(range(24))


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
