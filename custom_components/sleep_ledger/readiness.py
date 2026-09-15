"""Gesamt-Bereitschaftsindex — eine bewusst gekennzeichnete Heuristik.

Reines Python, keine HA-Abhängigkeiten.

**Warnhinweis, der überall mitgeführt werden muss:** Dies ist der einzige Wert
der Integration, für den es keine belastbare Evidenz gibt. Schlafkonto,
Regularität und die physiologischen z-Scores stehen jeweils auf publizierten
Befunden. Die *Gewichtung* dieser drei zu einer Zahl ist dagegen frei gewählt —
es existiert keine Studie, die sagt, dass Schlafschuld 45 % und HRV 35 % der
Leistungsfähigkeit erklärt.

Der Wert ist trotzdem nützlich als Dashboard- und Automationsgröße, solange klar
ist, was er ist. Deshalb: konfigurierbare Gewichte und ein ``is_heuristic``-Flag
bis in die Entitätsattribute.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Standardgewichte der drei Komponenten.
DEFAULT_WEIGHTS = {"debt": 0.45, "physiology": 0.35, "regularity": 0.20}

#: Akute Schuld in Minuten, ab der die Schuldkomponente auf 0 Punkte fällt.
#: Eine volle Nacht Rückstand ist die natürliche Skala dafür.
DEBT_ZERO_POINT_MIN = 480.0

#: Erholungs-z-Score, der 0 bzw. 100 Punkte ergibt.
PHYSIOLOGY_Z_RANGE = 2.0

#: SRI-Bereich für die Punkteskala. Bewusst **nicht** der theoretische Bereich
#: -100…+100: reale Menschen liegen fast immer zwischen 40 und 90, eine lineare
#: Abbildung des Vollbereichs würde alle Nutzer bei ~70 Punkten zusammendrücken.
SRI_SCORE_FLOOR = 40.0
SRI_SCORE_CEILING = 90.0


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    """Ergebnis der Bereitschaftsberechnung."""

    score: float | None
    """0–100, oder ``None``, wenn keine einzige Komponente verfügbar ist."""

    components: dict[str, float] = field(default_factory=dict)
    """Punktwert je verfügbarer Komponente (0–100)."""

    weights: dict[str, float] = field(default_factory=dict)
    """Tatsächlich verwendete, auf die Summe 1 renormierte Gewichte."""

    is_heuristic: bool = True
    """Immer wahr — der Wert ist keine validierte Messgröße."""


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def debt_score(acute_min: float | None) -> float | None:
    """Schuldkomponente: volle Punktzahl bei ausgeglichenem oder positivem Konto."""
    if acute_min is None:
        return None
    return _clamp_score(100.0 * (1.0 - acute_min / DEBT_ZERO_POINT_MIN))


def physiology_score(recovery_z: float | None) -> float | None:
    """Physiologiekomponente: 50 Punkte entsprechen der eigenen Baseline."""
    if recovery_z is None:
        return None
    return _clamp_score(50.0 + 50.0 * recovery_z / PHYSIOLOGY_Z_RANGE)


def regularity_score(sri: float | None) -> float | None:
    """Regularitätskomponente aus dem SRI, skaliert auf den realen Wertebereich."""
    if sri is None:
        return None
    span = SRI_SCORE_CEILING - SRI_SCORE_FLOOR
    return _clamp_score(100.0 * (sri - SRI_SCORE_FLOOR) / span)


def compute(
    *,
    acute_debt_min: float | None,
    recovery_z: float | None,
    sri: float | None,
    weights: dict[str, float] | None = None,
) -> ReadinessResult:
    """Bereitschaftsindex aus den drei Komponenten bilden.

    Fehlende Komponenten werden übersprungen und die verbleibenden Gewichte
    renormiert — eine fehlende Regularität soll den Wert nicht nach unten ziehen,
    sondern einfach nicht mitzählen.
    """
    active = dict(DEFAULT_WEIGHTS if weights is None else weights)

    components: dict[str, float] = {
        name: value
        for name, value in (
            ("debt", debt_score(acute_debt_min)),
            ("physiology", physiology_score(recovery_z)),
            ("regularity", regularity_score(sri)),
        )
        if value is not None
    }

    total_weight = sum(active.get(name, 0.0) for name in components)
    if not components or total_weight <= 0.0:
        return ReadinessResult(score=None)

    used = {name: active.get(name, 0.0) / total_weight for name in components}
    score = sum(components[name] * used[name] for name in components)
    return ReadinessResult(score=score, components=components, weights=used)
