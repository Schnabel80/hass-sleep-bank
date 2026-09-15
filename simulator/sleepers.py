"""Synthetische Schläfertypen für die Modellvalidierung.

Das Schlafkonto lässt sich nicht an echten Daten kalibrieren, solange man noch
keine hat. Diese Generatoren erzeugen Nachtserien mit bekannten Eigenschaften,
sodass sich prüfen lässt, ob die Kennzahlen das Erwartete anzeigen.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from sleep_ledger.models import Provenance, SleepNight

if TYPE_CHECKING:
    from collections.abc import Callable


def _night(
    day: date, onset_hour: float, duration_min: float, awake_min: float = 8.0
) -> SleepNight:
    onset = datetime.combine(day - timedelta(days=1), time(0)) + timedelta(hours=onset_hour)
    return SleepNight(
        date=day,
        total_min=round(duration_min, 1),
        awake_min=round(awake_min, 1),
        onset_time=onset,
        wake_time=onset + timedelta(minutes=duration_min + awake_min),
        resting_hr=60.0 + max(0.0, (450.0 - duration_min)) / 25.0 + random.gauss(0, 1.0),
        hrv_ms=45.0 - max(0.0, (450.0 - duration_min)) / 15.0 + random.gauss(0, 3.0),
        respiratory_rate=14.0 + random.gauss(0, 0.4),
        anchor_provenance=Provenance.FOCUS,
        confidence=0.85,
    )


def regelmaessig(day: date, index: int) -> SleepNight:
    """Acht Stunden, jeden Tag zur selben Zeit."""
    return _night(day, 23.0 + random.gauss(0, 0.2), 480 + random.gauss(0, 20))


def wochenend_nachholer(day: date, index: int) -> SleepNight:
    """Werktags zu wenig, am Wochenende deutlich später und länger."""
    if day.weekday() >= 5:
        return _night(day, 25.0 + random.gauss(0, 0.4), 570 + random.gauss(0, 30))
    return _night(day, 23.5 + random.gauss(0, 0.3), 390 + random.gauss(0, 25))


def schichtarbeiter(day: date, index: int) -> SleepNight:
    """Wechselschicht: drei Nächte früh, drei Nächte spät."""
    late = (index // 3) % 2 == 1
    onset = 2.0 if late else 22.5
    return _night(day, onset + random.gauss(0, 0.5), 420 + random.gauss(0, 40))


def harte_woche(day: date, index: int) -> SleepNight:
    """Van-Dongen-Protokoll: normal, dann zwei Wochen Restriktion, dann Erholung."""
    if index < 14:
        duration = 480
    elif index < 28:
        duration = 360
    else:
        duration = 540
    return _night(day, 23.0 + random.gauss(0, 0.3), duration + random.gauss(0, 15))


SLEEPERS: dict[str, Callable[[date, int], SleepNight]] = {
    "regelmäßig": regelmaessig,
    "Wochenend-Nachholer": wochenend_nachholer,
    "Schichtarbeiter": schichtarbeiter,
    "harte Wochen": harte_woche,
}


def generate(name: str, *, days: int, start: date, seed: int = 42) -> list[SleepNight]:
    """Nachtserie eines Schläfertyps erzeugen."""
    random.seed(seed)
    maker = SLEEPERS[name]
    return [maker(start + timedelta(days=index), index) for index in range(days)]
