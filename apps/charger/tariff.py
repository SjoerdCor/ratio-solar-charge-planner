"""
tariff.py
=========
Parse evcc-style fixed tariff configuration into an hourly rate table.

Only the 'fixed' tariff type with optional time zones is supported.
Prices in the config are in EUR/kWh; the returned rates are in ct/kWh.
"""


def parse_tariff(grid_config: dict) -> dict[int, float]:
    """
    Return a mapping of {hour (0–23): rate in ct/kWh} from an evcc grid config.

    Expected config shape (the 'grid' section under 'tariff'):
      type: fixed
      price: 0.27        # EUR/kWh — default price for all hours
      zones:             # optional list of overrides
        - hours: "22-6"  # 24-h range; may cross midnight
          price: 0.23    # EUR/kWh for these hours

    If multiple zones apply to the same hour, the first zone wins (evcc spec).
    """
    if grid_config.get("type") != "fixed":
        raise ValueError(
            f"Unsupported tariff type: {grid_config.get('type')!r}. Only 'fixed' is supported."
        )

    default_ct = float(grid_config["price"]) * 100
    rates: dict[int, float] = {h: default_ct for h in range(24)}

    for zone in grid_config.get("zones", []):
        if "days" in zone:
            raise ValueError(
                "Tariff zones with 'days' are not yet supported. "
                "Remove the 'days' key or use hour ranges only."
            )
        zone_ct = float(zone["price"]) * 100
        hours_str = zone.get("hours")
        affected = _parse_hour_range(hours_str) if hours_str else list(range(24))
        for h in affected:
            # First matching zone wins — only overwrite if still at default
            if rates[h] == default_ct:
                rates[h] = zone_ct

    return rates


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
