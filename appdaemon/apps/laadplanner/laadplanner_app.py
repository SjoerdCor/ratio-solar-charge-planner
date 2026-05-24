"""
laadplanner_app.py
==================
AppDaemon app: reads SoC and deadline from HA, runs the optimizer,
and writes the desired charge mode back to the Ratio charger.

Configuration via apps.yaml on the HA host (not committed to git).
See appdaemon/apps.yaml.example for the expected structure.

Deployment: git clone / git pull on the HA host, point AppDaemon
to appdaemon/apps/laadplanner/ as the apps directory.
"""

from datetime import datetime, timedelta
from pathlib import Path

import appdaemon.plugins.hass.hassapi as hass

from . import solar_forecast
from .optimizer import build_candidates, mode_for_current_slot, select_slots

REPLAN_INTERVAL_S = 3600  # rebuild the plan every hour


class ChargeScheduler(hass.Hass):
    """Manages the Ratio charger mode based on SoC, deadline and solar forecast."""

    # AppDaemon uses initialize() instead of __init__(), so attributes are declared here.
    soc_sensor: str
    charge_mode_select: str
    charge_target_entity: str
    charge_by_entity: str
    battery_kwh: float
    charging_power_kw: float
    day_rate: float
    night_rate: float

    def initialize(self):
        """Register listeners and schedule the first plan build."""
        entities = self.args["entities"]
        self.soc_sensor = entities["soc_sensor"]
        self.charge_mode_select = entities["charge_mode_select"]
        self.charge_target_entity = entities["charge_target"]
        self.charge_by_entity = entities["charge_by"]

        vehicle = self.args["vehicle"]
        self.battery_kwh = float(vehicle["battery_kwh"])
        self.charging_power_kw = float(vehicle["charging_power_kw"])

        rate = self.args["fixed_rate"]
        self.day_rate = float(rate["day_rate_ct"])
        self.night_rate = float(rate["night_rate_ct"])

        loc = self.args["location"]
        solar_forecast.configure(
            latitude=float(loc["latitude"]),
            longitude=float(loc["longitude"]),
            roof_planes=self.args["panels"],
            cache_dir=Path(__file__).parent / "cache",
        )

        self.run_every(self._replan, "now", REPLAN_INTERVAL_S)
        self.listen_state(self._replan, self.soc_sensor)
        self.listen_state(self._replan, self.charge_by_entity)

        self.log("ChargeScheduler initialised")

    def _replan(self, *_args, **_kwargs):
        """Rebuild the charge plan and set the mode for the current hour."""
        soc = self._read_soc()
        target = self._read_charge_target()
        deadline = self._read_deadline()

        if soc is None or target is None or deadline is None:
            self.log("Cannot build plan: missing input", level="WARNING")
            return

        energy_needed_kwh = (target - soc) / 100 * self.battery_kwh
        if energy_needed_kwh <= 0:
            self._set_mode("PureSolar")
            return

        try:
            forecast = solar_forecast.fetch_forecast()
        except Exception as exc:
            self.log(f"Solar forecast failed: {exc}", level="WARNING")
            forecast = {}

        candidates = build_candidates(
            datetime.now(),
            deadline,
            forecast,
            self.charging_power_kw,
            self.night_rate,
            self.day_rate,
        )
        selected = select_slots(candidates, energy_needed_kwh)
        mode = mode_for_current_slot(selected)

        self.log(
            f"Plan rebuilt: SoC={soc:.0f}% target={target:.0f}% "
            f"deadline={deadline:%a %d %b %H:%M} → mode={mode}"
        )
        self._set_mode(mode)

    def _read_soc(self):
        """Read current SoC from the VW sensor (%)."""
        value = self.get_state(self.soc_sensor)
        if value in (None, "unavailable", "unknown"):
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _read_charge_target(self):
        """Read desired target SoC from input_number (%)."""
        value = self.get_state(self.charge_target_entity)
        if value in (None, "unavailable", "unknown"):
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _read_deadline(self):
        """Read charge deadline from input_datetime; fall back to 7 days from now."""
        value = self.get_state(self.charge_by_entity)
        if value in (None, "unavailable", "unknown"):
            return datetime.now() + timedelta(days=7)
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.now() + timedelta(days=7)

    def _set_mode(self, mode: str):
        """Write the desired mode to the Ratio select entity (no-op if unchanged)."""
        current = self.get_state(self.charge_mode_select)
        if current == mode:
            return
        self.call_service(
            "select/select_option",
            entity_id=self.charge_mode_select,
            option=mode,
        )
        self.log(f"Charge mode: {current} → {mode}")
