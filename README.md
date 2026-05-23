# Thuisenergiemanagementsysteem — Zeist

Slim laden van een VW ID.3 op basis van zonne-opwek, Tibber-prijzen en een
rolling-horizon optimizer, aangestuurd via AppDaemon → Home Assistant → Ratio Solar laadpaal.

## Setup

| Component | Details |
|-----------|---------|
| Locatie | Zeist (52.09°N, 5.23°E) |
| Laadpaal | Ratio Solar (OCPP 1.6) |
| Auto | VW ID.3 (77 kWh) |
| Omvormer | GoodWe GW4200D-NS (verwacht ~2 maanden) |
| Zonnepanelen | 9 × 430 Wp (3.87 kWp totaal) |

### Dakvlakken
| Vlak | Panelen | kWp | Azimuth | Tilt |
|------|---------|-----|---------|------|
| ZO   | 6       | 2.58 | -45°   | 35°  |
| NO   | 3       | 1.29 | -135°  | 35°  |

Verwachte jaaropbrengst: ~2.400 kWh

## Projectstructuur

```
thuisenergie/
├── appdaemon/
│   └── apps/
│       └── laadplanner/        ← AppDaemon app (stap 3+)
├── scripts/
│   ├── solar_forecast.py       ← Forecast.Solar + OpenMeteo (stap 1)
│   ├── solar_forecast_plot.py  ← Visualisatie zonnepredictie
│   ├── tibber_analyse.py       ← Prijsvergelijking vast vs. dynamisch (stap 2)
│   └── laadplanner.py          ← Rolling-horizon optimizer (stap 3)
├── homeassistant/
│   └── input_helpers/          ← YAML voor input_number / input_datetime
├── data/
│   └── cache/                  ← Tijdelijk, niet in git
├── docs/
│   └── architectuur.md
├── config.example.yaml         ← Template voor API keys etc.
└── requirements.txt
```

## Roadmap

- [x] Stap 0: Projectopzet + bestaande code overgezet
- [ ] Stap 1: Zonnepredictie uitbreiden met OpenMeteo (7 dagen) + caching
- [ ] Stap 2: Tibber prijsanalyse (historisch, vast vs. dynamisch)
- [ ] Stap 3: Simpele laadplanner (rolling horizon, geen linprog)
- [ ] Stap 4: AppDaemon integratie
- [ ] Stap 5: Dynamisch contract + live optimizer

## Laadlogica (gewenst gedrag)

**Basisstand**: Ratio op `PureSolar`, VW-app laadlimiet 80% — regelt zichzelf.

**Uitzonderingen (AppDaemon)**:
- SOC < 30% → `Smart`, laad tot 50% (noodladen)
- SOC < 80% én deadline nadert → `Smart`
- Zonne-overschot 300–1400 W én deadline nadert → `SmartSolar`
- Na ingreep → terug naar `PureSolar`

**Effectieve prijs SmartSolar:**
```
effectieve_prijs = (1400W × marktprijs + P_zon × €0) / (1400 + P_zon)
```

## Benodigde API keys

Zie `config.example.yaml`. Kopieer naar `config.yaml` (staat in `.gitignore`).

- **Forecast.Solar**: geen key nodig (gratis, 12 req/uur)
- **OpenMeteo**: geen key nodig (gratis)
- **Tibber**: token via https://developer.tibber.com/

## Installatie (dev)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
