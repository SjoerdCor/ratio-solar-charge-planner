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
from .optimizer import (
    build_candidates,
    max_available_energy,
    mode_for_current_slot,
    select_slots,
    select_slots_forced,
)
from .tariff import parse_tariff


class ChargeScheduler(hass.Hass):
    """Manages the Ratio charger mode based on SoC, deadline and solar forecast."""

    # AppDaemon uses initialize() instead of __init__(), so attributes are declared here.
    soc_sensor: str
    cable_sensor: str
    charge_mode_select: str
    charge_target_entity: str
    charge_by_entity: str
    battery_kwh: float
    charging_power_kw: float
    hourly_rates: dict

    def initialize(self):
        """Register listeners and schedule the first plan build."""
        entities = self.args["entities"]
        self.soc_sensor = entities["soc_sensor"]
        self.cable_sensor = entities["cable_sensor"]
        self.charge_mode_select = entities["charge_mode_select"]
        self.charge_target_entity = entities["charge_target"]
        self.charge_by_entity = entities["charge_by"]

        vehicle = self.args["vehicle"]
        self.battery_kwh = float(vehicle["battery_kwh"])
        self.charging_power_kw = float(vehicle["charging_power_kw"])

        self.hourly_rates = parse_tariff(self.args["tariff"]["grid"])

        loc = self.args["location"]
        solar_forecast.configure(
            latitude=float(loc["latitude"]),
            longitude=float(loc["longitude"]),
            roof_planes=self.args["panels"],
            cache_dir=Path(__file__).parent / "cache",
        )

        self.run_in(self._replan, 0)
        self.run_hourly(self._replan, "00:00:00")
        self.listen_state(self._replan, self.soc_sensor)
        self.listen_state(self._replan, "input_button.herplan_laadplanner")
        self.listen_state(self._replan, self.cable_sensor)

        self.log("ChargeScheduler initialised")

    def _replan(self, *_args, **_kwargs):
        """Rebuild the charge plan and set the mode for the current hour."""
        self.log("Replanning...")

        if self.get_state(self.cable_sensor) == "off":
            self._publish_status("Cable not connected — no charge plan calculated")
            return

        soc = self._read_soc()
        if soc is None:
            self.log("Cannot build plan: SoC unavailable", level="WARNING")
            self._publish_status("Cannot build plan: SoC unavailable")
            return

        target = self._read_charge_target()
        if target is None:
            self.log("Cannot build plan: charge target unavailable", level="WARNING")
            self._publish_status("Cannot build plan: charge target unavailable")
            return

        deadline = self._read_deadline()

        now = datetime.now()
        if deadline <= now:
            self.log("Deadline is in the past — no plan possible", level="WARNING")
            self._publish_status("Deadline passed — please set a new deadline")
            return

        energy_needed_kwh = (target - soc) / 100 * self.battery_kwh
        self.log(
            f"SoC={soc:.0f}%  target={target:.0f}%  "
            f"deadline={deadline:%a %d %b %H:%M}  needed={energy_needed_kwh:.1f} kWh"
        )

        if energy_needed_kwh <= 0:
            self.log("Target already reached — switching to PureSolar")
            self._publish_status(
                f"Target reached ({soc:.0f}% >= {target:.0f}%) — no charging needed"
            )
            self._set_mode("PureSolar")
            return

        try:
            forecast = solar_forecast.fetch_forecast()
        except Exception as exc:
            self.log(
                f"Solar forecast failed ({type(exc).__name__}: {exc}) — continuing without solar data",
                level="WARNING",
            )
            forecast = {}

        candidates = build_candidates(
            now,
            deadline,
            forecast,
            self.charging_power_kw,
            self.hourly_rates,
        )

        max_kwh = max_available_energy(candidates)
        if max_kwh < energy_needed_kwh:
            selected = select_slots_forced(candidates)
            warning = (
                f"Deadline not achievable: {max_kwh:.1f} kWh available, "
                f"{energy_needed_kwh:.1f} kWh needed. Charging as fast as possible."
            )
            self.log(warning, level="WARNING")
            self._publish_plan(selected, soc, target, warning=warning)
            self._set_mode(mode_for_current_slot(selected))
            return

        selected = select_slots(candidates, energy_needed_kwh)

        if not selected:
            self.log(
                "Optimizer returned no slots — deadline may be in the past or energy need is zero",
                level="WARNING",
            )

        mode = mode_for_current_slot(selected)
        self.log(
            f"Plan: {len(selected)} slot(s) selected  "
            f"planned={sum(s['energy_kwh'] for s in selected):.1f} kWh  "
            f"current mode -> {mode}"
        )
        self._publish_plan(selected, soc, target)
        self._set_mode(mode)

    def _publish_plan(
        self, selected: list, soc_start: float, soc_target: float, warning: str = ""
    ):
        """Write the charge plan to sensor.laadplan so it can be shown on the dashboard."""
        running_soc = soc_start
        lines = []
        for s in selected:
            running_soc = min(
                soc_target, running_soc + s["energy_kwh"] / self.battery_kwh * 100
            )
            duration = timedelta(hours=s["energy_kwh"] / s["power_kw"])
            end = s["slot"] + duration
            lines.append(
                f"{s['slot'].strftime('%H:%M')}-{end.strftime('%H:%M')}"
                f"  {s['mode']}"
                f"  {s['effective_price']:.1f} ct/kWh"
                f"  -> {running_soc:.0f}% (+{s['energy_kwh']:.1f} kWh)"
            )

        plan_text = "\n".join(lines) if lines else "No charging slots planned"
        if warning:
            plan_text = warning + "\n\n" + plan_text
        self._publish_status(plan_text)

    def _publish_status(self, text: str):
        """Write a status message to sensor.laadplan."""
        self.set_state(
            "sensor.laadplan",
            state=text[:255],
            attributes={"plan": text, "friendly_name": "Laadplan"},
        )

    def _read_soc(self):
        """Read current SoC from the VW sensor (%)."""
        self.log(f"SOC sensor entity ID: {self.soc_sensor!r}")
        value = self.get_state(self.soc_sensor)
        if value in (None, "unavailable", "unknown"):
            self.log("Failed reading SOC", level="WARNING")
            return None
        try:
            return float(value)
        except ValueError:
            self.log(f"Could not convert soc to float: {value}", level="WARNING")
            return None

    def _read_charge_target(self):
        """Read desired target SoC from input_number (%)."""
        value = self.get_state(self.charge_target_entity)
        if value in (None, "unavailable", "unknown"):
            self.log("Failed reading charge target", level="WARNING")
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _read_deadline(self):
        """Read charge deadline from input_datetime; fall back to 7 days from now."""
        value = self.get_state(self.charge_by_entity)
        if value in (None, "unavailable", "unknown"):
            self.log("Setting deadline to default fallback")
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
        self.log(f"Charge mode: {current} -> {mode}")
