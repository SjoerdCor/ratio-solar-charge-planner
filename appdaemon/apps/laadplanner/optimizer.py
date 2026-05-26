"""
optimizer.py
============
Pure optimisation logic — no config or HA dependencies.
Imported by both the CLI (scripts/laadplanner.py) and the AppDaemon app.

Units: power in kW, energy in kWh, tariffs in ct/kWh.
"""

from datetime import datetime, timedelta
from typing import Dict, List

GRID_POWER_KW = 1.4  # grid power drawn during SmartSolar (kW)
MIN_SOLAR_KWH = 0.3  # minimum solar production per slot to enable SmartSolar (kWh)


def rate_ct(hour: int, night_rate: float, day_rate: float) -> float:
    """Return the fixed tariff in ct/kWh for a given hour-of-day."""
    return night_rate if (hour >= 22 or hour < 6) else day_rate


def build_candidates(
    now: datetime,
    deadline: datetime,
    solar: Dict[str, float],
    charging_power_kw: float,
    night_rate: float,
    day_rate: float,
) -> List[dict]:
    """
    Build one candidate action per hour in the planning horizon.

    solar: {datetime_str: kWh} from solar_forecast.fetch_forecast().
    Returns list of dicts with keys: slot, mode, effective_price, energy_kwh.
    """
    candidates = []
    slot = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    while slot <= deadline:
        # Forecast.Solar timestamps mark the END of a period, so look up slot+1h.
        key = (slot + timedelta(hours=1)).strftime("%Y-%m-%d %H:00:00")
        solar_kwh = solar.get(key, 0.0)
        rate = rate_ct(slot.hour, night_rate, day_rate)

        if solar_kwh >= MIN_SOLAR_KWH:
            power_kw = GRID_POWER_KW + solar_kwh
            candidates.append(
                {
                    "slot": slot,
                    "mode": "SmartSolar",
                    "effective_price": (GRID_POWER_KW * rate) / power_kw,
                    "power_kw": power_kw,
                    "energy_kwh": power_kw,
                }
            )

        candidates.append(
            {
                "slot": slot,
                "mode": "Smart",
                "effective_price": rate,
                "power_kw": charging_power_kw,
                "energy_kwh": charging_power_kw,
            }
        )

        slot += timedelta(hours=1)

    return candidates


def select_slots(candidates: List[dict], energy_needed_kwh: float) -> List[dict]:
    """Pick the cheapest mode per slot, then greedily fill energy need in price order."""
    best_per_slot: Dict[datetime, dict] = {}
    for c in candidates:
        t = c["slot"]
        if (
            t not in best_per_slot
            or c["effective_price"] < best_per_slot[t]["effective_price"]
        ):
            best_per_slot[t] = c

    # The second sort key means to charge as quickly as possible for the lowest price
    # So you have some slack if there is less solar power than expected
    ranked = sorted(
        best_per_slot.values(),
        key=lambda c: (c["effective_price"], c["slot"]),
    )

    selected = []
    planned_kwh = 0.0
    for c in ranked:
        if planned_kwh >= energy_needed_kwh:
            break
        remaining = energy_needed_kwh - planned_kwh
        if remaining < c["energy_kwh"]:
            c = {**c, "energy_kwh": remaining}
        selected.append(c)
        planned_kwh += c["energy_kwh"]

    return sorted(selected, key=lambda c: c["slot"])


def mode_for_current_slot(selected: List[dict], now: datetime = None) -> str:
    """Return the planned mode for the current hour, or 'PureSolar' if nothing is scheduled."""
    if now is None:
        now = datetime.now()
    current_slot = now.replace(minute=0, second=0, microsecond=0)
    for c in selected:
        if c["slot"] == current_slot:
            return c["mode"]
    return "PureSolar"
