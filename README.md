# Thuisenergiemanagementsysteem — Zeist

Slim laden van een VW ID.3 op basis van zonne-opwek en een rolling-horizon optimizer,
aangestuurd via AppDaemon → Home Assistant → Ratio Solar laadpaal.


## Projectstructuur

```
thuisenergie/
├── appdaemon/
│   ├── apps.yaml.example           ← template voor configuratie
│   └── apps/
│       └── laadplanner/            ← AppDaemon app + optimizer + zonnepredictie
│           ├── laadplanner_app.py
│           ├── optimizer.py
│           └── solar_forecast.py
├── scripts/
│   ├── laadplanner.py              ← CLI: plan handmatig bekijken
│   └── solar_forecast_plot.py      ← visualisatie zonnepredictie
└── data/
    └── cache/                      ← tijdelijk, niet in git
```

## Installatie

### Stap 1: HA Helpers aanmaken

Ga naar **Settings → Devices & Services → Helpers → Create Helper** en maak aan:

- Type **Number**, naam `laad_doel`, bereik 0–100, eenheid %, standaard 80
- Type **Date and/or time**, naam `laad_klaar_om`, type: datum én tijd

### Stap 2: Entity ID's opzoeken

Ga naar **Settings → Developer Tools → States** en zoek op:

- `state_of_charge` of `battery_level` → noteer de volledige entity ID van je auto
- `charge_mode` → noteer de volledige entity ID van de Ratio laadpaal

### Stap 3: AppDaemon installeren

- Ga naar **Settings → Apps** (vroeger "Add-ons")
- Zoek op **AppDaemon** en klik op Install
- Klik op Start en zet "Start on boot" aan

### Stap 4: SSH & Terminal installeren

- Ga naar **Settings → Apps**
- Zoek op **Terminal & SSH** en installeer deze
- Klik op Start en dan Open Web UI

### Stap 5: Repo clonen

Maak eerst een Personal Access Token aan op GitHub:

- Kies Tokens (classic)
- Vink alleen `repo` aan
- Stel een vervaldatum in (90 dagen aanbevolen)

Clone dan de repo via de terminal:

```bash
cd /config
git clone https://<jouw-token>@github.com/<jouw-gebruikersnaam>/thuisenergie.git
```

### Stap 6: Apps kopiëren

> **Let op:** AppDaemon gebruikt `/addon_configs/a0d7b954_appdaemon/apps/` als app-map.
> Dit is de vaste locatie voor de AppDaemon add-on in Home Assistant OS.

```bash
cd /config/thuisenergie && git pull && \
cp -r /config/thuisenergie/appdaemon/apps/laadplanner/* \
   /addon_configs/a0d7b954_appdaemon/apps/laadplanner/
```

### Stap 7: apps.yaml invullen

```bash
nano /addon_configs/a0d7b954_appdaemon/apps/apps.yaml
```

Vul de entity ID's in die je in stap 2 hebt gevonden. Sla op met `Ctrl+X → Y → Enter`.

Controleer ook dat `module` op `laadplanner.laadplanner_app` staat:

```yaml
laadplanner:
  module: laadplanner.laadplanner_app
  class: ChargeScheduler
  ...
```

### Stap 8: AppDaemon herstarten

Ga naar **Settings → Apps → AppDaemon** en klik op het herstart-icoontje.

### Stap 9: Controleer de logs

Ga naar **Settings → Apps → AppDaemon → Logboek**.
Je zou dit moeten zien:

```
INFO AppDaemon: Starting apps: ['laadplanner']
INFO AppDaemon: Calling initialize() for laadplanner
INFO laadplanner: ChargeScheduler initialised
```

Als er errors staan, zijn de meest voorkomende oorzaken:

- Verkeerde entity ID's in `apps.yaml`
- `module` staat niet op `laadplanner.laadplanner_app`
- Helpers uit stap 1 zijn niet aangemaakt

## Updates

Na een wijziging in de code:

```bash
cd /config/thuisenergie && git pull && \
cp -r /config/thuisenergie/appdaemon/apps/laadplanner/* \
   /addon_configs/a0d7b954_appdaemon/apps/laadplanner/
```

Herstart daarna AppDaemon.

## Configuratie

### Helpers aanmaken

Maak de volgende helpers aan via **Settings → Devices & Services → Helpers → Create Helper**:

| Helper | Type | Beschrijving |
|--------|------|-------------|
| `input_number.laad_doel` | Number (0–100, eenheid %) | Laad de auto tot dit percentage |
| `input_datetime.laad_klaar_om` | Date and/or time (datum + tijd) | Deadline voor het bereiken van laad_doel |
| `input_button.herplan_laadplanner` | Button | Herbereken het laadplan direct |

### Dashboard

Maak een dashboard aan (of voeg kaarten toe aan een bestaand dashboard) via **Settings → Dashboards**.

**Laadplan weergeven** (Markdown card):
```yaml
type: markdown
content: >
  ## Laadplan
  {{ state_attr('sensor.laadplan', 'plan') }}
```

**Instellingen en knop** (Entities card):
```yaml
type: entities
title: Laadplanner
entities:
  - entity: input_number.laad_doel
    name: Doel SoC
  - entity: input_datetime.laad_klaar_om
    name: Klaar om
  - entity: input_button.herplan_laadplanner
    name: Herplan nu
```

Na het aanpassen van doel SoC of deadline: druk op **Herplan nu** om het plan direct bij te werken. Het plan wordt sowieso elk heel uur automatisch herberekend.

## Lokaal ontwikkelen

```bash
uv run python scripts/laadplanner.py --soc 25 --target 80 --days 2
uv run python scripts/solar_forecast_plot.py
```

Vereist `appdaemon/apps/apps.yaml` (kopieer van `apps.yaml.example` en vul in).

## Laadlogica

**Basisstand**: Ratio op `PureSolar` — laadt alleen op zonne-energie.

**Optimizer** (draait elk uur via AppDaemon):
- Berekent hoeveel kWh nog nodig is vóór de deadline
- Bouwt kandidaten per uur: `Smart` (11 kW net) en `SmartSolar` (1.4 kW net + zon)
- Kiest de goedkoopste uren (vaste tarieven: 27 ct/kWh dag, 23 ct/kWh nacht)
- Stelt de modus in voor het huidige uur

**Effectieve prijs SmartSolar:**
```
effectieve_prijs = (1.4 kW × tarief) / (1.4 kW + P_zon)
```

## Roadmap

- [x] Zonnepredictie via Forecast.Solar (vandaag + morgen)
- [x] Rolling-horizon laadplanner
- [x] AppDaemon integratie
- [ ] OpenMeteo als fallback (7 dagen vooruit)
- [ ] Dynamisch contract (Tibber prijsanalyse)
