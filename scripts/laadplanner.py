"""
laadplanner.py
==============
CLI wrapper for the rolling-horizon charge planner.

Usage:
  uv run python scripts/laadplanner.py
  uv run python scripts/laadplanner.py --soc 25 --target 80 --days 7
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import yaml

_ROOT = Path(__file__).parent.parent
_AD   = _ROOT / "appdaemon" / "apps" / "laadplanner"
sys.path.insert(0, str(_AD))

import solar_forecast
from optimizer import build_candidates, max_available_energy, select_slots, select_slots_forced
from tariff import parse_tariff

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def _load_config() -> dict:
    path = _ROOT / "appdaemon" / "apps" / "apps.yaml"
    if not path.exists():
        raise FileNotFoundError(
            "appdaemon/apps/apps.yaml not found — copy apps.yaml.example and fill it in."
        )
    return yaml.safe_load(path.read_text(encoding="utf-8"))["laadplanner"]


_cfg = _load_config()
solar_forecast.configure(
    latitude=_cfg["location"]["latitude"],
    longitude=_cfg["location"]["longitude"],
    roof_planes=_cfg["panels"],
    cache_dir=_ROOT / "data" / "cache",
)

BATTERY_KWH       = _cfg["vehicle"]["battery_kwh"]
CHARGING_POWER_KW = _cfg["vehicle"]["charging_power_kw"]
HOURLY_RATES      = parse_tariff(_cfg["tariff"]["grid"])


def print_plan(selected: List[dict], soc_start: float, soc_target: float, deadline: datetime):
    """Print the charge plan as a formatted table."""
    energy_needed  = (soc_target - soc_start) / 100 * BATTERY_KWH
    energy_planned = sum(s["energy_kwh"] for s in selected)

    print(
        f"Charge plan  SoC {soc_start:.0f}% -> {soc_target:.0f}%"
        f"  |  deadline {deadline:%a %d %b %H:%M}"
    )
    print(
        f"Needed  {energy_needed:.1f} kWh"
        f"  |  planned {energy_planned:.1f} kWh"
        f"  |  {len(selected)} slots"
    )
    print()
    print(f"  {'Time':<18} {'Mode':<12} {'ct/kWh':>9}  {'kWh':>6}  {'SoC':>5}")
    print(f"  {'-'*18} {'-'*12} {'-'*9}  {'-'*6}  {'-'*5}")

    soc = soc_start
    for s in selected:
        soc = min(soc_target, soc + s["energy_kwh"] / BATTERY_KWH * 100)
        print(
            f"  {s['slot'].strftime('%a %d %b  %H:%M'):<18}"
            f" {s['mode']:<12}"
            f" {s['effective_price']:>7.1f} ct/kWh"
            f"  {s['energy_kwh']:>5.1f}"
            f"  {soc:>4.0f}%"
        )


def main():
    """CLI entry point for the charge planner."""
    parser = argparse.ArgumentParser(description="Rolling-horizon charge planner")
    parser.add_argument("--soc",    type=float, default=25.0, help="Current SoC (%)")
    parser.add_argument("--target", type=float, default=80.0, help="Target SoC (%)")
    parser.add_argument("--days",   type=float, default=7.0,  help="Deadline in N days")
    args = parser.parse_args()

    deadline      = datetime.now() + timedelta(days=args.days)
    energy_needed = (args.target - args.soc) / 100 * BATTERY_KWH

    log.info("Fetching solar forecast...")
    forecast = solar_forecast.fetch_forecast()
    if forecast:
        log.info("  %d periods available (%s to %s)", len(forecast), min(forecast), max(forecast))
    else:
        log.info("  No forecast data available")

    candidates = build_candidates(
        datetime.now(), deadline, forecast, CHARGING_POWER_KW, HOURLY_RATES
    )

    max_kwh = max_available_energy(candidates)
    impossible = max_kwh < energy_needed
    if impossible:
        selected = select_slots_forced(candidates)
    else:
        selected = select_slots(candidates, energy_needed)
    print_plan(selected, args.soc, args.target, deadline)

    if impossible:
        print(
            f"\nWarning: deadline not achievable — {max_kwh:.1f} kWh available,"
            f" {energy_needed:.1f} kWh needed. Charging as fast as possible."
        )


if __name__ == "__main__":
    main()
