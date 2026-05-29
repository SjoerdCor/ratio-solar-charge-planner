"""
optimizer.py
============
Pure optimisation logic — no config or HA dependencies.
Imported by both the CLI and the AppDaemon app.

Units: power in kW, energy in kWh, tariffs in ct/kWh.
"""

from datetime import datetime, timedelta
from typing import Dict, List

# Minimum power (kW) the charger needs to actually charge a car.
# SmartSolar guarantees this by drawing from the grid when PV falls short.
# PureSolar only activates when PV production reaches this threshold.
MIN_CHARGING_KW = 1.4

# Minimum hourly PV production (kWh) below which SmartSolar is not worthwhile.
SMART_SOLAR_MIN_PV_KWH = 0.3


def build_candidates(
    now: datetime,
    deadline: datetime,
    solar: Dict[str, float],
    charging_power_kw: float,
    hourly_rates: Dict[int, float],
) -> List[dict]:
    """
    Build one candidate action per hour in the planning horizon.

    solar: {datetime_str: kWh} from solar_forecast.fetch_forecast().
    hourly_rates: {hour (0–23): rate in ct/kWh} from tariff.parse_tariff().
    Returns list of dicts with keys: slot, mode, effective_price, power_kw, energy_kwh.

    SmartSolar charges at max(MIN_CHARGING_KW, solar_kwh); the grid covers any shortfall
    below MIN_CHARGING_KW. PureSolar charges at solar_kwh with no grid draw, but only
    activates when solar_kwh >= MIN_CHARGING_KW. Solar production is modelled as constant
    within each hour (v1 simplification; actual output varies with cloud cover).
    """
    candidates = []
    slot = now.replace(minute=0, second=0, microsecond=0)

    while slot < deadline:
        # Fraction of the hour actually available for charging:
        # - First slot: starts at `now`, not at slot boundary.
        # - Last slot: ends at `deadline`, not at the next full hour.
        slot_start = max(slot, now)
        slot_end = min(slot + timedelta(hours=1), deadline)
        fraction = (slot_end - slot_start).total_seconds() / 3600

        if fraction <= 0:
            slot += timedelta(hours=1)
            continue

        # Forecast.Solar timestamps mark the END of a period, so look up slot+1h.
        key = (slot + timedelta(hours=1)).strftime("%Y-%m-%d %H:00:00")
        solar_kwh = solar.get(key, 0.0)
        rate = hourly_rates[slot.hour]

        if solar_kwh >= MIN_CHARGING_KW:
            # PV covers the minimum — PureSolar, no grid draw.
            # Cap at charger max (relevant when PV array is larger than car charge rate).
            power_kw = min(solar_kwh, charging_power_kw)
            candidates.append(
                {
                    "slot": slot,
                    "mode": "PureSolar",
                    "effective_price": 0.0,
                    "power_kw": power_kw,
                    "energy_kwh": power_kw * fraction,
                }
            )
        elif solar_kwh >= SMART_SOLAR_MIN_PV_KWH:
            # PV helps but falls short — SmartSolar draws the difference from the grid.
            grid_kw = MIN_CHARGING_KW - solar_kwh
            candidates.append(
                {
                    "slot": slot,
                    "mode": "SmartSolar",
                    "effective_price": (grid_kw * rate) / MIN_CHARGING_KW,
                    "power_kw": MIN_CHARGING_KW,
                    "energy_kwh": MIN_CHARGING_KW * fraction,
                }
            )

        candidates.append(
            {
                "slot": slot,
                "mode": "Smart",
                "effective_price": rate,
                "power_kw": charging_power_kw,
                "energy_kwh": charging_power_kw * fraction,
            }
        )

        slot += timedelta(hours=1)

    return candidates


def _best_per_slot(candidates: List[dict]) -> Dict[datetime, dict]:
    """Return the cheapest candidate per time slot."""
    best: Dict[datetime, dict] = {}
    for c in candidates:
        t = c["slot"]
        if t not in best or c["effective_price"] < best[t]["effective_price"]:
            best[t] = c
    return best


def _select_cheapest(candidates: List[dict], energy_needed_kwh: float) -> List[dict]:
    """Pick the cheapest mode per slot, then greedily fill energy need in price order."""
    # Second sort key: earliest slot wins on equal price, so slack stays at the end
    # if solar delivers less than forecast.
    ranked = sorted(
        _best_per_slot(candidates).values(),
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


def select_slots(
    candidates: List[dict],
    energy_needed_kwh: float,
    immediate_kwh: float = 0.0,
) -> List[dict]:
    """Pick slots to deliver energy_needed_kwh as cheaply as possible.

    immediate_kwh: energy that must be charged first using Smart (grid) mode,
    regardless of price — used to reach a minimum SoC before optimising the rest.
    """
    forced: List[dict] = []
    if immediate_kwh > 0:
        smart_sorted = sorted(
            (c for c in candidates if c["mode"] == "Smart"),
            key=lambda c: c["slot"],
        )
        remaining = min(immediate_kwh, energy_needed_kwh)
        for c in smart_sorted:
            if remaining <= 0:
                break
            if remaining < c["energy_kwh"]:
                c = {**c, "energy_kwh": remaining}
            forced.append(c)
            remaining -= c["energy_kwh"]

    forced_slots = {c["slot"] for c in forced}
    remaining_candidates = [c for c in candidates if c["slot"] not in forced_slots]
    remaining_energy = max(0.0, energy_needed_kwh - sum(c["energy_kwh"] for c in forced))

    optimized = _select_cheapest(remaining_candidates, remaining_energy)
    return sorted(forced + optimized, key=lambda c: c["slot"])


def max_available_energy(candidates: List[dict]) -> float:
    """Return the maximum energy (kWh) deliverable across all available slots."""
    return sum(c["energy_kwh"] for c in _best_per_slot(candidates).values())


def select_slots_forced(candidates: List[dict]) -> List[dict]:
    """Select all slots in chronological order, used when the deadline cannot be met."""
    return sorted(_best_per_slot(candidates).values(), key=lambda c: c["slot"])


def mode_for_current_slot(selected: List[dict], now: datetime = None) -> str:
    """Return the planned mode for the current hour, or 'PureSolar' if nothing is scheduled."""
    if now is None:
        now = datetime.now()
    current_slot = now.replace(minute=0, second=0, microsecond=0)
    for c in selected:
        if c["slot"] == current_slot:
            return c["mode"]
    return "PureSolar"
