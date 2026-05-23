"""
optimizer.py
============
Pure optimizer-logica — geen config, geen HA-dependencies.
Importeerbaar door zowel de CLI (laadplanner.py) als de AppDaemon app.
"""

from datetime import datetime, timedelta
from typing import Dict, List

GRID_W_SMARTSOLAR = 1_400
MIN_ZON_W = 300


def tarief_ct(uur: int, nacht_ct: float, dag_ct: float) -> float:
    """Geeft het vaste tarief in ct/kWh voor een gegeven uur."""
    return nacht_ct if (uur >= 22 or uur < 6) else dag_ct


def bouw_kandidaten(
    nu: datetime,
    deadline: datetime,
    solar: Dict[str, float],
    laadvermogen_smart_w: float,
    nacht_ct: float,
    dag_ct: float,
) -> List[dict]:
    """Bouw per uur in de horizon een kandidaat-actie met modus, prijs en kWh."""
    kandidaten = []
    uur = nu.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    while uur <= deadline:
        # Forecast.Solar-timestamps zijn einde van de periode, dus uur+1h als sleutel.
        sleutel = (uur + timedelta(hours=1)).strftime("%Y-%m-%d %H:00:00")
        p_zon_wh = solar.get(sleutel, 0.0)
        t = tarief_ct(uur.hour, nacht_ct, dag_ct)

        if p_zon_wh >= MIN_ZON_W:
            eff_prijs = (GRID_W_SMARTSOLAR * t) / (GRID_W_SMARTSOLAR + p_zon_wh)
            kandidaten.append({
                "tijdstip": uur,
                "modus": "SmartSolar",
                "effectieve_prijs": eff_prijs,
                "laad_kwh": (GRID_W_SMARTSOLAR + p_zon_wh) / 1000,
            })

        kandidaten.append({
            "tijdstip": uur,
            "modus": "Smart",
            "effectieve_prijs": t,
            "laad_kwh": laadvermogen_smart_w / 1000,
        })

        uur += timedelta(hours=1)

    return kandidaten


def selecteer_uren(kandidaten: List[dict], kwh_nodig: float) -> List[dict]:
    """Kies per tijdstip de goedkoopste modus, dan greedy op prijs."""
    per_tijdstip: Dict[datetime, dict] = {}
    for k in kandidaten:
        t = k["tijdstip"]
        if t not in per_tijdstip or k["effectieve_prijs"] < per_tijdstip[t]["effectieve_prijs"]:
            per_tijdstip[t] = k

    gesorteerd = sorted(
        per_tijdstip.values(),
        key=lambda k: (k["effectieve_prijs"], k["tijdstip"]),
    )

    gekozen = []
    kwh_gepland = 0.0
    for k in gesorteerd:
        if kwh_gepland >= kwh_nodig:
            break
        gekozen.append(k)
        kwh_gepland += k["laad_kwh"]

    return sorted(gekozen, key=lambda k: k["tijdstip"])


def modus_huidig_uur(gekozen: List[dict], nu: datetime = None) -> str:
    """Geeft de geplande modus voor het huidige uur, of 'PureSolar' als niets gepland."""
    if nu is None:
        nu = datetime.now()
    huidig_uur = nu.replace(minute=0, second=0, microsecond=0)
    for k in gekozen:
        if k["tijdstip"] == huidig_uur:
            return k["modus"]
    return "PureSolar"
