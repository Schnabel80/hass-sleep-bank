"""Aufwach-Anker und Rückrechnung der Einschlafzeit.

Reines Python, keine HA-Abhängigkeiten.

Leitprinzip
-----------
**Aufwachen ist messbar, Einschlafen nicht.** Wer im Bett liest, erzeugt keine
Schritte und hat den Schlafmodus längst aktiv — jede aktivitätsbasierte
Einschlaferkennung setzt den Schlafbeginn deshalb *systematisch zu früh* an. Die
Uhr weiß dagegen, wann tatsächlich Schlaf einsetzte, und meldet das als Dauer.

Also: genau einen robusten Anker am Morgen bestimmen und den Rest rückrechnen.

    onset = wake - (total_min + awake_min)

Die vier Ankerkandidaten stützen sich gegenseitig. Keiner von ihnen ist allein
verlässlich; die im Feld beobachteten Ausfälle sind in den Konstanten und Tests
dieses Moduls direkt abgebildet (siehe ``tests/test_timing.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from datetime import tzinfo as TzInfo
from enum import StrEnum

from .models import Provenance

# -- Fenster ---------------------------------------------------------------------

#: Morgenfenster, in dem ein Aufwach-Anker liegen darf.
MORNING_START_HOUR = 3
MORNING_END_HOUR = 12

#: Plausibler Bereich für die zurückgerechnete Einschlafzeit.
ONSET_EARLIEST_HOUR = 19
ONSET_LATEST_HOUR = 4

# -- Fokus-Sensor ----------------------------------------------------------------

#: Mindestdauer einer Fokusphase, damit sie als Schlaf gelten kann.
#: Fängt den Tages-Fokus ab (beobachtet: 08.09.2026, 13:16–14:29 Uhr).
FOCUS_MIN_DURATION = timedelta(hours=3)

#: Höchstdauer einer Fokusphase. Fängt verlorene Übergänge ab
#: (beobachtet: 08.09.2026 21:50 bis 10.09.2026 05:46 — 32 Stunden am Stück).
FOCUS_MAX_DURATION = timedelta(hours=16)

#: Zustände, die keine echte Zustandsänderung darstellen. Ein Neustart der
#: Companion-App erzeugt kurzzeitig `unavailable` und danach denselben Wert wie
#: zuvor (beobachtet: 13.09.2026, 07:31:37). Solche Blips dürfen niemals als
#: Übergang gewertet werden.
NON_STATES = frozenset({"unavailable", "unknown", "none", ""})

# -- Schrittzähler ---------------------------------------------------------------

#: Schritte, die innerhalb von :data:`RISE_WINDOW` anfallen müssen, damit die
#: Aktivität als Aufstehen zählt.
RISE_MIN_STEPS = 120.0
RISE_WINDOW = timedelta(minutes=30)

#: Mindestruhe, die eine Aktivitätsphase von der nächsten trennt.
QUIET_GAP = timedelta(minutes=20)

#: Schrittzahl, ab der eine kurze nächtliche Aktivität als Unterbrechung zählt
#: (Toilettengang). Darunter ist es Messrauschen.
INTERRUPTION_MIN_STEPS = 10.0

# -- Anker -----------------------------------------------------------------------

#: Grundkonfidenz je Kandidat.
BASE_CONFIDENCE = {
    Provenance.EXPLICIT: 1.0,
    Provenance.FOCUS: 0.8,
    Provenance.STEPS: 0.7,
    Provenance.DATA_ARRIVAL: 0.5,
}

#: Abstand, innerhalb dessen zwei Kandidaten einander stützen.
CONSENSUS_TOLERANCE = timedelta(minutes=45)

#: Konfidenzzuschlag je zusätzlich stützendem Kandidaten und Obergrenze.
CONSENSUS_BONUS = 0.1
MAX_CONFIDENCE = 0.95

#: Versatz zwischen „aufgewacht" und dem jeweiligen Signal. Fokus-Aus und eine
#: explizite Aufwachzeit markieren das Aufwachen selbst; Schritte und die Ankunft
#: der Nachtdaten liegen systematisch danach.
ANCHOR_LAG = {
    Provenance.EXPLICIT: timedelta(0),
    Provenance.FOCUS: timedelta(0),
    Provenance.STEPS: timedelta(minutes=10),
    Provenance.DATA_ARRIVAL: timedelta(minutes=10),
}

#: Toleranz, innerhalb derer die zurückgerechnete Einschlafzeit zum Fokus-Beginn
#: passen muss, sofern für dieselbe Nacht einer vorliegt.
ONSET_FOCUS_TOLERANCE = timedelta(minutes=90)

#: Abstand, bis zu dem eine Fokus-Phase überhaupt noch zur selben Nacht gehören
#: kann. Ohne diese Grenze würde eine Nacht *ohne* Fokus-Daten gegen die Phase
#: eines anderen Tages geprüft — fehlende Evidenz würde damit zu Gegenevidenz.
ONSET_FOCUS_RELEVANCE = timedelta(hours=12)

#: Konfidenzabschlag, wenn die Plausibilisierung der Einschlafzeit scheitert.
IMPLAUSIBLE_ONSET_PENALTY = 0.4


class Rejection(StrEnum):
    """Gründe, aus denen ein Kandidat verworfen wurde — für die Diagnose."""

    OUTSIDE_WINDOW = "outside_morning_window"
    FOCUS_TOO_SHORT = "focus_phase_too_short"
    FOCUS_TOO_LONG = "focus_phase_too_long"
    AFTER_DATA_ARRIVAL = "after_data_arrival"
    NO_SUSTAINED_ACTIVITY = "no_sustained_activity"


@dataclass(frozen=True, slots=True)
class Candidate:
    """Ein normalisierter Aufwach-Kandidat (Versatz bereits abgezogen)."""

    when: datetime
    provenance: Provenance
    confidence: float


@dataclass(frozen=True, slots=True)
class AnchorResult:
    """Ergebnis der Ankerbestimmung."""

    wake_time: datetime | None
    provenance: Provenance
    confidence: float
    candidates: list[Candidate] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    interruptions: int = 0


# -- Hilfsfunktionen --------------------------------------------------------------


def morning_window(day: date, tzinfo: TzInfo | None = None) -> tuple[datetime, datetime]:
    """Morgenfenster eines Kalendertags, in dem ein Anker liegen darf."""
    start = datetime.combine(day, time(MORNING_START_HOUR), tzinfo=tzinfo)
    end = datetime.combine(day, time(MORNING_END_HOUR), tzinfo=tzinfo)
    return start, end


def clean_state_history(history: list[tuple[datetime, str]]) -> list[tuple[datetime, str]]:
    """`unavailable`-Blips entfernen und gleiche Folgezustände zusammenfassen.

    Beides ist nötig, damit App-Neustarts keine Scheinübergänge erzeugen.
    """
    result: list[tuple[datetime, str]] = []
    for when, raw in sorted(history, key=lambda item: item[0]):
        value = str(raw).strip().lower()
        if value in NON_STATES:
            continue
        if result and result[-1][1] == value:
            continue
        result.append((when, value))
    return result


def focus_phases(history: list[tuple[datetime, str]]) -> list[tuple[datetime, datetime]]:
    """Zusammenhängende `on`-Phasen des Fokus-Sensors als (Beginn, Ende)."""
    cleaned = clean_state_history(history)
    phases: list[tuple[datetime, datetime]] = []
    started: datetime | None = None
    for when, value in cleaned:
        if value == "on" and started is None:
            started = when
        elif value == "off" and started is not None:
            phases.append((started, when))
            started = None
    return phases


def step_deltas(history: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """Kumulativen Schrittzähler in Zuwächse umrechnen.

    Der Sensor ist `total_increasing` und springt um Mitternacht auf null zurück.
    Ein Rückgang ist daher ein Reset, kein negativer Zuwachs — sonst würde jede
    Nacht einen großen künstlichen Ausschlag erzeugen.
    """
    deltas: list[tuple[datetime, float]] = []
    previous: float | None = None
    for when, value in sorted(history, key=lambda item: item[0]):
        if value is None:
            continue
        if previous is None or value < previous:
            increment = value if previous is not None else 0.0
        else:
            increment = value - previous
        previous = value
        if increment > 0:
            deltas.append((when, increment))
    return deltas


def _activity_bursts(
    deltas: list[tuple[datetime, float]],
    quiet_gap: timedelta = QUIET_GAP,
) -> list[tuple[datetime, datetime, float]]:
    """Aktivitätsphasen als (Beginn, Ende, Schritte), getrennt durch Ruhepausen."""
    bursts: list[tuple[datetime, datetime, float]] = []
    start: datetime | None = None
    end: datetime | None = None
    total = 0.0
    for when, increment in deltas:
        if start is None:
            start, end, total = when, when, increment
            continue
        assert end is not None
        if when - end > quiet_gap:
            bursts.append((start, end, total))
            start, end, total = when, when, increment
        else:
            end, total = when, total + increment
    if start is not None and end is not None:
        bursts.append((start, end, total))
    return bursts


# -- Kandidaten -------------------------------------------------------------------


def focus_candidate(
    phases: list[tuple[datetime, datetime]],
    window: tuple[datetime, datetime],
    rejected: dict[str, str],
) -> datetime | None:
    """Aufwach-Kandidat aus dem Fokus-Sensor."""
    window_start, window_end = window
    for start, end in phases:
        if not (window_start <= end <= window_end):
            continue
        duration = end - start
        if duration < FOCUS_MIN_DURATION:
            rejected[str(Provenance.FOCUS)] = str(Rejection.FOCUS_TOO_SHORT)
            continue
        if duration > FOCUS_MAX_DURATION:
            rejected[str(Provenance.FOCUS)] = str(Rejection.FOCUS_TOO_LONG)
            continue
        return end
    rejected.setdefault(str(Provenance.FOCUS), str(Rejection.OUTSIDE_WINDOW))
    return None


def steps_candidate(
    deltas: list[tuple[datetime, float]],
    window: tuple[datetime, datetime],
    rejected: dict[str, str],
) -> tuple[datetime | None, int]:
    """Aufwach-Kandidat aus anhaltender Schrittaktivität, plus Unterbrechungszahl.

    Kurze Ausbrüche vor dem Aufstehen — der nächtliche Toilettengang — beenden den
    Schlaf **nicht**. Sie werden gezählt und fließen als Fragmentierung ein.
    """
    window_start, window_end = window
    night_start = window_start - timedelta(hours=MORNING_START_HOUR + 4)
    relevant = [(w, d) for w, d in deltas if night_start <= w <= window_end]
    bursts = _activity_bursts(relevant)

    interruptions = 0
    for start, end, total in bursts:
        sustained = total >= RISE_MIN_STEPS and (end - start) <= RISE_WINDOW * 4
        if sustained and window_start <= start <= window_end:
            return start, interruptions
        if total >= INTERRUPTION_MIN_STEPS and start < window_end:
            interruptions += 1

    rejected[str(Provenance.STEPS)] = str(Rejection.NO_SUSTAINED_ACTIVITY)
    return None, interruptions


def determine_anchor(
    *,
    day: date,
    explicit_wake: datetime | None = None,
    focus_history: list[tuple[datetime, str]] | None = None,
    steps_history: list[tuple[datetime, float]] | None = None,
    data_arrival: datetime | None = None,
    tzinfo: TzInfo | None = None,
) -> AnchorResult:
    """Aufwachzeit aus allen verfügbaren Signalen bestimmen."""
    window = morning_window(day, tzinfo)
    window_start, window_end = window
    rejected: dict[str, str] = {}

    phases = focus_phases(focus_history or [])
    deltas = step_deltas(steps_history or [])

    raw: list[tuple[datetime, Provenance]] = []
    if explicit_wake is not None:
        raw.append((explicit_wake, Provenance.EXPLICIT))
    focus_time = focus_candidate(phases, window, rejected)
    if focus_time is not None:
        raw.append((focus_time, Provenance.FOCUS))
    steps_time, interruptions = steps_candidate(deltas, window, rejected)
    if steps_time is not None:
        raw.append((steps_time, Provenance.STEPS))
    if data_arrival is not None and window_start <= data_arrival <= window_end:
        raw.append((data_arrival, Provenance.DATA_ARRIVAL))

    # Signalversatz abziehen, damit alle Kandidaten dasselbe meinen: „aufgewacht".
    candidates = [
        Candidate(when=when - ANCHOR_LAG[prov], provenance=prov, confidence=BASE_CONFIDENCE[prov])
        for when, prov in raw
    ]

    # Die Ankunft der Nachtdaten ist eine Obergrenze: Die Uhr kann die Session
    # nicht melden, bevor sie beendet ist. Spätere Kandidaten sind unplausibel.
    if data_arrival is not None:
        limit = data_arrival + CONSENSUS_TOLERANCE
        kept = []
        for candidate in candidates:
            if candidate.when > limit and candidate.provenance is not Provenance.EXPLICIT:
                rejected[str(candidate.provenance)] = str(Rejection.AFTER_DATA_ARRIVAL)
            else:
                kept.append(candidate)
        candidates = kept

    if not candidates:
        return AnchorResult(
            wake_time=None,
            provenance=Provenance.NONE,
            confidence=0.0,
            candidates=[],
            rejected=rejected,
            interruptions=interruptions,
        )

    # Konsens: Cluster um den stärksten Kandidaten; Stützung erhöht die Konfidenz.
    leader = max(candidates, key=lambda c: c.confidence)
    cluster = [c for c in candidates if abs(c.when - leader.when) <= CONSENSUS_TOLERANCE]
    weight = sum(c.confidence for c in cluster)
    offsets = sum((c.when - leader.when).total_seconds() * c.confidence for c in cluster)
    wake_time = leader.when + timedelta(seconds=offsets / weight)
    confidence = min(MAX_CONFIDENCE, leader.confidence + CONSENSUS_BONUS * (len(cluster) - 1))

    return AnchorResult(
        wake_time=wake_time,
        provenance=leader.provenance,
        confidence=confidence,
        candidates=sorted(candidates, key=lambda c: c.when),
        rejected=rejected,
        interruptions=interruptions,
    )


# -- Rückrechnung -----------------------------------------------------------------


def onset_is_plausible(
    onset: datetime,
    focus_phases_found: list[tuple[datetime, datetime]] | None = None,
) -> bool:
    """Ob eine zurückgerechnete Einschlafzeit glaubwürdig ist.

    Zwei Prüfungen. Die Uhrzeit muss im nächtlichen Fenster liegen — das gilt
    immer. Zusätzlich muss die Zeit zum Fokus-Beginn passen, **aber nur, wenn für
    dieselbe Nacht überhaupt eine Fokus-Phase vorliegt**: Eine Nacht, in der der
    Fokus-Sensor nichts geliefert hat, ist nicht unplausibel, sondern schlicht
    unbelegt. Sie wird über die Konfidenz des Ankers abgewertet, nicht hier.
    """
    hour = onset.hour
    if not (hour >= ONSET_EARLIEST_HOUR or hour <= ONSET_LATEST_HOUR):
        return False

    relevant = [
        start
        for start, _ in (focus_phases_found or [])
        if abs(onset - start) <= ONSET_FOCUS_RELEVANCE
    ]
    if not relevant:
        return True
    return min(abs(onset - start) for start in relevant) <= ONSET_FOCUS_TOLERANCE


def derive_onset(
    wake_time: datetime | None,
    total_min: float | None,
    awake_min: float | None,
) -> datetime | None:
    """Einschlafzeit aus Anker und gemessener Schlafspanne zurückrechnen."""
    if wake_time is None or total_min is None or total_min <= 0:
        return None
    return wake_time - timedelta(minutes=total_min + (awake_min or 0.0))
