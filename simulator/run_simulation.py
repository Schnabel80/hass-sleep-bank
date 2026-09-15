"""Modellvalidierung ohne Home Assistant.

Aufruf:

    uv run python simulator/run_simulation.py

Rechnet die Kennzahlen für mehrere synthetische Schläfertypen durch und gibt sie
als Tabelle aus. Dient der Kalibrierung der Vorgabewerte, bevor echte Daten
vorliegen — und als Regressionsschutz beim Schrauben an den Zeitkonstanten.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))

from sleep_bank import debt, physiology, readiness, regularity
from sleepers import SLEEPERS, generate

DAYS = 90
START = date(2026, 1, 1)


def fmt(value: float | None, digits: int = 0, suffix: str = "") -> str:
    """Zahl oder ein sichtbares „nicht verfügbar"."""
    if value is None:
        return "      –"
    return f"{value:7.{digits}f}{suffix}"


def evaluate(nights: list, today: date) -> dict[str, float | None]:
    """Alle Kennzahlen für eine Nachtserie berechnen."""
    d = debt.compute(nights, today=today)
    r = regularity.compute(nights, today=today)
    p = physiology.compute(nights, today=today)
    ready = readiness.compute(acute_debt_min=d.acute_min, recovery_z=p.recovery_z, sri=r.sri)
    return {
        "akut": d.acute_min,
        "chronisch": d.chronic_min_per_night,
        "SRI": r.sri,
        "jetlag": r.social_jetlag_hours,
        "streuung": r.midpoint_variability_min,
        "erholung": p.recovery_z,
        "bereitschaft": ready.score,
    }


def main() -> None:
    """Alle Schläfertypen durchrechnen und ausgeben."""
    end = START + timedelta(days=DAYS - 1)
    header = (
        f"{'Schläfertyp':22s} {'akut':>8s} {'chron.':>8s} {'SRI':>8s} "
        f"{'Jetlag':>8s} {'Streuung':>9s} {'Erhol.':>8s} {'Bereit.':>8s}"
    )
    print(f"\n{DAYS} Nächte, Auswertung am {end}\n")
    print(header)
    print("-" * len(header))

    for name in SLEEPERS:
        nights = generate(name, days=DAYS, start=START)
        m = evaluate(nights, end)
        print(
            f"{name:22s} {fmt(m['akut'])} {fmt(m['chronisch'])} {fmt(m['SRI'], 1)} "
            f"{fmt(m['jetlag'], 2)} {fmt(m['streuung'])}  {fmt(m['erholung'], 2)} "
            f"{fmt(m['bereitschaft'])}"
        )

    print("\nVerlauf des Typs „harte Wochen“ (14 Nächte à 8 h, 14 à 6 h, dann 9 h):\n")
    nights = generate("harte Wochen", days=56, start=START)
    print(f"{'Tag':>4s} {'Nacht':>7s} {'akut':>8s} {'chronisch':>10s} {'bereit':>8s}")
    print("-" * 42)
    for index in range(6, 56, 2):
        window = nights[: index + 1]
        today = START + timedelta(days=index)
        m = evaluate(window, today)
        print(
            f"{index + 1:4d} {window[-1].total_min:7.0f} {fmt(m['akut'])} "
            f"{fmt(m['chronisch'], 0)}   {fmt(m['bereitschaft'])}"
        )
    print()


if __name__ == "__main__":
    main()
