"""
solar_forecast.py
=================
Haalt uurverwachting op via Forecast.Solar API voor twee dakvlakken (ZO + NO)
en combineert deze tot een totaalvoorspelling.

Forecast.Solar azimutconventie: 0=south, negatief=oost, positief=west.
  ZO (zuidoost) → az=-45, NO (noordoost) → az=-135

Gratis API: max 12 requests/uur.
Stap 1 (TODO): OpenMeteo toevoegen voor dag 2-7.
"""

import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

LATITUDE = 52.09
LONGITUDE = 5.23

DAKVLAKKEN = [
    {"naam": "ZO", "kwp": 2.58, "azimuth": -45,  "tilt": 35},
    {"naam": "NO", "kwp": 1.29, "azimuth": -135, "tilt": 35},
]

FORECAST_SOLAR_BASE = "https://api.forecast.solar/estimate"
CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_MAX_OUD = timedelta(hours=1)


def _haal_api(kwp: float, tilt: int, azimuth: int) -> dict:
    """Fetch ruwe API-response voor één dakvlak. Gebruikt schijfcache (max 1 uur oud)."""
    cache_bestand = CACHE_DIR / f"forecast_{tilt}_{azimuth}_{kwp}.json"
    if cache_bestand.exists():
        opgeslagen = json.loads(cache_bestand.read_text(encoding="utf-8"))
        leeftijd = datetime.now() - datetime.fromisoformat(opgeslagen["opgeslagen_op"])
        if leeftijd < CACHE_MAX_OUD:
            return opgeslagen["result"]

    url = f"{FORECAST_SOLAR_BASE}/{LATITUDE}/{LONGITUDE}/{tilt}/{azimuth}/{kwp}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    result = resp.json()["result"]

    cache_bestand.write_text(
        json.dumps({"opgeslagen_op": datetime.now().isoformat(), "result": result}),
        encoding="utf-8",
    )
    return result


def haal_dakvlak_uurwaarden(kwp: float, tilt: int, azimuth: int) -> Dict[str, float]:
    """
    Geeft per-periode energieproductie voor één dakvlak.
    Dict: {datetime_str: Wh in die periode}.

    Gebruikt watt_hours_period (niet watt_hours, dat is cumulatief).
    """
    return _haal_api(kwp, tilt, azimuth)["watt_hours_period"]


def haal_uurwaarden() -> Dict[str, float]:
    """
    Combineer alle dakvlakken tot één totaalvoorspelling.
    Geeft dict terug: {datetime_str: Wh in die periode}.
    """
    totaal: Dict[str, float] = {}
    for vlak in DAKVLAKKEN:
        vlak_data = haal_dakvlak_uurwaarden(vlak["kwp"], vlak["tilt"], vlak["azimuth"])
        for t, wh in vlak_data.items():
            totaal[t] = totaal.get(t, 0) + wh
    return totaal


def watt_op_tijdstip(tijdstip: datetime) -> float:
    """
    Geef de verwachte zonneopwek in W op een gegeven tijdstip.
    Interpoleer lineair tussen de twee dichtstbijzijnde uur-gemiddelden (watts).
    """
    totaal_watts: Dict[str, float] = {}
    for vlak in DAKVLAKKEN:
        vlak_watts = _haal_api(vlak["kwp"], vlak["tilt"], vlak["azimuth"])["watts"]
        for t, w in vlak_watts.items():
            totaal_watts[t] = totaal_watts.get(t, 0) + w

    sleutels = sorted(totaal_watts.keys())
    tijdstip_str = tijdstip.strftime("%Y-%m-%d %H:%M:%S")

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
        return totaal_watts[voor]

    t0 = datetime.strptime(voor, "%Y-%m-%d %H:%M:%S")
    t1 = datetime.strptime(na,   "%Y-%m-%d %H:%M:%S")
    frac = (tijdstip - t0).total_seconds() / (t1 - t0).total_seconds()
    return totaal_watts[voor] + frac * (totaal_watts[na] - totaal_watts[voor])


if __name__ == "__main__":
    vandaag = datetime.now().strftime("%Y-%m-%d")
    print(f"Zonnepredictie per periode ({vandaag}):")
    uurwaarden = haal_uurwaarden()
    dag_totaal = 0.0
    for t, wh in sorted(uurwaarden.items()):
        if t.startswith(vandaag):
            dag_totaal += wh
            print(f"  {t[11:16]}: {wh:5.0f} Wh")
    print(f"  Dag-totaal: {dag_totaal:.0f} Wh ({dag_totaal/1000:.2f} kWh)")
