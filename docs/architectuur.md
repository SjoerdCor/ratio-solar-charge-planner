# Architectuur

```
Tibber API (prijzen, 24-48u vooruit)
Forecast.Solar API (dag 1)
OpenMeteo API (dag 2-7, irradiantie)
Historische Nordpool data (seizoensgemiddelde dag 7+)
        │
        ▼
scripts/solar_forecast.py    ← zonnepredictie + caching
scripts/tibber_analyse.py    ← prijsanalyse
scripts/laadplanner.py       ← rolling-horizon optimizer
        │
        ▼
AppDaemon (appdaemon/apps/laadplanner/)
  - leest HA-entiteiten (SOC, vehicle_connected, P_zon)
  - roept optimizer aan
  - schrijft terug naar select.ratio_*_charge_mode
        │
        ▼
Home Assistant
  - uitvoering via entiteiten
  - dashboard / notificaties
        │
        ▼
Ratio Solar laadpaal (OCPP 1.6)
```

## HA-entiteiten

### Ratio Solar
| Entiteit | Type | Beschrijving |
|----------|------|-------------|
| `switch.ratio_<serial>_charging` | switch | Laden aan/uit |
| `select.ratio_<serial>_charge_mode` | select | Smart / SmartSolar / PureSolar |
| `binary_sensor.ratio_<serial>_vehicle_connected` | binary_sensor | Auto aangesloten |

### Volkswagen Connect
| Entiteit | Type | Beschrijving |
|----------|------|-------------|
| `sensor.volkswagen_<serial>_state_of_charge` | sensor | SOC in % |

### Input helpers (aan te maken in HA)
| Helper | Type | Standaard |
|--------|------|-----------|
| `input_number.laad_doel` | number | 80% |
| `input_datetime.laad_klaar_om` | datetime | volgend weekend 10:00 |
| `input_datetime.auto_nodig_op` | datetime | flexibel |
