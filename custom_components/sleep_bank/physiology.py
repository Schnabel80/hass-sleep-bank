"""Physiologische Erholung — robuste Baselines und z-Scores.

Reines Python, keine HA-Abhängigkeiten.

Ruhepuls, HRV und Atemfrequenz sind nur **relativ zur eigenen Baseline**
aussagekräftig. Absolute Schwellen sind zwischen Personen nicht vergleichbar:
eine HRV von 24 ms kann für die eine Person normal und für die andere ein
deutliches Warnsignal sein. Deshalb durchgängig z-Scores gegen ein rollierendes
persönliches Fenster.

Die Baseline nutzt **Median und MAD** statt Mittelwert und Standardabweichung.
Bei Stichproben von wenigen Wochen mit gelegentlichen Ausreißern (Infekt,
Alkohol, Messfehler) würden klassische Momente die Baseline mitverschieben und
die Abweichung, die man eigentlich sehen will, gerade wegmitteln.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import SleepNight

#: Länge des Baseline-Fensters in Nächten.
DEFAULT_BASELINE_DAYS = 28

#: Mindestzahl Messwerte im Fenster, bevor ein z-Score ausgegeben wird.
MIN_BASELINE_SAMPLES = 14

#: Umrechnung MAD → Standardabweichungs-Äquivalent bei Normalverteilung.
MAD_TO_SIGMA = 1.4826

#: Untergrenze der Streuung, damit eine sehr gleichförmige Baseline keine
#: absurden z-Scores erzeugt (Division durch beinahe null).
MIN_SCALE = {"resting_hr": 1.0, "hrv_ms": 2.0, "respiratory_rate": 0.3}

#: z-Scores werden hierauf begrenzt — jenseits davon ist die Aussage ohnehin
#: nur noch „deutlich auffällig", und Ausreißer sollen den Gesamtwert nicht sprengen.
Z_CLAMP = 4.0

#: Vorzeichen: +1 bedeutet „höher ist besser für die Erholung".
METRIC_DIRECTION = {"resting_hr": -1.0, "hrv_ms": +1.0, "respiratory_rate": -1.0}


@dataclass(frozen=True, slots=True)
class PhysiologyResult:
    """Ergebnis der physiologischen Auswertung. ``None`` heißt: zu wenig Daten."""

    resting_hr_z: float | None
    hrv_z: float | None
    respiratory_rate_z: float | None
    recovery_z: float | None
    """Zusammengefasster Erholungs-z-Score; positiv = besser als die eigene Baseline."""

    baseline_samples: int
    """Kleinste Stichprobengröße unter den genutzten Messreihen."""


def robust_baseline(values: list[float]) -> tuple[float, float] | None:
    """Median und robuste Streuung (aus dem MAD) einer Messreihe."""
    if len(values) < 2:
        return None
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])
    return median, mad * MAD_TO_SIGMA


def _metric_values(nights: list[SleepNight], attribute: str) -> list[float]:
    return [v for n in nights if (v := getattr(n, attribute)) is not None]


def _z_score(
    current: float | None,
    baseline_values: list[float],
    attribute: str,
    min_samples: int,
) -> float | None:
    """Richtungsbereinigter z-Score: positiv = besser erholt als üblich."""
    if current is None or len(baseline_values) < min_samples:
        return None
    baseline = robust_baseline(baseline_values)
    if baseline is None:
        return None
    median, scale = baseline
    scale = max(scale, MIN_SCALE[attribute])
    raw = (current - median) / scale
    directed = raw * METRIC_DIRECTION[attribute]
    return max(-Z_CLAMP, min(Z_CLAMP, directed))


def compute(
    nights: list[SleepNight],
    *,
    today: date,
    baseline_days: int = DEFAULT_BASELINE_DAYS,
    min_samples: int = MIN_BASELINE_SAMPLES,
) -> PhysiologyResult:
    """Physiologische z-Scores der jüngsten Nacht gegen die eigene Baseline."""
    latest = max(nights, key=lambda n: n.date, default=None)
    if latest is None:
        return PhysiologyResult(None, None, None, None, 0)

    # Die zu bewertende Nacht gehört nicht in ihre eigene Baseline.
    start = latest.date - timedelta(days=baseline_days)
    history = [n for n in nights if start <= n.date < latest.date]

    scores: dict[str, float | None] = {}
    sample_counts: list[int] = []
    for attribute in ("resting_hr", "hrv_ms", "respiratory_rate"):
        values = _metric_values(history, attribute)
        sample_counts.append(len(values))
        scores[attribute] = _z_score(getattr(latest, attribute), values, attribute, min_samples)

    available = [v for v in scores.values() if v is not None]
    recovery = sum(available) / len(available) if available else None

    return PhysiologyResult(
        resting_hr_z=scores["resting_hr"],
        hrv_z=scores["hrv_ms"],
        respiratory_rate_z=scores["respiratory_rate"],
        recovery_z=recovery,
        baseline_samples=min(sample_counts) if sample_counts else 0,
    )
