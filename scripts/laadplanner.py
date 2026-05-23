"""
laadplanner.py
==============
CLI-wrapper voor de rolling-horizon laadplanner.

Gebruik:
  uv run python scripts/laadplanner.py
  uv run python scripts/laadplanner.py --soc 25 --doel 80 --dagen 7
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent))
from config_loader import laad_config
from optimizer import bouw_kandidaten, selecteer_uren
from solar_forecast import haal_uurwaarden

_cfg = laad_config()
ACCU_KWH             = _cfg["auto"]["accu_kwh"]
LAADVERMOGEN_SMART_W = _cfg["auto"]["laadvermogen_smart_w"]
NACHT_CT             = _cfg["vast_tarief"]["nacht_ct_per_kwh"]
DAG_CT               = _cfg["vast_tarief"]["dag_ct_per_kwh"]


def druk_af(gekozen: List[dict], soc_start: float, soc_doel: float, deadline: datetime):
    """Print het laadplan naar stdout."""
    kwh_nodig  = (soc_doel - soc_start) / 100 * ACCU_KWH
    kwh_gepland = sum(k["laad_kwh"] for k in gekozen)
    n_uren = len(gekozen)

    deadline_str = deadline.strftime("%a %d %b %H:%M")
    print(f"Laadplan  SOC {soc_start:.0f}% -> {soc_doel:.0f}%  |  deadline {deadline_str}")
    print(f"Te laden  {kwh_nodig:.1f} kWh  |  gepland {kwh_gepland:.1f} kWh  |  {n_uren} uren")
    print()
    print(f"  {'Tijdstip':<18} {'Modus':<12} {'Prijs':>9}  {'kWh':>6}  {'SOC':>5}")
    print(f"  {'-'*18} {'-'*12} {'-'*9}  {'-'*6}  {'-'*5}")

    soc = soc_start
    for k in gekozen:
        soc = min(soc_doel, soc + k["laad_kwh"] / ACCU_KWH * 100)
        print(
            f"  {k['tijdstip'].strftime('%a %d %b  %H:%M'):<18}"
            f" {k['modus']:<12}"
            f" {k['effectieve_prijs']:>7.1f} ct"
            f"  {k['laad_kwh']:>5.1f}"
            f"  {soc:>4.0f}%"
        )


def main():
    """CLI-ingangspunt voor de laadplanner."""
    parser = argparse.ArgumentParser(description="Simpele laadplanner")
    parser.add_argument("--soc",   type=float, default=25.0, help="Huidige SOC (%)")
    parser.add_argument("--doel",  type=float, default=80.0, help="Doel SOC (%)")
    parser.add_argument("--dagen", type=float, default=7.0,  help="Deadline over N dagen")
    args = parser.parse_args()

    deadline  = datetime.now() + timedelta(days=args.dagen)
    kwh_nodig = (args.doel - args.soc) / 100 * ACCU_KWH

    print("Zonnepredictie ophalen...")
    solar = haal_uurwaarden()
    bereik = f"{min(solar)} t/m {max(solar)}" if solar else "-"
    print(f"  {len(solar)} periodes beschikbaar ({bereik})")
    print()

    kandidaten = bouw_kandidaten(
        datetime.now(), deadline, solar, LAADVERMOGEN_SMART_W, NACHT_CT, DAG_CT
    )
    gekozen = selecteer_uren(kandidaten, kwh_nodig)
    druk_af(gekozen, args.soc, args.doel, deadline)


if __name__ == "__main__":
    main()
