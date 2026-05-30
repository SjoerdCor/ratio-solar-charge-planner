"""
charger_app.py
==============
AppDaemon app: reads SoC and deadline from HA, runs the optimizer,
and writes the desired charge mode back to the Ratio charger.

Configuration via apps.yaml on the HA host (not committed to git).
See apps.yaml.example for the expected structure.

Deployment: git clone / git pull on the HA host, symlink
apps/charger/ into the AppDaemon apps directory.
"""

import json
from dataclasses import dataclass, field
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

_SOC_OVERRIDE = "input_number.soc_override"
_SESSION_ENERGY = "sensor.session_energy_kwh"
_PLAN_JSON = Path("/homeassistant/www/charge_plan.json")


@dataclass
class _PlanData:
    """All fields written to the charge-plan JSON and sensor."""
    soc_start: float | None = None
    soc_target: float | None = None
    deadline: datetime | None = None
    slots: list = field(default_factory=list)
    warning: str | None = None
    status: str | None = None


class ChargeScheduler(hass.Hass):  # pylint: disable=too-many-instance-attributes
    """Manages the Ratio charger mode based on SoC, deadline and solar forecast.

    AppDaemon apps cannot use __init__, so all instance attributes must be declared here
    and assigned in initialize(). This inherently produces more attributes than pylint's
    default limit allows.
    """

    soc_sensor: str
    power_sensor: str
    cable_sensor: str
    charge_mode_select: str
    charge_target_entity: str
    charge_minimum_entity: str
    charge_by_entity: str
    battery_kwh: float
    charging_power_kw: float
    hourly_rates: dict
    _last_power_kw: float | None
    _last_power_time: datetime | None
    _threshold_timer: str | None

    def initialize(self):
        """Register listeners and schedule the first plan build."""
        self.soc_sensor = self.args["soc_sensor"]

        serial = self.args["ratio_serial"]
        self.cable_sensor = f"binary_sensor.ratio_{serial}_vehicle_connected"
        self.charge_mode_select = f"select.ratio_{serial}_charge_mode"
        self.power_sensor = f"sensor.ratio_{serial}_actual_charging_power"

        self.charge_target_entity = "input_number.charge_target"
        self.charge_minimum_entity = "input_number.charge_minimum"
        self.charge_by_entity = "input_datetime.charge_by"

        vehicle = self.args["vehicle"]
        self.battery_kwh = float(vehicle["battery_kwh"])
        self.charging_power_kw = float(vehicle["charging_power_kw"])

        self.hourly_rates = parse_tariff(self.args["tariff"]["grid"])

        lat = float(self.get_state("zone.home", attribute="latitude"))
        lon = float(self.get_state("zone.home", attribute="longitude"))
        solar_forecast.configure(
            latitude=lat,
            longitude=lon,
            roof_planes=self.args.get("panels", []),
            cache_dir=Path(__file__).parent / "cache",
        )

        self._last_power_kw = None
        self._last_power_time = None
        self._threshold_timer = None

        if self.get_state(self.cable_sensor) != "on":
            self._reset_session_energy()

        self.run_in(self._replan, 0)
        self.run_hourly(self._replan, "00:00:00")
        self.listen_state(self._replan, self.soc_sensor)
        self.listen_state(self._replan, "input_button.replan")
        self.listen_state(self._replan, self.cable_sensor)
        self.listen_state(self._on_power_change, self.power_sensor)
        self.listen_state(self._on_cable_disconnect, self.cable_sensor, new="off")

        self.log("ChargeScheduler initialised")

    def _replan(self, *_args, **_kwargs):
        """Rebuild the charge plan and set the mode for the current hour."""
        if self._threshold_timer is not None:
            self.cancel_timer(self._threshold_timer)
            self._threshold_timer = None

        self.log("Replanning...")

        if self.get_state(self.cable_sensor) == "off":
            status = "Cable not connected — no charge plan calculated"
            self._publish_status(status)
            self._write_plan_json(_PlanData(status=status))
            return

        soc = self._read_soc()
        if soc is not None:
            self._sync_soc_override(soc)
        else:
            soc = self._read_soc_fallback()

        if soc is None:
            self.log("Cannot build plan: SoC unavailable", level="WARNING")
            status = "Cannot build plan: SoC unavailable"
            self._publish_status(status)
            self._write_plan_json(_PlanData(status=status))
            return

        target = self._read_charge_target()
        if target is None:
            self.log("Cannot build plan: charge target unavailable", level="WARNING")
            status = "Cannot build plan: charge target unavailable"
            self._publish_status(status)
            self._write_plan_json(_PlanData(soc_start=round(soc, 1), status=status))
            return

        deadline = self._read_deadline()

        now = datetime.now()
        if deadline <= now:
            self.log("Deadline is in the past — no plan possible", level="WARNING")
            status = "Deadline passed — please set a new deadline"
            self._publish_status(status)
            self._write_plan_json(_PlanData(
                soc_start=round(soc, 1),
                soc_target=round(target, 1),
                deadline=deadline,
                status=status,
            ))
            return

        minimum = self._read_charge_minimum()
        self._run_optimizer(soc, target, deadline, minimum)

    def _run_optimizer(self, soc: float, target: float, deadline: datetime, minimum: float):
        """Build and apply the charge plan; schedule a mid-hour replan when a threshold is near."""
        immediate_kwh = max(0.0, (minimum - soc) / 100 * self.battery_kwh)
        energy_needed_kwh = (target - soc) / 100 * self.battery_kwh
        self.log(
            f"SoC={soc:.0f}%  target={target:.0f}%  minimum={minimum:.0f}%  "
            f"deadline={deadline:%a %d %b %H:%M}  needed={energy_needed_kwh:.1f} kWh"
            + (f"  immediate={immediate_kwh:.1f} kWh" if immediate_kwh > 0 else "")
        )

        if energy_needed_kwh <= 0:
            self.log("Target already reached — switching to PureSolar")
            status = f"Target reached ({soc:.0f}% ≥ {target:.0f}%) — no charging needed"
            self._publish_status(status)
            self._write_plan_json(_PlanData(
                soc_start=round(soc, 1),
                soc_target=round(target, 1),
                deadline=deadline,
                status=status,
            ))
            self._set_mode("PureSolar")
            return

        try:
            forecast = solar_forecast.fetch_forecast()
        except solar_forecast.SolarForecastError as exc:
            self.log(
                f"Solar forecast failed: {exc} — continuing without solar data",
                level="WARNING",
            )
            forecast = {}

        candidates = build_candidates(
            datetime.now(), deadline, forecast, self.charging_power_kw, self.hourly_rates,
        )

        max_kwh = max_available_energy(candidates)
        if max_kwh < energy_needed_kwh:
            selected = select_slots_forced(candidates)
            warning = (
                f"Deadline not achievable: {max_kwh:.1f} kWh available, "
                f"{energy_needed_kwh:.1f} kWh needed. Charging as fast as possible."
            )
            self.log(warning, level="WARNING")
            self._publish_plan(selected, soc, target, deadline, warning=warning)
            self._set_mode(mode_for_current_slot(selected))
            return

        selected = select_slots(candidates, energy_needed_kwh, immediate_kwh=immediate_kwh)

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
        self._publish_plan(selected, soc, target, deadline)
        self._set_mode(mode)
        self._schedule_threshold_timer(immediate_kwh, energy_needed_kwh)

    def _schedule_threshold_timer(self, immediate_kwh: float, energy_needed_kwh: float):
        """Schedule a mid-hour replan for when the nearest SoC threshold will be crossed.

        Uses the minimum SoC threshold first (if not yet reached), otherwise the charge
        target. Fires 5 minutes before the estimated crossing; clamped to at least 60 s.
        """
        threshold_kwh = immediate_kwh if immediate_kwh > 0 else energy_needed_kwh
        time_to_threshold_sec = threshold_kwh / self.charging_power_kw * 3600
        if time_to_threshold_sec < 3600:
            delay = max(60, time_to_threshold_sec - 300)
            self._threshold_timer = self.run_in(self._replan, delay)
            self.log(
                f"SoC threshold in ~{time_to_threshold_sec / 60:.0f} min"
                f" — replan in {delay / 60:.0f} min"
            )

    def _on_power_change(self, entity, attribute, old, new, kwargs):
        """Accumulate session energy from every power sensor update (Riemann sum)."""
        now = datetime.now()
        if self._last_power_time is not None and self._last_power_kw is not None:
            elapsed_hours = (now - self._last_power_time).total_seconds() / 3600
            energy_kwh = self._last_power_kw * elapsed_hours
            if energy_kwh > 0:
                try:
                    current = float(self.get_state(_SESSION_ENERGY) or 0)
                except (TypeError, ValueError):
                    current = 0.0
                self.set_state(
                    _SESSION_ENERGY,
                    state=round(current + energy_kwh, 3),
                    attributes={"unit_of_measurement": "kWh", "friendly_name": "Session energy"},
                )

        if new in (None, "unavailable", "unknown"):
            self._last_power_kw = 0.0
        else:
            try:
                self._last_power_kw = float(new) / 1000  # W → kW
            except (TypeError, ValueError):
                self._last_power_kw = 0.0
        self._last_power_time = now

    def _on_cable_disconnect(self, entity, attribute, old, new, kwargs):
        """Reset session energy tracking when the cable is removed."""
        self._reset_session_energy()

    def _reset_session_energy(self):
        self._last_power_kw = None
        self._last_power_time = None
        self.set_state(
            _SESSION_ENERGY,
            state=0.0,
            attributes={"unit_of_measurement": "kWh", "friendly_name": "Session energy"},
        )
        self.log("Session energy reset")

    def _sync_soc_override(self, soc: float):
        """Keep input_number.soc_override in sync with the real sensor."""
        self.call_service(
            "input_number/set_value",
            entity_id=_SOC_OVERRIDE,
            value=round(soc, 1),
        )

    def _read_soc_fallback(self):
        """Estimate SoC from override + accumulated session energy when sensor is unavailable."""
        override = self.get_state(_SOC_OVERRIDE)
        if override in (None, "unavailable", "unknown"):
            return None
        try:
            base_soc = float(override)
        except ValueError:
            return None

        try:
            session_kwh = float(self.get_state(_SESSION_ENERGY) or 0)
        except (TypeError, ValueError):
            session_kwh = 0.0

        estimated = min(100.0, base_soc + session_kwh / self.battery_kwh * 100)
        self.log(
            f"SoC sensor unavailable — override {base_soc:.0f}%"
            f" + {session_kwh:.2f} kWh session = {estimated:.0f}%",
            level="WARNING",
        )
        return estimated

    def _publish_plan(
        self,
        selected: list,
        soc_start: float,
        soc_target: float,
        deadline: datetime,
        warning: str = "",
    ):
        """Write the charge plan to sensor.charge_plan and www/charge_plan.json."""
        running_soc = soc_start
        lines = []
        json_slots = []
        for s in selected:
            running_soc = min(
                soc_target, running_soc + s["energy_kwh"] / self.battery_kwh * 100
            )
            start = s.get("start_time", s["slot"])
            duration = timedelta(hours=s["energy_kwh"] / s["power_kw"])
            end = start + duration
            lines.append(
                f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
                f"  {s['mode']}"
                f"  {s['effective_price']:.1f} ct/kWh"
                f"  -> {running_soc:.0f}% (+{s['energy_kwh']:.1f} kWh)"
            )
            json_slots.append({
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "date": start.strftime("%Y-%m-%d"),
                "mode": s["mode"],
                "effective_price": round(s["effective_price"], 1),
                "energy_kwh": round(s["energy_kwh"], 1),
                "soc_after": round(running_soc, 1),
            })

        plan_text = "\n".join(lines) if lines else "No charging slots planned"
        if warning:
            plan_text = warning + "\n\n" + plan_text
        self._publish_status(plan_text)

        self._write_plan_json(_PlanData(
            soc_start=round(soc_start, 1),
            soc_target=round(soc_target, 1),
            deadline=deadline,
            slots=json_slots,
            warning=warning or None,
        ))

    def _write_plan_json(self, plan: _PlanData):
        """Write the charge plan as JSON to /homeassistant/www/ for the HTML dashboard."""
        data = {
            "soc_start": plan.soc_start,
            "soc_target": plan.soc_target,
            "deadline": plan.deadline.isoformat(timespec="seconds") if plan.deadline else None,
            "warning": plan.warning,
            "status": plan.status,
            "updated": datetime.now().isoformat(timespec="seconds"),
            "slots": plan.slots,
        }
        try:
            _PLAN_JSON.parent.mkdir(parents=True, exist_ok=True)
            _PLAN_JSON.write_text(json.dumps(data), encoding="utf-8")
        except OSError as exc:
            self.log(f"Could not write plan JSON: {exc}", level="WARNING")

    def _publish_status(self, text: str):
        """Write a status message to sensor.charge_plan."""
        self.set_state(
            "sensor.charge_plan",
            state=text[:255],
            attributes={"plan": text, "friendly_name": "Charge Plan"},
        )

    def _read_soc(self):
        """Read current SoC from the EV sensor (%). Returns None if unavailable."""
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

    def _read_charge_minimum(self) -> float:
        """Read minimum SoC to charge to immediately (%). Returns 0.0 if unavailable."""
        value = self.get_state(self.charge_minimum_entity)
        if value in (None, "unavailable", "unknown"):
            return 0.0
        try:
            return float(value)
        except ValueError:
            return 0.0

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
