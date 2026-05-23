"""
laadplanner_app.py
==================
AppDaemon app die de rolling-horizon optimizer aanroept en
de Ratio Solar laadpaal aanstuurt via Home Assistant.

Wacht op:
- scripts/laadplanner.py (stap 3)
- GoodWe integratie actief (panelen verwacht ~2 maanden)
- Tibber contract
"""

import appdaemon.plugins.hass.hassapi as hass

class LaadPlanner(hass.Hass):

    def initialize(self):
        self.log("LaadPlanner geïnitialiseerd (nog niet actief)")
        # TODO: listeners registreren, optimizer inplannen
