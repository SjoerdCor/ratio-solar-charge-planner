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

### Stap 1 — AppDaemon installeren (in Home Assistant)

Ga naar **Instellingen → Apps**, zoek op **AppDaemon** en klik op **Installeren**. Zet daarna "Starten bij opstarten" aan en klik op **Start**.

### Stap 2 — Terminal & SSH installeren (in Home Assistant)

Ga naar **Instellingen → Apps**, zoek op **Terminal & SSH** en installeer deze. Klik op **Start** en open de web-UI via **Openen**.

> Alle volgende stappen voer je uit in deze terminal.

### Stap 3 — Repo clonen (in Terminal)

Maak eerst een Personal Access Token aan op GitHub:
- Kies **Tokens (classic)**
- Vink alleen `repo` aan
- Stel een vervaldatum in (90 dagen aanbevolen)

Clone daarna de repo:

```bash
cd /config
git clone https://<jouw-token>@github.com/sjoerdcor/ratio-solar-charge-planner.git
```

### Stap 4 — Apps, helpers en dashboard instellen (in Terminal)

Deze stap maak je **eenmalig** symlinks zodat `git pull` voortaan genoeg is voor updates.

```bash
cd /config/ratio-solar-charge-planner && git pull && \
ln -sf /config/ratio-solar-charge-planner/appdaemon/apps/laadplanner \
   /addon_configs/a0d7b954_appdaemon/apps/laadplanner && \
mkdir -p /config/packages && \
ln -sf /config/ratio-solar-charge-planner/homeassistant/packages/laadplanner.yaml \
   /config/packages/laadplanner.yaml
```

Voeg daarna **eenmalig** de volgende secties toe aan `/config/configuration.yaml`. Als de `homeassistant:`-sectie al bestaat, voeg alleen de `packages:`-regel toe.

```bash
nano /config/configuration.yaml
```

```yaml
homeassistant:
  packages: !include_dir_named packages

lovelace:
  dashboards:
    ev-laden:
      mode: yaml
      title: EV-laden
      filename: ratio-solar-charge-planner/homeassistant/dashboard.yaml
      show_in_sidebar: true
      require_admin: false
```

Na het herstarten in Stap 6 verschijnt **EV-laden** in de zijbalk. Het dashboard wordt automatisch bijgewerkt bij elke `git pull`.

### Stap 5 — apps.yaml invullen (in Terminal)

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
    cable_sensor:       "binary_sensor.ratio_YOUR_SERIAL_vehicle_connected"
    charge_mode_select: "select.ratio_YOUR_SERIAL_charge_mode"
    charge_target:      "input_number.laad_doel"
    charge_by:          "input_datetime.laad_klaar_om"

  vehicle:
    battery_kwh: 77          # bruikbare accucapaciteit in kWh
    charging_power_kw: 11.0  # maximaal laadvermogen in kW

  tariff:
    grid:
      type: fixed
      price: 0.27       # EUR/kWh (default rate)
      zones:
        - hours: "22-6"
          price: 0.23   # EUR/kWh (night rate)
```

Sla op met `Ctrl+X → Y → Enter`.

#### Tarief configureren

Het tarief wordt opgegeven in hetzelfde formaat als [evcc](https://docs.evcc.io/docs/reference/configuration/tariffs).

- `price` is de standaardprijs in **EUR/kWh** (dus 0.27 = 27 ct/kWh).
- `zones` is optioneel. Zonder zones geldt de standaardprijs voor alle uren.
- Een zone overschrijft de standaardprijs voor de opgegeven uren:
  - `hours: "6-22"` — van 06:00 t/m 21:59 (einduur is niet inbegrepen)
  - `hours: "22-6"` — van 22:00 t/m 05:59 (kruist middernacht)
- Bij overlappende zones wint de **eerste** zone.
- Het evcc-veld `days` (dag-van-de-week per zone) wordt nog **niet** ondersteund — de app geeft een foutmelding als je dit gebruikt.

Heb je een enkelvoudig tarief (geen nacht- of daltarief), dan laat je `zones` weg:

```yaml
  tariff:
    grid:
      type: fixed
      price: 0.28
```

### Stap 6 — Home Assistant herstarten (in Home Assistant)

Ga naar **Instellingen → Systeem → Opnieuw opstarten**. Home Assistant laadt de helpers uit `configuration.yaml` en herstart AppDaemon automatisch.

### Stap 7 — Logs controleren (in Home Assistant)

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
cd /config/ratio-solar-charge-planner && git pull
```

De symlinks zorgen dat de app-code, helpers en het dashboard automatisch meekomen. Herstart Home Assistant alleen als er wijzigingen zijn in `homeassistant/packages/laadplanner.yaml`.

---

## Dashboard

Het dashboard staat in `homeassistant/dashboard.yaml` en wordt tijdens de installatie (Stap 4) automatisch gekoppeld. Na een `git pull` zijn wijzigingen direct zichtbaar na een HA-herstart.

Met **Herplan nu** bereken je het laadplan direct opnieuw, zonder te wachten op het volgende volle uur.

---

## Laadlogica

**Basisstand**: Ratio op `PureSolar` — laadt alleen op zonne-energie.

**Optimizer** (draait elk uur via AppDaemon):
- Berekent hoeveel kWh nog nodig is vóór de deadline
- Bouwt kandidaten per uur: `Smart` (vol netvermogen), `SmartSolar` (minimaal 1,4 kW, netsupplement indien zon tekortschiet) en `PureSolar` (alleen zon, minimaal 1,4 kW opwek vereist)
- Kiest de goedkoopste uren op basis van vaste tarieven en zonnepredictie
- Stelt de modus in voor het huidige uur

---

## Roadmap

- [x] Zonnepredictie via Forecast.Solar (vandaag + morgen)
- [x] Rolling-horizon laadplanner
- [x] AppDaemon integratie
- [ ] OpenMeteo als fallback (7 dagen vooruit)
- [ ] Dynamisch contract (Tibber prijsanalyse)
