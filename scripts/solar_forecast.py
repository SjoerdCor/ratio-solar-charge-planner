"""
solar_forecast.py
=================
Haalt uurverwachting op via Forecast.Solar API voor twee dakvlakken (ZO + NO)
en combineert deze tot een totaalvoorspelling.

Stap 1 (TODO): uitbreiden met OpenMeteo voor dag 2-7 + caching.

Gratis Forecast.Solar API: max 12 requests/uur.
"""

import requests
from datetime import datetime, timedelta
from typing import Dict

# --- Configuratie (later uit config.yaml laden) ---
LATITUDE = 52.09
LONGITUDE = 5.23

DAKVLAKKEN = [
    {"naam": "ZO", "kwp": 2.58, "azimuth": -45,  "tilt": 35},
    {"naam": "NO", "kwp": 1.29, "azimuth": -135, "tilt": 35},
]

FORECAST_SOLAR_BASE = "https://api.forecast.solar/estimate"


def haal_dakvlak_uurwaarden(kwp: float, tilt: int, azimuth: int) -> Dict[str, float]:
    """
    Vraag uurwaarden op voor één dakvlak via Forecast.Solar.
    Geeft dict terug: {datetime_str: watt_uur}.

    TODO stap 1: caching toevoegen (JSON naar schijf, max 1 uur oud).
    """
    url = (
        f"{FORECAST_SOLAR_BASE}/{LATITUDE}/{LONGITUDE}"
        f"/{tilt}/{azimuth}/{kwp}"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data["result"]["watt_hours"]


def haal_uurwaarden() -> Dict[str, float]:
    """
    Combineer alle dakvlakken tot één totaalvoorspelling.
    Geeft dict terug: {datetime_str: watt_uur}.
    """
    totaal: Dict[str, float] = {}
    for vlak in DAKVLAKKEN:
        vlak_data = haal_dakvlak_uurwaarden(vlak["kwp"], vlak["tilt"], vlak["azimuth"])
        for tijdstip, wh in vlak_data.items():
            totaal[tijdstip] = totaal.get(tijdstip, 0) + wh
    return totaal


def watt_op_tijdstip(tijdstip: datetime) -> float:
    """
    Geef de verwachte zonneopwek in W op een gegeven tijdstip.
    Interpoleer lineair tussen de twee dichtstbijzijnde uurwaarden.

    TODO stap 1: gebruik gecachede data i.p.v. live API-call.
    """
    uurwaarden = haal_uurwaarden()
    sleutels = sorted(uurwaarden.keys())
    tijdstip_str = tijdstip.strftime("%Y-%m-%d %H:%M:%S")

    # Zoek omliggend interval
    voor = None
    na = None
    for s in sleutels:
        if s <= tijdstip_str:
            voor = s
        elif s > tijdstip_str and na is None:
            na = s
            break

    if voor is None:
        return 0.0
    if na is None:
        return uurwaarden[voor]

    # Lineaire interpolatie
    t0 = datetime.strptime(voor, "%Y-%m-%d %H:%M:%S")
    t1 = datetime.strptime(na,   "%Y-%m-%d %H:%M:%S")
    frac = (tijdstip - t0).total_seconds() / (t1 - t0).total_seconds()
    return uurwaarden[voor] + frac * (uurwaarden[na] - uurwaarden[voor])


if __name__ == "__main__":
    print("Ophalen zonnepredictie (vandaag)...")
    uurwaarden = haal_uurwaarden()
    for tijdstip, wh in sorted(uurwaarden.items()):
        print(f"  {tijdstip}: {wh:.0f} Wh")
