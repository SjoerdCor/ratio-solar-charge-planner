"""
laadplanner_app.py
==================
AppDaemon app: leest SOC en deadline uit HA, draait de optimizer,
en schrijft de gewenste laadmodus terug naar de Ratio-laadpaal.

Configuratie via apps.yaml (op de HA-host, niet gecommit).
Zie appdaemon/apps.yaml.example voor de verwachte structuur.

Deployment: kopieer de inhoud van appdaemon/apps/laadplanner/ naar
de AppDaemon apps-map op de HA-host.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import appdaemon.plugins.hass.hassapi as hass

# Voeg de scripts-map toe zodat solar_forecast en optimizer importeerbaar zijn.
# In productie: kopieer solar_forecast.py en optimizer.py naar deze map.
_SCRIPTS = Path(__file__).parent.parent.parent.parent / "scripts"
if _SCRIPTS.exists():
    sys.path.insert(0, str(_SCRIPTS))

from optimizer import bouw_kandidaten, modus_huidig_uur, selecteer_uren
from solar_forecast import haal_uurwaarden

HERPLAN_INTERVAL_S = 3600  # herteken het plan elk uur


class LaadPlanner(hass.Hass):
    """Beheert de laadmodus van de Ratio-laadpaal op basis van SOC, deadline en zonnepredictie."""

    def initialize(self):
        """Registreer listeners en plan het eerste herteken-moment."""
        ent = self.args["entiteiten"]
        self.soc_sensor       = ent["soc_sensor"]
        self.laadmodus_select = ent["laadmodus_select"]
        self.laad_doel_entity = ent["laad_doel"]
        self.laad_klaar_om    = ent["laad_klaar_om"]

        auto = self.args["auto"]
        self.accu_kwh          = float(auto["accu_kwh"])
        self.laadvermogen_w    = float(auto["laadvermogen_smart_w"])

        tarief = self.args["vast_tarief"]
        self.dag_ct   = float(tarief["dag_ct_per_kwh"])
        self.nacht_ct = float(tarief["nacht_ct_per_kwh"])

        # Herteken het plan elk uur
        self.run_every(self._herteken, "now", HERPLAN_INTERVAL_S)

        # Herteken ook als SOC of deadline verandert
        self.listen_state(self._herteken, self.soc_sensor)
        self.listen_state(self._herteken, self.laad_klaar_om)

        self.log("LaadPlanner geinitialiseerd")

    def _herteken(self, *args, **kwargs):
        """Bereken het laadplan en stel de modus in voor het huidige uur."""
        soc    = self._lees_soc()
        doel   = self._lees_laad_doel()
        deadline = self._lees_deadline()

        if soc is None or doel is None or deadline is None:
            self.log("Kan plan niet maken: ontbrekende invoer", level="WARNING")
            return

        kwh_nodig = (doel - soc) / 100 * self.accu_kwh
        if kwh_nodig <= 0:
            self._stel_modus_in("PureSolar")
            return

        try:
            solar = haal_uurwaarden()
        except Exception as e:
            self.log(f"Zonnepredictie ophalen mislukt: {e}", level="WARNING")
            solar = {}

        kandidaten = bouw_kandidaten(
            datetime.now(), deadline, solar,
            self.laadvermogen_w, self.nacht_ct, self.dag_ct,
        )
        gekozen = selecteer_uren(kandidaten, kwh_nodig)
        modus   = modus_huidig_uur(gekozen)

        self.log(
            f"Plan hertekend: SOC={soc:.0f}% doel={doel:.0f}% "
            f"deadline={deadline:%a %d %b %H:%M} → modus={modus}"
        )
        self._stel_modus_in(modus)

    def _lees_soc(self):
        """Lees huidige SOC van de VW-sensor (%)."""
        waarde = self.get_state(self.soc_sensor)
        if waarde in (None, "unavailable", "unknown"):
            return None
        try:
            return float(waarde)
        except ValueError:
            return None

    def _lees_laad_doel(self):
        """Lees gewenste doel-SOC van input_number (%)."""
        waarde = self.get_state(self.laad_doel_entity)
        if waarde in (None, "unavailable", "unknown"):
            return None
        try:
            return float(waarde)
        except ValueError:
            return None

    def _lees_deadline(self):
        """Lees deadline van input_datetime en geef een datetime terug."""
        waarde = self.get_state(self.laad_klaar_om)
        if waarde in (None, "unavailable", "unknown"):
            # Fallback: over 7 dagen
            return datetime.now() + timedelta(days=7)
        try:
            return datetime.strptime(waarde, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.now() + timedelta(days=7)

    def _stel_modus_in(self, modus: str):
        """Schrijf de gewenste modus naar het Ratio-select-entiteit."""
        huidig = self.get_state(self.laadmodus_select)
        if huidig == modus:
            return
        self.call_service(
            "select/select_option",
            entity_id=self.laadmodus_select,
            option=modus,
        )
        self.log(f"Laadmodus gezet: {huidig} → {modus}")
