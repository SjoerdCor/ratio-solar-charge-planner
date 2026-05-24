"""
solar_forecast_plot.py
======================
Matplotlib visualisation of the solar forecast per roof plane.
"""

import sys
from datetime import datetime
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import yaml

_ROOT = Path(__file__).parent.parent
_AD   = _ROOT / "appdaemon" / "apps" / "laadplanner"
sys.path.insert(0, str(_AD))

import solar_forecast
from solar_forecast import fetch_roof_plane_forecast


def _load_config() -> dict:
    path = _ROOT / "appdaemon" / "apps" / "apps.yaml"
    if not path.exists():
        raise FileNotFoundError(
            "appdaemon/apps/apps.yaml not found — copy apps.yaml.example and fill it in."
        )
    return yaml.safe_load(path.read_text(encoding="utf-8"))["laadplanner"]


_cfg = _load_config()
solar_forecast.configure(
    latitude=_cfg["location"]["latitude"],
    longitude=_cfg["location"]["longitude"],
    roof_planes=_cfg["panels"],
    cache_dir=_ROOT / "data" / "cache",
)


def plot_forecast():
    """Plot per-period solar production per roof plane for today and tomorrow."""
    _, ax = plt.subplots(figsize=(12, 5))
    total: dict = {}

    colours = {"SE": "#f5a623", "NE": "#4a90e2"}

    for plane in _cfg["panels"]:
        data = fetch_roof_plane_forecast(plane["kwp"], plane["tilt"], plane["azimuth"])
        times  = [datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") for ts in data]
        values = [kwh * 1000 for kwh in data.values()]  # display as Wh for readability

        ax.plot(times, values, label=plane["name"],
                color=colours.get(plane["name"], "gray"), linewidth=2)

        for t, wh in zip(times, values):
            ts = t.strftime("%Y-%m-%d %H:%M:%S")
            total[ts] = total.get(ts, 0.0) + wh

    t_total = [datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") for ts in sorted(total)]
    w_total = [total[ts] for ts in sorted(total)]
    ax.plot(t_total, w_total, label="Total", color="green", linewidth=2.5, linestyle="--")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_xlabel("Time")
    ax.set_ylabel("Wh per period")
    ax.set_title("Solar forecast — Zeist")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    plot_forecast()
