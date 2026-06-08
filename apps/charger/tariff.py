"""
tariff.py
=========
Parse evcc-style fixed tariff configuration into a weekly rate table.

Only the 'fixed' tariff type with optional time zones is supported.
A zone may restrict itself to certain hours (`hours`) and/or certain
weekdays (`days`). Prices in the config are in EUR/kWh; the returned
rates are in ct/kWh.
"""

from dataclasses import dataclass
from datetime import datetime

# evcc three-letter day abbreviations → datetime.weekday() numbers (Mon=0 … Sun=6).
_DAY_NUMBERS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


@dataclass(frozen=True)
class TariffSchedule:
    """A full week of hourly rates, keyed by (weekday, hour) in ct/kWh.

    weekday follows datetime.weekday(): Monday=0 … Sunday=6.
    """

    rates: dict[tuple[int, int], float]

    def rate_for(self, when: datetime) -> float:
        """Return the rate in ct/kWh that applies at the given moment."""
        return self.rates[(when.weekday(), when.hour)]


def parse_tariff(grid_config: dict) -> TariffSchedule:
    """
    Build a weekly rate table from an evcc grid config.

    Expected config shape (the 'grid' section under 'tariff'):
      type: fixed
      price: 0.27         # EUR/kWh — default price for all hours
      zones:              # optional list of overrides
        - days: "Mon-Fri" # optional; weekdays the zone applies to
          hours: "22-6"   # optional; 24-h range, may cross midnight
          price: 0.23     # EUR/kWh for these hours/days
        - days: "Sat,Sun" # whole-weekend off-peak (no `hours` → all hours)
          price: 0.23

    A zone without `days` applies to every weekday; a zone without `hours`
    applies to every hour. If multiple zones cover the same (day, hour) cell,
    the first zone wins (evcc spec).
    """
    if grid_config.get("type") != "fixed":
        raise ValueError(
            f"Unsupported tariff type: {grid_config.get('type')!r}. Only 'fixed' is supported."
        )

    default_ct = float(grid_config["price"]) * 100
    rates: dict[tuple[int, int], float] = {
        (day, hour): default_ct for day in range(7) for hour in range(24)
    }
    assigned: set[tuple[int, int]] = set()

    for zone in grid_config.get("zones", []):
        zone_ct = float(zone["price"]) * 100
        days = _parse_days(zone["days"]) if zone.get("days") else range(7)
        hours = _parse_hour_range(zone["hours"]) if zone.get("hours") else range(24)
        for day in days:
            for hour in hours:
                cell = (day, hour)
                if cell not in assigned:  # first matching zone wins
                    rates[cell] = zone_ct
                    assigned.add(cell)

    return TariffSchedule(rates)


def _parse_hour_range(hour_range: str) -> list[int]:
    """
    Parse an evcc hour-range string into a list of hours.

    "6-22"  → [6, 7, ..., 21]          (daytime, does not cross midnight)
    "22-6"  → [22, 23, 0, 1, 2, 3, 4, 5]  (nighttime, crosses midnight)
    """
    start_str, end_str = hour_range.split("-")
    start, end = int(start_str), int(end_str)
    if start < end:
        return list(range(start, end))
    # Wraps around midnight
    return list(range(start, 24)) + list(range(0, end))


def _parse_days(days_spec: str) -> list[int]:
    """
    Parse an evcc day specification into weekday numbers (Mon=0 … Sun=6).

    Accepts comma-separated tokens, each a single day or an inclusive range:
      "Mon-Fri"  → [0, 1, 2, 3, 4]
      "Sat,Sun"  → [5, 6]
      "Fri-Mon"  → [4, 5, 6, 0]   (wraps across the week boundary)
    Day names are the evcc abbreviations (Mon…Sun), case-insensitive.
    """
    days: list[int] = []
    for token in days_spec.split(","):
        token = token.strip()
        if "-" in token:
            start_str, end_str = token.split("-")
            start, end = _day_number(start_str), _day_number(end_str)
            if start <= end:
                days.extend(range(start, end + 1))
            else:  # wraps across the week boundary (e.g. Fri-Mon)
                days.extend(list(range(start, 7)) + list(range(0, end + 1)))
        else:
            days.append(_day_number(token))
    return days


def _day_number(name: str) -> int:
    """Map a day name (Mon…Sun, case-insensitive) to its weekday number."""
    key = name.strip().lower()[:3]
    try:
        return _DAY_NUMBERS[key]
    except KeyError:
        raise ValueError(
            f"Unknown day name: {name!r}. Use Mon, Tue, Wed, Thu, Fri, Sat, or Sun."
        ) from None
