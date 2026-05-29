# Smart EV Charging with Ratio Solar

AppDaemon app for Home Assistant that intelligently controls your **Ratio Solar charge point** based on a solar production forecast and a cheapest-first strategy.

## Why?

Common solutions like evcc or the built-in HA charging integrations control a charge point based on power: charge when there's enough solar, otherwise don't. The Ratio Solar has a unique **SmartSolar** mode that always combines a small amount of grid power with solar energy — so you benefit from your panels even at low production levels, something generic solutions miss.

This app makes full use of all three Ratio Solar modes. Based on an hourly solar forecast (Forecast.Solar) it picks the cheapest combination each hour, so the car is charged on time at minimum cost.

## Requirements

- Home Assistant (OS or Supervised)
- Ratio Solar charge point, connected via the Ratio integration
- Electric vehicle — a SoC sensor in Home Assistant is recommended but not required (see [SoC fallback](#soc-fallback))
- Solar panels with production data

---

## Installation

### Step 1 — Install prerequisites (in Home Assistant)

**Ratio integration** — Install via HACS: add [home-assistant-ratio](https://github.com/aaearon/home-assistant-ratio) as a custom repository (category: Integration). This connects your charge point to Home Assistant.

**AppDaemon** — Go to **Settings → Add-ons**, search for **AppDaemon** and click **Install**. Enable "Start on boot" and click **Start**.

**Terminal & SSH** — Go to **Settings → Add-ons**, search for **Terminal & SSH** and install it. Click **Start** and open the web UI via **Open**.

> Steps 2–6 are run in the Terminal & SSH terminal.

### Step 2 — Clone the repo (in Terminal)

```bash
cd /config
git clone https://github.com/sjoerdcor/ratio-solar-charge-planner.git
```

### Step 3 — Set up apps, helpers and dashboard (in Terminal)

This step creates **one-time symlinks** so that `git pull` is enough for all future updates.

```bash
cd /config/ratio-solar-charge-planner && git pull && \
ln -sf /homeassistant/ratio-solar-charge-planner/apps/charger \
   /addon_configs/a0d7b954_appdaemon/apps/charger && \
mkdir -p /config/packages && \
ln -sf /config/ratio-solar-charge-planner/homeassistant/packages/charger.yaml \
   /config/packages/charger.yaml
```

Then add the following sections **once** to `/config/configuration.yaml`. If the `homeassistant:` section already exists, only add the `packages:` line.

```bash
nano /config/configuration.yaml
```

```yaml
homeassistant:
  packages: !include_dir_named packages

lovelace:
  dashboards:
    ev-charging:
      mode: yaml
      title: EV Charging
      filename: ratio-solar-charge-planner/homeassistant/dashboard.yaml
      show_in_sidebar: true
      require_admin: false
```

After restarting in Step 5, **EV Charging** appears in the sidebar. The dashboard updates automatically with every `git pull`.

### Step 4 — Fill in apps.yaml (in Terminal)

```bash
nano /addon_configs/a0d7b954_appdaemon/apps/apps.yaml
```

You need two values from Home Assistant — look them up via **Settings → Developer tools → States**:

- **Ratio serial number**: search for `charge_mode` and note the entity ID. It looks like `select.ratio_P000000012345_charge_mode` — the part between `ratio_` and `_charge_mode` is your serial number.
- **SoC sensor**: search for `state_of_charge` or `battery_level` to find your EV's battery sensor.

Use the template below and fill in your values:

```yaml
charger:
  module: charger.charger_app
  class: ChargeScheduler

  panels:
    - name: SE
      kwp: 2.58
      azimuth: -45       # degrees from south: 0=S, -90=E, +90=W, -45=SE, +45=SW
      tilt: 35           # degrees from horizontal: 0=flat, 90=vertical
    - name: NE
      kwp: 1.29
      azimuth: -135      # -135=NE
      tilt: 35

  ratio_serial: "YOUR_SERIAL"
  soc_sensor:   "sensor.YOUR_EV_state_of_charge"

  vehicle:
    battery_kwh: 77          # usable battery capacity in kWh
    charging_power_kw: 11.0  # maximum charging power in kW

  tariff:
    grid:
      type: fixed
      price: 0.27       # EUR/kWh (default rate)
      zones:
        - hours: "22-6"
          price: 0.23   # EUR/kWh (night rate)
```

Save with `Ctrl+X → Y → Enter`.

#### Configuring the panels

The app uses your home location together with the panel orientation to request an hourly solar forecast from Forecast.Solar. It reads the coordinates automatically from `zone.home` in Home Assistant — the location you set during the initial HA setup.

Each entry under `panels` describes one roof section (array). You can add as many as you have.

| Field | What to fill in |
|---|---|
| `name` | Any label — used only in logs |
| `kwp` | Peak power of the array in kWp (kilowatt-peak) |
| `azimuth` | Direction the panels face, in degrees from south: `0` = south, `-90` = east, `+90` = west, `-45` = south-east, `+45` = south-west, `-135` = north-east |
| `tilt` | Angle from horizontal: `0` = flat roof, `35` = typical sloped roof, `90` = vertical wall |

If your roof has two differently oriented sections (e.g. one facing SE and one facing NE), list both — the app combines their forecasts.

**No solar panels?** You can omit the `panels` key entirely or leave the list empty:

```yaml
  panels: []
```

The app still runs, but without a solar forecast it can only schedule Smart (grid) charging — PureSolar and SmartSolar modes are never used.

#### Configuring the tariff

The tariff format is the same as [evcc](https://docs.evcc.io/docs/reference/configuration/tariffs).

- `price` is the default price in **EUR/kWh** (so 0.27 = 27 ct/kWh).
- `zones` is optional. Without zones the default price applies to all hours.
- A zone overrides the default price for the specified hours:
  - `hours: "6-22"` — from 06:00 to 21:59 (end hour not included)
  - `hours: "22-6"` — from 22:00 to 05:59 (crosses midnight)
- When zones overlap, the **first** zone wins.
- The evcc field `days` (day-of-week per zone) is **not yet supported** — the app raises an error if you use it.

For a flat rate (no night or off-peak tariff), omit `zones`:

```yaml
  tariff:
    grid:
      type: fixed
      price: 0.28
```

### Step 5 — Restart Home Assistant (in Home Assistant)

Go to **Settings → System → Restart**. Home Assistant loads the helpers from `configuration.yaml` and restarts AppDaemon automatically.

### Step 6 — Check the logs (in Home Assistant)

Go to **Settings → Add-ons → AppDaemon → Log**. You should see:

```
INFO AppDaemon: Starting apps: ['charger']
INFO AppDaemon: Calling initialize() for charger
INFO charger: ChargeScheduler initialised
INFO charger: Replanning...
```

---

## Updates

```bash
cd /config/ratio-solar-charge-planner && git pull
```

The symlinks ensure that the app code, helpers and dashboard all update automatically. Only restart Home Assistant if there are changes to `homeassistant/packages/charger.yaml`.

---

## Dashboard

The dashboard is in `homeassistant/dashboard.yaml` and is linked automatically during installation (Step 3). Changes are visible after a `git pull` and a HA restart.

Use **Replan** to recalculate the charge plan immediately, without waiting for the next full hour.

**Charge immediately to** sets a minimum SoC that must be reached as soon as possible using grid (Smart) charging, before the optimizer takes over for the rest. Set it to the minimum you need to be able to drive, for example 30% if that covers your daily commute. Set to 0 to disable (default).

**Current SoC (fallback)** shows the SoC value the app uses when the real sensor is unavailable. When the sensor works, this field is kept in sync automatically. When it doesn't, you can set it manually before plugging in. The timestamp shows when it was last updated.

---

## Charging logic

**Default mode**: Ratio set to `PureSolar` — charges on solar only.

**Optimizer** (runs every hour via AppDaemon):
- Calculates how many kWh are still needed before the deadline
- Builds candidates per hour: `Smart` (full grid power), `SmartSolar` (minimum 1.4 kW, grid supplement when solar falls short) and `PureSolar` (solar only, requires at least 1.4 kW production)
- If a minimum SoC is set and not yet reached, fills the earliest Smart slots first to cover that amount (phase 1)
- Picks the cheapest remaining hours for the rest of the energy (phase 2)
- Sets the mode for the current hour

---

## SoC fallback

The app reads the current state of charge from the sensor configured as `soc_sensor`. Some manufacturers restrict API access in a way that makes this sensor permanently unavailable — Volkswagen is a known example.

When the sensor is unavailable, the app falls back to `input_number.soc_override`:

- **When the real sensor works**: `soc_override` is kept in sync automatically on every replan.
- **When the sensor is unavailable**: the app estimates SoC from `soc_override` plus the energy charged during the current session, measured via the charger's power sensor.
- **If the sensor is never available**: set `soc_override` manually in the dashboard each time you plug in. The app tracks charging progress from that starting point.

The dashboard shows when `soc_override` was last updated so you can tell whether the value is current.

---

## Roadmap

- [x] Solar forecast via Forecast.Solar (today + tomorrow)
- [x] Rolling-horizon charge planner
- [x] AppDaemon integration
- [ ] OpenMeteo as fallback (7 days ahead)
- [ ] Dynamic tariff (Tibber)

---

## Acknowledgements

This app depends on the [Ratio integration for Home Assistant](https://github.com/aaearon/home-assistant-ratio) by [@aaearon](https://github.com/aaearon), which is built on his [aioratio](https://github.com/aaearon/aioratio) Python library. Without that work, controlling the Ratio charge point from Home Assistant would not be possible.

---

## Disclaimer

This project is an independent community effort and is not affiliated with, endorsed by, or in any way officially connected to Ratio. Use at your own risk.

---

## License

MIT — free to use, modify and distribute. No warranties, no liability. See [LICENSE](LICENSE).
