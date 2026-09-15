"""Schlafkonto — akutes und chronisches Defizit.

Reines Python, keine HA-Abhängigkeiten.

Modell
------
Zwei Leaky-Integratoren über dieselbe Nachtbilanz, mit unterschiedlichen
Zeitkonstanten. Das entspricht der Erweiterung des Zwei-Prozess-Modells um einen
langsamen Schuldspeicher: zurückliegende Schlafverluste wirken schwächer als
kürzliche, verschwinden aber nicht schlagartig.

    D_t = D_(t-1) · exp(-Δt / τ) + effektive_bilanz_t

Positive Werte bedeuten **Defizit**, negative ein Guthaben.

Die beiden Speicher beantworten verschiedene Fragen und werden deshalb bewusst in
verschiedenen Einheiten ausgegeben:

* **akut** (τ≈3 Nächte) — *kumulierte* Schlafschuld in Minuten: „wie viel Schlaf
  fehlt mir gerade". Diese Zeitkonstante bildet die gemessene Erholungsdynamik ab:
  nach 10 Nächten à 6 h ist das Konto mit rund sieben bis acht Nächten à 8 h
  ausgeglichen — genau der Wert aus der Restriktionsforschung.
* **chronisch** (τ≈21 Nächte) — *mittleres Defizit pro Nacht* in Minuten: „wie
  stehe ich seit Wochen da". Ein kumulierter Wert wäre hier nicht interpretierbar,
  weil seine Größenordnung allein an der Zeitkonstante hinge.

Zwei Asymmetrien bilden gesicherte Befunde ab:

* ``recovery_efficiency`` < 1 — Erholung kostet mehr Schlaf, als verloren ging.
  Nach 10 Nächten à 6 h brauchte es rund 7 Nächte à 8 h zurück zur Baseline.
* ``credit_cap`` — Vorschlafen ("Sleep Banking") wirkt, schützt aber nur etwa
  zwei bis drei Tage. Das Guthaben wird gedeckelt, die Schuld nicht.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date

    from .models import SleepNight

#: Schlafbedarf in Minuten. Populationsmittel — individuell stark schwankend (~6–9 h).
DEFAULT_SLEEP_NEED_MIN = 480.0

#: Zeitkonstante des akuten Speichers in Nächten.
DEFAULT_TAU_ACUTE_NIGHTS = 3.0

#: Zeitkonstante des chronischen Speichers in Nächten.
DEFAULT_TAU_CHRONIC_NIGHTS = 21.0

#: Wirkungsgrad von Überschussschlaf beim Schuldabbau (< 1 = Erholung ist langsamer).
DEFAULT_RECOVERY_EFFICIENCY = 0.5

#: Obergrenze des Guthabens in Minuten (Sleep-Banking-Deckel, ~2–3 Tage Schutz).
DEFAULT_CREDIT_CAP_MIN = 120.0

#: Chronische Schuld gilt unterhalb dieses Werts als ausgeglichen.
DEFAULT_RECOVERY_TARGET_MIN = 30.0

#: Anrechnungsfaktor für Nickerchen. Heuristisch: Nickerchen wirken als
#: Gegenmaßnahme, eine Nickerchen-Minute ersetzt aber keine Nachtschlafminute.
NAP_CREDIT_FACTOR = 0.7

#: Mindestzahl erfasster Nächte, bevor der jeweilige Speicher ausgegeben wird.
MIN_NIGHTS_ACUTE = 3
MIN_NIGHTS_CHRONIC = 14

#: Obergrenze für die Vorwärtssimulation in :func:`nights_to_recovery`.
MAX_SIMULATED_NIGHTS = 90


@dataclass(frozen=True, slots=True)
class DebtResult:
    """Ergebnis der Kontoberechnung. ``None`` heißt: zu wenig Daten."""

    acute_min: float | None
    """Kumulierte akute Schlafschuld in Minuten (positiv = Defizit)."""

    chronic_min_per_night: float | None
    """Gewichtetes mittleres Defizit pro Nacht in Minuten."""

    nights_used: int
    last_balance_min: float | None
    """Bilanz der jüngsten Nacht (positiv = Defizit)."""


def effective_sleep_min(night: SleepNight) -> float | None:
    """Anrechenbarer Schlaf einer Nacht inklusive abgewerteter Nickerchen."""
    if night.total_min is None:
        return None
    return night.total_min + (night.nap_min or 0.0) * NAP_CREDIT_FACTOR


def night_balance_min(
    night: SleepNight, need_min: float, recovery_efficiency: float
) -> float | None:
    """Effektive Bilanz einer Nacht: positiv = Defizit, negativ = Guthaben."""
    slept = effective_sleep_min(night)
    if slept is None:
        return None
    balance = need_min - slept
    if balance < 0.0:
        # Überschuss zählt abgewertet — Erholung ist langsamer als der Verlust.
        balance *= recovery_efficiency
    return balance


def _accumulate(
    nights: list[SleepNight],
    *,
    today: date,
    tau_nights: float,
    need_min: float,
    recovery_efficiency: float,
    credit_cap_min: float | None,
) -> tuple[float, float, int]:
    """Leaky-Integrator über die Nächte.

    Gibt ``(gewichtete Summe, Summe der Gewichte, genutzte Nächte)`` zurück. Die
    Gewichtssumme erlaubt dem Aufrufer, statt der Kumulation ein gewichtetes
    Mittel zu bilden.

    Lücken werden ausschließlich als Zerfall behandelt — eine Nacht ohne Messung
    ist keine Nacht mit null Stunden Schlaf und darf das Konto nicht belasten.
    """
    usable = sorted((n for n in nights if n.has_duration), key=lambda n: n.date)
    if not usable:
        return 0.0, 0.0, 0

    debt = 0.0
    weight = 0.0
    previous: date | None = None
    for night in usable:
        if previous is not None:
            elapsed = (night.date - previous).days
            if elapsed > 0:
                decay = math.exp(-elapsed / tau_nights)
                debt *= decay
                weight *= decay
        balance = night_balance_min(night, need_min, recovery_efficiency)
        if balance is None:  # pragma: no cover - durch has_duration ausgeschlossen
            continue
        debt += balance
        if credit_cap_min is not None:
            debt = max(debt, -credit_cap_min)
        weight += 1.0
        previous = night.date

    # Bis heute weiter altern lassen, damit ein stehengebliebener Datenstrom
    # nicht dauerhaft einen alten Kontostand einfriert.
    if previous is not None:
        stale = (today - previous).days
        if stale > 0:
            decay = math.exp(-stale / tau_nights)
            debt *= decay
            weight *= decay

    return debt, weight, len(usable)


def compute(
    nights: list[SleepNight],
    *,
    today: date,
    need_min: float = DEFAULT_SLEEP_NEED_MIN,
    tau_acute: float = DEFAULT_TAU_ACUTE_NIGHTS,
    tau_chronic: float = DEFAULT_TAU_CHRONIC_NIGHTS,
    recovery_efficiency: float = DEFAULT_RECOVERY_EFFICIENCY,
    credit_cap_min: float = DEFAULT_CREDIT_CAP_MIN,
) -> DebtResult:
    """Akutes und chronisches Schlafkonto berechnen."""
    base = {
        "today": today,
        "need_min": need_min,
        "recovery_efficiency": recovery_efficiency,
    }
    # Der Sleep-Banking-Deckel gehört an den akuten Speicher: Vorschlafen schützt
    # kurzfristig, es lässt sich kein Guthaben über Wochen anhäufen.
    acute, _, used = _accumulate(
        nights, tau_nights=tau_acute, credit_cap_min=credit_cap_min, **base
    )
    # Der chronische Speicher wird als Mittel ausgegeben und braucht keinen Deckel.
    chronic_sum, chronic_weight, _ = _accumulate(
        nights, tau_nights=tau_chronic, credit_cap_min=None, **base
    )
    chronic_mean = chronic_sum / chronic_weight if chronic_weight > 0 else None

    latest = max((n for n in nights if n.has_duration), key=lambda n: n.date, default=None)
    last_balance = (
        night_balance_min(latest, need_min, recovery_efficiency) if latest is not None else None
    )

    return DebtResult(
        acute_min=acute if used >= MIN_NIGHTS_ACUTE else None,
        chronic_min_per_night=chronic_mean if used >= MIN_NIGHTS_CHRONIC else None,
        nights_used=used,
        last_balance_min=last_balance,
    )


def nights_to_recovery(
    acute_min: float,
    *,
    target_sleep_min: float,
    need_min: float = DEFAULT_SLEEP_NEED_MIN,
    tau_acute: float = DEFAULT_TAU_ACUTE_NIGHTS,
    recovery_efficiency: float = DEFAULT_RECOVERY_EFFICIENCY,
    credit_cap_min: float = DEFAULT_CREDIT_CAP_MIN,
    recovery_target_min: float = DEFAULT_RECOVERY_TARGET_MIN,
) -> int | None:
    """Wie viele Nächte à ``target_sleep_min`` bis das akute Konto ausgeglichen ist.

    ``None`` bedeutet: mit dieser Schlafdauer nicht innerhalb von
    :data:`MAX_SIMULATED_NIGHTS` erreichbar — typischerweise, weil ``target_sleep_min``
    den Bedarf nicht deckt.
    """
    if acute_min <= recovery_target_min:
        return 0

    balance = need_min - target_sleep_min
    if balance < 0.0:
        balance *= recovery_efficiency

    debt = acute_min
    decay = math.exp(-1.0 / tau_acute)
    for count in range(1, MAX_SIMULATED_NIGHTS + 1):
        debt = max(debt * decay + balance, -credit_cap_min)
        if debt <= recovery_target_min:
            return count
    return None
