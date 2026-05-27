# Slim laden met Ratio Solar

AppDaemon-app voor Home Assistant die je **Ratio Solar laadpaal** slim aanstuurt op basis van een zonneopwekvoorspelling en een goedkoopste-eerst-strategie.

## Waarom?

Gangbare oplossingen zoals evcc of de ingebouwde HA-laadintegraties sturen een laadpaal aan op basis van vermogen: laad als er genoeg zon is, anders niet. De Ratio Solar heeft echter een unieke **SmartSolar**-stand die altijd een klein stukje netstroom combineert met zonne-energie — waardoor je ook bij beperkte opwek voordeel haalt uit je zonnepanelen, iets wat generieke oplossingen missen.

Deze app benut alle drie de standen van de Ratio Solar optimaal. Op basis van een uurlijkse zonneopwekvoorspelling (Forecast.Solar) kiest hij elk uur de goedkoopste combinatie, zodat de auto op tijd opgeladen is tegen minimale kosten.

## Vereisten

- Home Assistant (OS of Supervised)
- Ratio Solar laadpaal, gekoppeld via de Ratio-integratie
- Elektrische auto met SoC-sensor in Home Assistant (de meeste moderne EV's bieden dit)
- Zonnepanelen met bijbehorende opwekgegevens

---

## Installatie

### Stap 1 — Helpers aanmaken (in Home Assistant)

Ga naar **Instellingen → Apparaten & diensten → Hulpstukken → Hulpstuk aanmaken** en maak drie hulpstukken aan:

| Naam | Type | Instellingen |
|------|------|-------------|
| `laad_doel` | Getal | Minimaal 0, maximaal 100, eenheid % |
| `laad_klaar_om` | Datum en/of tijd | Schakel "Datum opnemen" én "Tijd opnemen" in |
| `herplan_laadplanner` | Knop | Geen extra instellingen nodig |

### Stap 2 — AppDaemon installeren (in Home Assistant)

Ga naar **Instellingen → Apps**, zoek op **AppDaemon** en klik op **Installeren**. Zet daarna "Starten bij opstarten" aan en klik op **Start**.

### Stap 3 — Terminal & SSH installeren (in Home Assistant)

Ga naar **Instellingen → Apps**, zoek op **Terminal & SSH** en installeer deze. Klik op **Start** en open de web-UI via **Openen**.

> Alle volgende stappen voer je uit in deze terminal.

### Stap 4 — Repo clonen (in Terminal)

Maak eerst een Personal Access Token aan op GitHub:
- Kies **Tokens (classic)**
- Vink alleen `repo` aan
- Stel een vervaldatum in (90 dagen aanbevolen)

Clone daarna de repo:

```bash
cd /config
git clone https://<jouw-token>@github.com/sjoerdcor/thuisenergie.git
```

### Stap 5 — Apps kopiëren (in Terminal)

```bash
cd /config/thuisenergie && git pull && \
cp -r appdaemon/apps/laadplanner \
   /addon_configs/a0d7b954_appdaemon/apps/
```

### Stap 6 — apps.yaml invullen (in Terminal)

```bash
nano /addon_configs/a0d7b954_appdaemon/apps/apps.yaml
```

Zoek de entiteits-ID's op via **Instellingen → Ontwikkelaarshulpmiddelen → Staten** in Home Assistant:
- Zoek op `state_of_charge` of `battery_level` → SoC-sensor van je auto
- Zoek op `charge_mode` → modus-selector van de Ratio laadpaal

Gebruik onderstaand sjabloon en vul de juiste ID's in:

```yaml
laadplanner:
  module: laadplanner.laadplanner_app
  class: ChargeScheduler

  location:
    latitude: 52.09      # jouw breedtegraad
    longitude: 5.23      # jouw lengtegraad

  panels:
    - name: SE
      kwp: 2.58
      azimuth: -45       # graden t.o.v. het zuiden; west = positief, oost = negatief
      tilt: 35
    - name: NE
      kwp: 1.29
      azimuth: -135
      tilt: 35

  entities:
    soc_sensor:         "sensor.volkswagen_YOUR_SERIAL_state_of_charge"
    charge_mode_select: "select.ratio_YOUR_SERIAL_charge_mode"
    charge_target:      "input_number.laad_doel"
    charge_by:          "input_datetime.laad_klaar_om"

  vehicle:
    battery_kwh: 77          # bruikbare accucapaciteit in kWh
    charging_power_kw: 11.0  # maximaal laadvermogen in kW

  fixed_rate:
    day_rate_ct:   27.0   # ct/kWh, 06:00–22:00
    night_rate_ct: 23.0   # ct/kWh, 22:00–06:00
```

Sla op met `Ctrl+X → Y → Enter`.

### Stap 7 — AppDaemon herstarten (in Home Assistant)

Ga naar **Instellingen → Apps → AppDaemon** en klik op het herstart-icoontje.

### Stap 8 — Logs controleren (in Home Assistant)

Ga naar **Instellingen → Apps → AppDaemon → Logboek**. Je zou dit moeten zien:

```
INFO AppDaemon: Starting apps: ['laadplanner']
INFO AppDaemon: Calling initialize() for laadplanner
INFO laadplanner: ChargeScheduler initialised
INFO laadplanner: Replanning...
```

---

## Updates

```bash
cd /config/thuisenergie && git pull && \
cp -r appdaemon/apps/laadplanner \
   /addon_configs/a0d7b954_appdaemon/apps/
```

Herstart daarna AppDaemon (zie Stap 7).

---

## Dashboard

Voeg de volgende kaarten toe via **Instellingen → Dashboards → Bewerken**.

**Laadplan weergeven** (Markdown-kaart):
```yaml
type: markdown
content: >
  ## Laadplan
  {{ state_attr('sensor.laadplan', 'plan') }}
```

**Instellingen en knoppen** (Entiteitskaart):
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

Met **Herplan nu** bereken je het laadplan direct opnieuw, zonder te wachten op het volgende volle uur.

---

## Lokaal ontwikkelen

```bash
uv run python scripts/laadplanner.py --soc 25 --target 80 --days 2
uv run python scripts/solar_forecast_plot.py
```

Vereist `appdaemon/apps/apps.yaml` (kopieer van `appdaemon/apps.yaml.example` en vul in).

---

## Laadlogica

**Basisstand**: Ratio op `PureSolar` — laadt alleen op zonne-energie.

**Optimizer** (draait elk uur via AppDaemon):
- Berekent hoeveel kWh nog nodig is vóór de deadline
- Bouwt kandidaten per uur: `Smart` (vol netvermogen) en `SmartSolar` (1,4 kW net + zon)
- Kiest de goedkoopste uren op basis van vaste tarieven en zonnepredictie
- Stelt de modus in voor het huidige uur


---

## Roadmap

- [x] Zonnepredictie via Forecast.Solar (vandaag + morgen)
- [x] Rolling-horizon laadplanner
- [x] AppDaemon integratie
- [ ] OpenMeteo als fallback (7 dagen vooruit)
- [ ] Dynamisch contract (Tibber prijsanalyse)
