"""
laadplanner.py
==============
Simpele rolling-horizon laadplanner (greedy, zonder linprog).

Gebruik:
  uv run python scripts/laadplanner.py
  uv run python scripts/laadplanner.py --soc 25 --doel 80 --dagen 7
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent))
from solar_forecast import haal_uurwaarden

# Accu en laadpaal
ACCU_KWH = 77.0
LAADVERMOGEN_SMART_W = 11_000   # W in Smart-modus (3-fase 16A)
GRID_W_SMARTSOLAR = 1_400       # W van het net in SmartSolar-modus

# Vast tarief (ct/kWh)
NACHT_CT = 23.0   # 22:00–06:00
DAG_CT   = 27.0   # 06:00–22:00

MIN_ZON_W = 300   # minimaal zonne-overschot voor SmartSolar


def tarief_ct(uur: int) -> float:
    return NACHT_CT if (uur >= 22 or uur < 6) else DAG_CT


def bouw_kandidaten(nu: datetime, deadline: datetime, solar: Dict[str, float]) -> List[dict]:
    """Per uur in de horizon: goedkoopste modus + verwachte laad-kWh."""
    kandidaten = []
    uur = nu.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    while uur <= deadline:
        # Forecast.Solar geeft Wh per periode; timestamp is einde van de periode.
        # We kijken dus naar uur+1h als sleutel voor het uur dat begint op `uur`.
        sleutel = (uur + timedelta(hours=1)).strftime("%Y-%m-%d %H:00:00")
        p_zon_wh = solar.get(sleutel, 0.0)

        t = tarief_ct(uur.hour)

        # SmartSolar: alleen als er genoeg zon is
        if p_zon_wh >= MIN_ZON_W:
            eff_prijs = (GRID_W_SMARTSOLAR * t) / (GRID_W_SMARTSOLAR + p_zon_wh)
            kandidaten.append({
                "tijdstip": uur,
                "modus": "SmartSolar",
                "effectieve_prijs": eff_prijs,
                "laad_kwh": (GRID_W_SMARTSOLAR + p_zon_wh) / 1000,
            })

        # Smart: altijd beschikbaar
        kandidaten.append({
            "tijdstip": uur,
            "modus": "Smart",
            "effectieve_prijs": t,
            "laad_kwh": LAADVERMOGEN_SMART_W / 1000,
        })

        uur += timedelta(hours=1)

    return kandidaten


def selecteer_uren(kandidaten: List[dict], kwh_nodig: float) -> List[dict]:
    """Kies per tijdstip de goedkoopste modus, dan greedy op prijs."""
    # Per tijdstip: goedkoopste modus winst
    per_tijdstip: Dict[datetime, dict] = {}
    for k in kandidaten:
        t = k["tijdstip"]
        if t not in per_tijdstip or k["effectieve_prijs"] < per_tijdstip[t]["effectieve_prijs"]:
            per_tijdstip[t] = k

    # Sorteer op prijs (goedkoopste eerst), bij gelijke prijs op tijdstip
    gesorteerd = sorted(per_tijdstip.values(), key=lambda k: (k["effectieve_prijs"], k["tijdstip"]))

    gekozen = []
    kwh_gepland = 0.0
    for k in gesorteerd:
        if kwh_gepland >= kwh_nodig:
            break
        gekozen.append(k)
        kwh_gepland += k["laad_kwh"]

    return sorted(gekozen, key=lambda k: k["tijdstip"])


def druk_af(gekozen: List[dict], soc_start: float, soc_doel: float, deadline: datetime):
    kwh_nodig = (soc_doel - soc_start) / 100 * ACCU_KWH
    kwh_gepland = sum(k["laad_kwh"] for k in gekozen)

    print(f"Laadplan  SOC {soc_start:.0f}% -> {soc_doel:.0f}%  |  deadline {deadline.strftime('%a %d %b %H:%M')}")
    print(f"Te laden  {kwh_nodig:.1f} kWh  |  gepland {kwh_gepland:.1f} kWh  |  {len(gekozen)} uren")
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
    parser = argparse.ArgumentParser(description="Simpele laadplanner")
    parser.add_argument("--soc",   type=float, default=25.0, help="Huidige SOC (%)")
    parser.add_argument("--doel",  type=float, default=80.0, help="Doel SOC (%)")
    parser.add_argument("--dagen", type=float, default=7.0,  help="Deadline over N dagen")
    args = parser.parse_args()

    deadline = datetime.now() + timedelta(days=args.dagen)
    kwh_nodig = (args.doel - args.soc) / 100 * ACCU_KWH

    print("Zonnepredictie ophalen...")
    solar = haal_uurwaarden()
    print(f"  {len(solar)} periodes beschikbaar ({min(solar) if solar else '-'} t/m {max(solar) if solar else '-'})")
    print()

    kandidaten = bouw_kandidaten(datetime.now(), deadline, solar)
    gekozen = selecteer_uren(kandidaten, kwh_nodig)
    druk_af(gekozen, args.soc, args.doel, deadline)


if __name__ == "__main__":
    main()
