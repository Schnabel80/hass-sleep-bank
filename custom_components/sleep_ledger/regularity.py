"""Regularitätsmetriken — Sleep Regularity Index, Schlafmitte, Social Jetlag.

Reines Python, keine HA-Abhängigkeiten.

Warum diese Metriken zentral sind: In der UK-Biobank-Kohorte (n = 60.977,
Aktigraphie) sagte die **Regelmäßigkeit** des Schlafs die Gesamtmortalität
besser voraus als die Schlafdauer. Die Regularität ist hier deshalb keine
Zusatzinformation, sondern gleichrangig mit dem Schlafkonto.

Alle Funktionen liefern ``None``, wenn die Datenlage nicht trägt. Eine Zahl aus
zu wenigen oder lückenhaften Nächten wäre schlimmer als keine Zahl.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import SleepNight

#: Epochenlänge in Minuten für den SRI. 5 min ist der übliche Kompromiss
#: zwischen Auflösung und Robustheit gegenüber Zeitstempel-Rauschen.
EPOCH_MINUTES = 5

#: Epochen pro Tag.
EPOCHS_PER_DAY = 24 * 60 // EPOCH_MINUTES

#: Mindestzahl aufeinanderfolgender auswertbarer Tagespaare für den SRI.
MIN_SRI_PAIRS = 10

#: Mindestzahl Nächte für Schlafmitte-Streuung und Social Jetlag.
MIN_MIDPOINT_SAMPLES = 7
MIN_SOCIAL_JETLAG_SAMPLES = 4

#: Standardfenster in Tagen.
DEFAULT_SRI_WINDOW_DAYS = 30
DEFAULT_MIDPOINT_WINDOW_DAYS = 14

#: Wochentage, die standardmäßig als frei gelten (Samstag, Sonntag).
DEFAULT_FREE_DAYS = frozenset({5, 6})


@dataclass(frozen=True, slots=True)
class RegularityResult:
    """Ergebnis der Regularitätsberechnung. ``None`` heißt: zu wenig Daten."""

    sri: float | None
    """Sleep Regularity Index, -100 (völlig unregelmäßig) bis +100 (identisch)."""

    midpoint_minutes: float | None
    """Mittlere Schlafmitte als Minuten nach Mitternacht (zirkuläres Mittel)."""

    midpoint_variability_min: float | None
    """Zirkuläre Standardabweichung der Schlafmitte in Minuten."""

    social_jetlag_hours: float | None
    """Betrag der Differenz der Schlafmitte zwischen freien und Arbeitstagen."""

    sri_pairs: int
    """Zahl der ausgewerteten Tagespaare — Transparenz über die Datenbasis."""


# -- zirkuläre Statistik ----------------------------------------------------------
#
# Uhrzeiten sind zyklisch: 23:50 und 00:10 liegen 20 Minuten auseinander, nicht
# 23 Stunden 40. Ein arithmetisches Mittel über Minuten-nach-Mitternacht wäre bei
# Schlafmitten um Mitternacht grob falsch, deshalb durchgängig Vektormittel.

_MINUTES_PER_DAY = 24 * 60


def _to_angle(minutes: float) -> float:
    return 2.0 * math.pi * (minutes % _MINUTES_PER_DAY) / _MINUTES_PER_DAY


def circular_mean_minutes(values: list[float]) -> float | None:
    """Zirkuläres Mittel von Uhrzeiten in Minuten nach Mitternacht."""
    if not values:
        return None
    sin_sum = sum(math.sin(_to_angle(v)) for v in values)
    cos_sum = sum(math.cos(_to_angle(v)) for v in values)
    if abs(sin_sum) < 1e-12 and abs(cos_sum) < 1e-12:
        return None
    angle = math.atan2(sin_sum / len(values), cos_sum / len(values))
    return (angle / (2.0 * math.pi) * _MINUTES_PER_DAY) % _MINUTES_PER_DAY


def circular_std_minutes(values: list[float]) -> float | None:
    """Zirkuläre Standardabweichung von Uhrzeiten in Minuten."""
    if len(values) < 2:
        return None
    sin_mean = sum(math.sin(_to_angle(v)) for v in values) / len(values)
    cos_mean = sum(math.cos(_to_angle(v)) for v in values) / len(values)
    resultant = math.hypot(sin_mean, cos_mean)
    if resultant <= 1e-12:
        return _MINUTES_PER_DAY / 4.0
    std_rad = math.sqrt(max(0.0, -2.0 * math.log(min(1.0, resultant))))
    return std_rad / (2.0 * math.pi) * _MINUTES_PER_DAY


def circular_difference_minutes(a: float, b: float) -> float:
    """Kürzester Abstand zweier Uhrzeiten in Minuten (0 … 720)."""
    diff = abs(a - b) % _MINUTES_PER_DAY
    return min(diff, _MINUTES_PER_DAY - diff)


# -- Sleep Regularity Index -------------------------------------------------------


def _sleep_intervals(nights: list[SleepNight]) -> list[tuple[datetime, datetime]]:
    """Schlafintervalle der Nächte, die verwertbare Zeiten haben."""
    return sorted(
        (night.onset_time, night.wake_time)
        for night in nights
        if night.has_timing
        and night.onset_time is not None
        and night.wake_time is not None
        and night.wake_time > night.onset_time
    )


def _day_states(day: date, intervals: list[tuple[datetime, datetime]]) -> list[bool]:
    """Binäre Schlaf/Wach-Folge eines Kalendertags in Epochen."""
    # Naive vs. aware datetimes dürfen sich nicht mischen; die Nächte liefern
    # durchgängig dasselbe Format, deshalb wird es hier vom ersten Intervall übernommen.
    tzinfo = intervals[0][0].tzinfo if intervals else None
    base = datetime.combine(day, time.min, tzinfo=tzinfo)
    states = []
    for epoch in range(EPOCHS_PER_DAY):
        # Epochenmitte prüfen — robuster gegen Intervallgrenzen als der Rand.
        moment = base + timedelta(minutes=EPOCH_MINUTES * epoch + EPOCH_MINUTES / 2)
        states.append(any(start <= moment < end for start, end in intervals))
    return states


def sleep_regularity_index(
    nights: list[SleepNight],
    *,
    today: date,
    window_days: int = DEFAULT_SRI_WINDOW_DAYS,
    min_pairs: int = MIN_SRI_PAIRS,
) -> tuple[float | None, int]:
    """Sleep Regularity Index über das Fenster berechnen.

    Gibt ``(SRI, ausgewertete Tagespaare)`` zurück. Der SRI ist die
    prozentuale Wahrscheinlichkeit, zu einer beliebigen Tageszeit an zwei
    aufeinanderfolgenden Tagen im selben Zustand zu sein, skaliert auf -100…+100.

    Ein Kalendertag ist nur auswertbar, wenn sowohl die an diesem Morgen endende
    als auch die an diesem Abend beginnende Nacht bekannt sind — sonst wären die
    Epochen des Tages teils geraten.
    """
    start = today - timedelta(days=window_days)
    window = [n for n in nights if start <= n.date <= today and n.has_timing]
    intervals = _sleep_intervals(window)
    if not intervals:
        return None, 0

    known = {n.date for n in window}
    # Tag D ist vollständig, wenn die Nacht mit Aufwachen an D und die Nacht mit
    # Aufwachen an D+1 (also dem Einschlafen am Abend von D) beide vorliegen.
    complete = sorted(d for d in known if (d + timedelta(days=1)) in known)
    if len(complete) < 2:
        return None, 0

    cache: dict[date, list[bool]] = {}

    def states_for(day: date) -> list[bool]:
        if day not in cache:
            cache[day] = _day_states(day, intervals)
        return cache[day]

    matches = 0
    pairs = 0
    for day in complete:
        following = day + timedelta(days=1)
        if following not in complete:
            continue
        today_states = states_for(day)
        next_states = states_for(following)
        matches += sum(a == b for a, b in zip(today_states, next_states, strict=True))
        pairs += 1

    if pairs < min_pairs:
        return None, pairs

    fraction = matches / (pairs * EPOCHS_PER_DAY)
    return -100.0 + 200.0 * fraction, pairs


# -- Schlafmitte und Social Jetlag ------------------------------------------------


def _midpoint_minutes(night: SleepNight) -> float | None:
    midpoint = night.midpoint
    if midpoint is None:
        return None
    return midpoint.hour * 60.0 + midpoint.minute + midpoint.second / 60.0


def compute(
    nights: list[SleepNight],
    *,
    today: date,
    sri_window_days: int = DEFAULT_SRI_WINDOW_DAYS,
    midpoint_window_days: int = DEFAULT_MIDPOINT_WINDOW_DAYS,
    free_days: frozenset[int] = DEFAULT_FREE_DAYS,
) -> RegularityResult:
    """Alle Regularitätsmetriken berechnen."""
    sri, pairs = sleep_regularity_index(nights, today=today, window_days=sri_window_days)

    start = today - timedelta(days=midpoint_window_days)
    window = [n for n in nights if start <= n.date <= today and n.has_timing]

    midpoints = [m for m in (_midpoint_minutes(n) for n in window) if m is not None]
    mean_midpoint = (
        circular_mean_minutes(midpoints) if len(midpoints) >= MIN_MIDPOINT_SAMPLES else None
    )
    variability = (
        circular_std_minutes(midpoints) if len(midpoints) >= MIN_MIDPOINT_SAMPLES else None
    )

    # Social Jetlag: der Wochentag des *Aufwachens* bestimmt, ob die Nacht auf
    # einen freien Tag fiel — die Nacht von Freitag auf Samstag ist eine freie Nacht.
    free_samples = [
        m
        for n in window
        if n.date.weekday() in free_days
        if (m := _midpoint_minutes(n)) is not None
    ]
    work_samples = [
        m
        for n in window
        if n.date.weekday() not in free_days
        if (m := _midpoint_minutes(n)) is not None
    ]
    social_jetlag = None
    if (
        len(free_samples) >= MIN_SOCIAL_JETLAG_SAMPLES
        and len(work_samples) >= MIN_SOCIAL_JETLAG_SAMPLES
    ):
        free_mean = circular_mean_minutes(free_samples)
        work_mean = circular_mean_minutes(work_samples)
        if free_mean is not None and work_mean is not None:
            social_jetlag = circular_difference_minutes(free_mean, work_mean) / 60.0

    return RegularityResult(
        sri=sri,
        midpoint_minutes=mean_midpoint,
        midpoint_variability_min=variability,
        social_jetlag_hours=social_jetlag,
        sri_pairs=pairs,
    )
