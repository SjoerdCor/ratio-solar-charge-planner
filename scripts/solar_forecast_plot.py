"""
solar_forecast_plot.py
======================
Matplotlib visualisatie van de zonnepredictie per dakvlak.
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from solar_forecast import haal_dakvlak_uurwaarden, DAKVLAKKEN

def plot_voorspelling():
    """Plot per-periode zonneopwek per dakvlak voor vandaag en morgen."""
    _, ax = plt.subplots(figsize=(12, 5))
    totaal: dict = {}

    kleuren = {"ZO": "#f5a623", "NO": "#4a90e2"}

    for vlak in DAKVLAKKEN:
        data = haal_dakvlak_uurwaarden(vlak["kwp"], vlak["tilt"], vlak["azimuth"])
        tijden = [datetime.strptime(t, "%Y-%m-%d %H:%M:%S") for t in data]
        waarden = list(data.values())

        ax.plot(tijden, waarden, label=vlak["naam"],
                color=kleuren.get(vlak["naam"], "gray"), linewidth=2)

        for t, w in zip(tijden, waarden):
            ts = t.strftime("%Y-%m-%d %H:%M:%S")
            totaal[ts] = totaal.get(ts, 0) + w

    # Totaallijn
    t_tot = [datetime.strptime(t, "%Y-%m-%d %H:%M:%S") for t in sorted(totaal)]
    w_tot = [totaal[t] for t in sorted(totaal)]
    ax.plot(t_tot, w_tot, label="Totaal", color="green", linewidth=2.5, linestyle="--")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_xlabel("Tijd")
    ax.set_ylabel("Wh per periode")
    ax.set_title("Zonnepredictie vandaag — Zeist")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    plot_voorspelling()
