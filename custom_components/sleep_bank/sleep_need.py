"""Schätzung des individuellen Schlafbedarfs.

Reines Python, keine HA-Abhängigkeiten.

Warum das ein eigenes Modul verdient
------------------------------------
Das chronische Defizit folgt dem angenommenen Bedarf **exakt 1:1** — das steckt
in der Definition ``Defizit = Bedarf − Schlaf``. Eine Fehlannahme von 30 Minuten
erzeugt also 30 Minuten Phantomdefizit pro Nacht. Der Bedarf ist damit die
einflussreichste Zahl des Systems, einflussreicher als jede Messung.

Gleichzeitig ist er individuell weit gestreut. Kitamura et al. haben 15 gesunde
junge Männer neun Tage lang zwölf Stunden ins Bett gelegt und aus dem
Asymptotenwert die optimale Dauer bestimmt: im Mittel **8,41 h**, individuell
zwischen **7,29 und 9,26 h**. Der gewohnte Schlaf zu Hause lag im Mittel bei
7,37 h — der Gewohnheitswert **unterschätzt den Bedarf also um rund eine Stunde**.

Das Verfahren hier
------------------
Die Methode von Kitamura ist zu Hause nachbildbar: An die Nächte eines Laufs
ungestörten Schlafs wird eine abklingende Exponentialfunktion gelegt und deren
Asymptote als Bedarf genommen.

    Dauer(d) = Bedarf + Rebound · exp(−d / τ)

``d`` ist die Position der Nacht innerhalb des Laufs. Die erste Nacht ist
rebound-dominiert und misst vor allem die *Schuld*, nicht den Bedarf — bei
Kitamura schliefen die Probanden in der ersten Nacht mit erweiterter Gelegenheit
10,59 h, ganze 3,22 h über ihrem Gewohnheitswert. Stabil wurde der Wert erst ab
der vierten Nacht. Genau diesen Verlauf bildet der Fit ab, statt ihn zu ignorieren.

Was „frei" heißt — und warum der Wochentag dafür nicht reicht
--------------------------------------------------------------
Entscheidend ist nicht der Kalender, sondern ob der Schlaf **von selbst endete**.
Wer auch samstags einen Wecker stellt, hat keine freie Nacht, egal was der
Wochentag sagt. Genau hier lauert ein stiller Fehler: Weckergekappte Nächte
messen die *Weckzeit*, nicht den Bedarf, und würden ihn systematisch zu niedrig
schätzen — mit einem zu kleinen Defizit als Folge. Eine Unterschätzung des
Bedarfs ist gefährlicher als gar keine Schätzung, weil sie beruhigt.

Deshalb wird die Weckerbindung aktiv erkannt: Extern auferlegte Weckzeiten
streuen auffällig wenig. Liegt die Streuung der Aufwachzeiten an den vermeintlich
freien Tagen genauso eng wie an Arbeitstagen und um dieselbe Uhrzeit, gelten sie
als weckergebunden und werden **nicht** für die Schätzung verwendet. Stattdessen
meldet die Integration offen, dass sie den Bedarf so nicht bestimmen kann.

Umgekehrt ist eine Nacht auch ohne freien Tag verwertbar, wenn sie **vor** dem
gewohnten Weckzeitpunkt endete — dann hat der Schlaf von selbst aufgehört.

Güteklassen, absteigend
-----------------------
1. **Kalibrierlauf** — ein vom Nutzer markiertes Fenster ohne Wecker. Die direkte
   Heimnachbildung des publizierten Protokolls und für durchgehend
   weckergebundene Menschen der einzige belastbare Weg.
2. **Gepoolte ungestörte Läufe** — zusammenhängende Nächte ohne Wecker.
3. **Letzte Nacht je Lauf** — wenn zu wenige lange Läufe für einen Fit vorliegen.
   Unterschätzt den Bedarf eher, weil auch die zweite freie Nacht noch nicht
   gesättigt ist; das ist die ehrlichere Richtung als eine Überschätzung.
4. **Keine Schätzung, mit Begründung** — der eingestellte Wert bleibt stehen, und
   der Grund steht am Sensor.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from .regularity import circular_difference_minutes, circular_mean_minutes, circular_std_minutes

if TYPE_CHECKING:
    from datetime import date

    from .models import SleepNight

#: Zeitkonstante des Rebound-Abklingens in Nächten.
#:
#: Fest vorgegeben statt mitgefittet: Die meisten freien Läufe im Alltag sind
#: Wochenenden mit nur zwei Nächten, aus denen sich drei Parameter nicht
#: bestimmen lassen. Der Wert ist so gewählt, dass der Rebound bis zur vierten
#: Nacht auf unter 10 % abgeklungen ist — der Zeitpunkt, an dem im publizierten
#: Protokoll das Plateau erreicht war.
REBOUND_TAU_NIGHTS = 1.2

#: Physiologisch plausibler Bereich einer Bedarfsschätzung (5–11 h).
#: Weit genug für genetische Kurzschläfer, eng genug, um Rechenausreißer zu fangen.
MIN_PLAUSIBLE_NEED_MIN = 300.0
MAX_PLAUSIBLE_NEED_MIN = 660.0

#: Anforderungen an einen Fit über gepoolte freie Läufe.
MIN_POOLED_NIGHTS = 8
MIN_POOLED_RUNS = 4
MIN_DISTINCT_POSITIONS = 3
"""Mindestens die Positionen 0, 1 und 2 müssen vorkommen — sonst wäre die
Asymptote eine Extrapolation aus zwei Gruppenmitteln und entsprechend labil."""

#: Anforderungen an einen Kalibrierlauf.
MIN_CALIBRATION_NIGHTS = 4
PLATEAU_NIGHTS = 4
"""Ab so vielen Nächten gilt das Plateau als erreicht (Kitamura: stabil ab E4)."""

#: Anforderungen an den robusten Rückfallweg.
MIN_FALLBACK_RUNS = 4

#: Anteil der Arbeitstage, deren Aufwachzeit eng um denselben Zeitpunkt liegen
#: muss, damit von einem Weckermuster ausgegangen wird.
#:
#: Bewusst ein Dichtetest und **kein** Streuungstest: Die Standardabweichung
#: reagiert so empfindlich auf Ausreißer, dass schon eine einzelne Urlaubswoche
#: unter rund hundert Arbeitstagen das Weckermuster verdeckt hätte — mit dem
#: Ergebnis, dass weckergekappte Nächte für den Bedarf gehalten werden.
ALARM_CONCENTRATION_MIN = 0.7

#: Abstand der mittleren Aufwachzeiten, bis zu dem freie und Arbeitstage als
#: „gleich früh" gelten — dann klingelt es offenbar auch an den freien Tagen.
ALARM_ALIGNMENT_MAX_MIN = 30.0

#: Obergrenze des Toleranzfensters um den Weckzeitpunkt.
MAX_ALARM_TOLERANCE_MIN = 45.0

#: Mindestzahl Nächte mit Aufwachzeit, bevor ein Weckmuster bestimmt wird.
MIN_WAKE_SAMPLES = 7


class NeedReason(StrEnum):
    """Warum keine Schätzung möglich war — gehört an den Sensor, nicht ins Log."""

    OK = "ok"
    NO_TIMING = "no_timing_data"
    """Keine Aufwachzeiten vorhanden — ohne sie ist keine Weckerprüfung möglich."""

    ALARM_CONSTRAINED = "all_nights_alarm_constrained"
    """Jede Nacht endete am Wecker. Nur ein Kalibrierlauf hilft hier weiter."""

    TOO_FEW_SAMPLES = "too_few_unconstrained_nights"
    CALIBRATION_TOO_SHORT = "calibration_run_too_short"


class NeedMethod(StrEnum):
    """Wie eine Bedarfsschätzung zustande kam — absteigende Güte."""

    CALIBRATION = "calibration"
    POOLED_RUNS = "pooled_runs"
    LAST_NIGHT_OF_RUN = "last_night_of_run"
    NONE = "none"


#: Konfidenz je Verfahren, vor Abzügen.
BASE_CONFIDENCE = {
    NeedMethod.CALIBRATION: 0.9,
    NeedMethod.POOLED_RUNS: 0.65,
    NeedMethod.LAST_NIGHT_OF_RUN: 0.4,
    NeedMethod.NONE: 0.0,
}


@dataclass(frozen=True, slots=True)
class NeedEstimate:
    """Ergebnis einer Bedarfsschätzung."""

    minutes: float | None
    method: NeedMethod
    confidence: float
    nights_used: int
    runs_used: int
    uncertainty_min: float | None
    """Grobe Streuung der Schätzung — bewusst kein Konfidenzintervall."""

    plateau_reached: bool = False
    """Nur für Kalibrierläufe: war der Lauf lang genug, um das Plateau zu sehen?"""

    clamped: bool = False
    """Ob das Rohergebnis auf den plausiblen Bereich begrenzt werden musste."""

    reason: NeedReason = NeedReason.OK
    """Bei fehlender Schätzung: warum. Gehört sichtbar an den Sensor."""

    @property
    def is_usable(self) -> bool:
        """Ob die Schätzung übernommen werden darf."""
        return self.minutes is not None and self.method is not NeedMethod.NONE


# -- Weckererkennung --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WakeProfile:
    """Wie die Aufwachzeiten verteilt sind — Grundlage der Weckererkennung."""

    workday_center: float | None
    workday_spread: float | None
    free_center: float | None
    free_spread: float | None

    workday_concentration: float | None = None
    """Anteil der Arbeitstage, deren Aufwachzeit nahe am Zentrum liegt."""

    @property
    def has_alarm_pattern(self) -> bool:
        """Ob die Arbeitstage ein Weckermuster zeigen.

        Ohne eng gebündelte Aufwachzeiten gibt es keinen Wecker, gegen den sich
        eine einzelne Nacht abgrenzen ließe — dann greift die Prüfung nicht.
        """
        return (
            self.workday_center is not None
            and self.workday_concentration is not None
            and self.workday_concentration >= ALARM_CONCENTRATION_MIN
        )

    @property
    def alarm_tolerance(self) -> float:
        """Abstand zum Weckzeitpunkt, innerhalb dessen eine Nacht als gekappt gilt.

        Nach oben gedeckelt: Eine durch Ausreißer aufgeblähte Streuung darf das
        Fenster nicht so weit öffnen, dass auch ausgeschlafene Nächte als
        weckergekappt gelten.
        """
        return min(
            MAX_ALARM_TOLERANCE_MIN,
            max(ALARM_ALIGNMENT_MAX_MIN, 2.0 * (self.workday_spread or 0.0)),
        )


def _wake_minutes(night: SleepNight) -> float | None:
    """Aufwachzeit als Minuten nach Mitternacht."""
    if night.wake_time is None or not night.has_timing:
        return None
    return night.wake_time.hour * 60.0 + night.wake_time.minute


def wake_profile(nights: list[SleepNight], *, free_days: frozenset[int]) -> WakeProfile:
    """Verteilung der Aufwachzeiten an Arbeits- und freien Tagen bestimmen."""
    workday: list[float] = []
    free: list[float] = []
    for night in nights:
        minutes = _wake_minutes(night)
        if minutes is None:
            continue
        (free if night.date.weekday() in free_days else workday).append(minutes)

    def summarise(values: list[float]) -> tuple[float | None, float | None]:
        if len(values) < MIN_WAKE_SAMPLES:
            return None, None
        return circular_mean_minutes(values), circular_std_minutes(values)

    workday_center, workday_spread = summarise(workday)
    free_center, free_spread = summarise(free)

    profile = WakeProfile(workday_center, workday_spread, free_center, free_spread)
    if workday_center is None:
        return profile

    # Dasselbe gedeckelte Fenster wie bei der Einzelnachtprüfung. Ohne den Deckel
    # würde eine breit gestreute Verteilung ihr eigenes Toleranzfenster so weit
    # aufziehen, dass sie sich selbst als eng gebündelt bescheinigt.
    tolerance = profile.alarm_tolerance
    near = sum(
        1 for value in workday if circular_difference_minutes(value, workday_center) <= tolerance
    )
    return replace(profile, workday_concentration=near / len(workday))


def classify_nights(
    nights: list[SleepNight], *, free_days: frozenset[int]
) -> tuple[frozenset[date], float | None]:
    """Nächte einzeln danach einteilen, ob ihr Schlaf von selbst endete.

    Eine einzige, symmetrische Regel: **Eine Nacht ist ungestört, wenn ihre
    Aufwachzeit deutlich vom gewohnten Weckzeitpunkt abweicht** — in welche
    Richtung, ist gleichgültig. Wer an einem Dienstag um 08:00 statt 06:15
    aufwacht, hat Urlaub; wer eine Stunde vor dem Wecker aufwacht, hat
    ausgeschlafen. Beides sind Nächte, die der Wecker nicht beendet hat.

    Der Wochentag spielt dabei bewusst **keine** Rolle. Zwei Fehler, die genau
    daran hingen, sind als Tests hinterlegt: Urlaubstage unter der Woche wurden
    übersehen, solange nur früheres Erwachen zählte, und eine einzelne
    Urlaubswoche verdeckte die Weckerbindung aller übrigen Wochenenden, solange
    über die Gruppe statt über die einzelne Nacht geurteilt wurde.

    Gibt die ungestörten Daten zurück sowie den Anteil freier Nächte, die
    erkennbar am Wecker endeten — letzteres nur zur Anzeige.
    """
    profile = wake_profile(nights, free_days=free_days)

    if not profile.has_alarm_pattern:
        # Kein erkennbares Weckermuster: Dann bleibt nur der Kalender.
        return frozenset(n.date for n in nights if n.date.weekday() in free_days), None

    center = profile.workday_center
    assert center is not None
    tolerance = profile.alarm_tolerance

    found: set[date] = set()
    free_total = 0
    free_alarm_bound = 0
    for night in nights:
        minutes = _wake_minutes(night)
        if minutes is None:
            continue
        unconstrained = circular_difference_minutes(minutes, center) > tolerance
        if unconstrained:
            found.add(night.date)
        if night.date.weekday() in free_days:
            free_total += 1
            free_alarm_bound += not unconstrained

    share = free_alarm_bound / free_total if free_total else None
    return frozenset(found), share


# -- Läufe ungestörter Nächte -----------------------------------------------------


def free_runs(
    nights: list[SleepNight],
    *,
    free_days: frozenset[int],
    forced_free: frozenset[date] = frozenset(),
) -> list[list[SleepNight]]:
    """Zusammenhängende Läufe ungestörter Nächte bilden.

    Eine Nacht gilt als ungestört, wenn am Morgen danach kein Wecker zu erwarten
    war — also der Wochentag des Aufwachens frei ist oder das Datum ausdrücklich
    als frei markiert wurde (Kalibrierfenster, Urlaub).
    """
    usable = sorted(
        (n for n in nights if n.has_duration and n.total_min is not None),
        key=lambda n: n.date,
    )
    runs: list[list[SleepNight]] = []
    current: list[SleepNight] = []
    for night in usable:
        is_free = night.date in forced_free or night.date.weekday() in free_days
        if not is_free:
            if current:
                runs.append(current)
                current = []
            continue
        if current and (night.date - current[-1].date).days != 1:
            runs.append(current)
            current = []
        current.append(night)
    if current:
        runs.append(current)
    return runs


def _positions(runs: list[list[SleepNight]]) -> list[tuple[int, float]]:
    """(Position im Lauf, Schlafdauer) über alle Läufe."""
    return [
        (index, night.total_min)
        for run in runs
        for index, night in enumerate(run)
        if night.total_min is not None
    ]


# -- Asymptotenfit ----------------------------------------------------------------


def fit_asymptote(
    samples: list[tuple[int, float]], *, tau: float = REBOUND_TAU_NIGHTS
) -> tuple[float, float] | None:
    """Asymptote und Reststreuung aus (Position, Dauer)-Paaren bestimmen.

    Lineare Ausgleichsrechnung von ``Dauer`` gegen ``exp(−Position/τ)``. Der
    Achsenabschnitt ist die Asymptote, also der gesuchte Bedarf.

    Gibt ``(Asymptote, Reststreuung)`` zurück oder ``None``, wenn die Datenlage
    keinen Fit trägt.
    """
    if len(samples) < 3:
        return None

    xs = [math.exp(-position / tau) for position, _ in samples]
    ys = [duration for _, duration in samples]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance <= 1e-12:
        # Alle Nächte an derselben Position — keine Steigung bestimmbar.
        return None

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / variance
    if slope < 0.0:
        # Kein abklingender Verlauf: Die Person holt nichts nach, es gibt also
        # keinen Rebound zu entfernen. Dann ist der Mittelwert die beste Schätzung.
        slope = 0.0
    intercept = mean_y - slope * mean_x

    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys, strict=True)]
    spread = statistics.stdev(residuals) if len(residuals) > 2 else None
    return intercept, (spread if spread is not None else 0.0)


def _finalize(
    raw: float,
    *,
    method: NeedMethod,
    nights: int,
    runs: int,
    spread: float | None,
    plateau: bool = False,
    confidence_penalty: float = 0.0,
) -> NeedEstimate:
    """Rohwert begrenzen und in ein Ergebnis mit Konfidenz überführen."""
    clamped_value = max(MIN_PLAUSIBLE_NEED_MIN, min(MAX_PLAUSIBLE_NEED_MIN, raw))
    was_clamped = abs(clamped_value - raw) > 0.5
    confidence = max(0.0, BASE_CONFIDENCE[method] - confidence_penalty)
    if was_clamped:
        # Ein Wert außerhalb des plausiblen Bereichs deutet auf schlechte Daten hin.
        confidence *= 0.5
    return NeedEstimate(
        minutes=round(clamped_value, 1),
        method=method,
        confidence=round(confidence, 2),
        nights_used=nights,
        runs_used=runs,
        uncertainty_min=None if spread is None else round(spread, 1),
        plateau_reached=plateau,
        clamped=was_clamped,
    )


def _empty(reason: NeedReason, *, nights: int = 0, runs: int = 0) -> NeedEstimate:
    return NeedEstimate(
        minutes=None,
        method=NeedMethod.NONE,
        confidence=0.0,
        nights_used=nights,
        runs_used=runs,
        uncertainty_min=None,
        reason=reason,
    )


# -- Die drei Verfahren -----------------------------------------------------------


def estimate_from_calibration(
    nights: list[SleepNight], *, window: frozenset[date]
) -> NeedEstimate:
    """Bedarf aus einem ausdrücklich markierten Kalibrierfenster bestimmen.

    Die direkte Heimnachbildung des publizierten Protokolls: ein Lauf ohne
    Wecker, an den die Abklingkurve gelegt wird.

    Dieses Verfahren braucht **keine** Aufwachzeiten. Der Nutzer versichert mit
    dem Markieren des Fensters, dass kein Wecker gestellt war — deshalb ist es
    der einzige Weg, der auch für durchgehend weckergebundene Menschen trägt.
    """
    runs = free_runs(nights, free_days=frozenset(), forced_free=window)
    if not runs:
        return _empty(NeedReason.TOO_FEW_SAMPLES)
    longest = max(runs, key=len)
    if len(longest) < MIN_CALIBRATION_NIGHTS:
        return _empty(NeedReason.CALIBRATION_TOO_SHORT, nights=len(longest), runs=len(runs))

    fit = fit_asymptote(_positions([longest]))
    if fit is None:
        return _empty(NeedReason.TOO_FEW_SAMPLES, nights=len(longest), runs=len(runs))

    asymptote, spread = fit
    plateau = len(longest) >= PLATEAU_NIGHTS
    return _finalize(
        asymptote,
        method=NeedMethod.CALIBRATION,
        nights=len(longest),
        runs=1,
        spread=spread,
        plateau=plateau,
        # Ein zu kurzer Lauf hat das Plateau nicht gesehen — die Asymptote ist
        # dann eine Extrapolation und verdient weniger Vertrauen.
        confidence_penalty=0.0 if plateau else 0.25,
    )


def unconstrained_dates(
    nights: list[SleepNight], *, free_days: frozenset[int]
) -> tuple[frozenset[date], NeedReason]:
    """Nächte bestimmen, deren Schlaf von selbst endete."""
    if not any(night.has_timing for night in nights):
        # Ohne Aufwachzeiten lässt sich die Weckerbindung nicht prüfen. Dann
        # lieber gar nicht schätzen als eine weckergekappte Nacht für den
        # Bedarf zu halten — eine Unterschätzung beruhigt fälschlich.
        return frozenset(), NeedReason.NO_TIMING

    found, alarm_share = classify_nights(nights, free_days=free_days)
    if found:
        return found, NeedReason.OK

    constrained = alarm_share is not None and alarm_share > 0.8
    return frozenset(), (
        NeedReason.ALARM_CONSTRAINED if constrained else NeedReason.TOO_FEW_SAMPLES
    )


def estimate_from_free_runs(
    nights: list[SleepNight], *, free_days: frozenset[int]
) -> NeedEstimate:
    """Bedarf aus gepoolten ungestörten Läufen bestimmen."""
    free_dates, reason = unconstrained_dates(nights, free_days=free_days)
    if reason is not NeedReason.OK:
        return _empty(reason)

    runs = free_runs(nights, free_days=frozenset(), forced_free=free_dates)
    samples = _positions(runs)
    distinct = {position for position, _ in samples}

    longest_run = max((len(run) for run in runs), default=0)
    # Zwei Wege zu einem tragfähigen Fit: viele kurze Läufe über Monate, oder ein
    # einzelner hinreichend langer. Der lange Lauf ist typischerweise ein Urlaub,
    # den niemand als Kalibrierung markiert hat — er soll trotzdem gefunden
    # werden, nur mit geringerer Konfidenz als ein bestätigter Kalibrierlauf.
    enough_structure = (
        len(samples) >= MIN_POOLED_NIGHTS and len(runs) >= MIN_POOLED_RUNS
    ) or longest_run >= MIN_CALIBRATION_NIGHTS
    if enough_structure and len(distinct) >= MIN_DISTINCT_POSITIONS:
        fit = fit_asymptote(samples)
        if fit is not None:
            asymptote, spread = fit
            return _finalize(
                asymptote,
                method=NeedMethod.POOLED_RUNS,
                nights=len(samples),
                runs=len(runs),
                spread=spread,
            )

    # Rückfall: die jeweils letzte Nacht eines Laufs ist am wenigsten von Rebound
    # geprägt. Unterschätzt den Bedarf eher — die ehrlichere Fehlerrichtung.
    finals = [run[-1].total_min for run in runs if run[-1].total_min is not None]
    if len(finals) < MIN_FALLBACK_RUNS:
        return _empty(NeedReason.TOO_FEW_SAMPLES, nights=len(samples), runs=len(runs))

    median = statistics.median(finals)
    deviation = statistics.median([abs(value - median) for value in finals])
    return _finalize(
        median,
        method=NeedMethod.LAST_NIGHT_OF_RUN,
        nights=len(finals),
        runs=len(runs),
        spread=deviation * 1.4826,
    )


def estimate(
    nights: list[SleepNight],
    *,
    free_days: frozenset[int],
    calibration_window: frozenset[date] = frozenset(),
) -> NeedEstimate:
    """Beste verfügbare Bedarfsschätzung liefern.

    Ein gültiger Kalibrierlauf schlägt die gepoolte Schätzung immer — er ist das
    einzige Verfahren, das den Bedarf unter annähernd gesättigten Bedingungen
    misst statt ihn aus Alltagsnächten zu extrapolieren.
    """
    if calibration_window:
        calibrated = estimate_from_calibration(nights, window=calibration_window)
        if calibrated.is_usable:
            return calibrated
    return estimate_from_free_runs(nights, free_days=free_days)


# -- Langsame Übernahme -----------------------------------------------------------

#: Höchstens so viele Minuten darf sich der angewandte Bedarf je Tag verschieben.
#: Entspricht rund fünf Minuten im Monat — schnell genug, um einer echten
#: Veränderung über Monate zu folgen, langsam genug, dass das Konto über Jahre
#: vergleichbar bleibt. Ein springender Bedarf sähe im Verlauf aus wie eine
#: plötzliche Schlafkrise, obwohl sich nur der Nenner geändert hat.
MAX_DRIFT_MIN_PER_DAY = 1.0 / 6.0

#: Unterhalb dieser Konfidenz wird eine Schätzung nicht automatisch übernommen.
MIN_CONFIDENCE_TO_ADOPT = 0.5


def adapt(
    applied_min: float,
    estimate_result: NeedEstimate,
    *,
    elapsed_days: float,
    max_drift_per_day: float = MAX_DRIFT_MIN_PER_DAY,
    min_confidence: float = MIN_CONFIDENCE_TO_ADOPT,
) -> float:
    """Den angewandten Bedarf gedeckelt in Richtung der Schätzung ziehen."""
    if (
        not estimate_result.is_usable
        or estimate_result.minutes is None
        or estimate_result.confidence < min_confidence
        or elapsed_days <= 0
    ):
        return applied_min

    limit = max_drift_per_day * elapsed_days
    difference = estimate_result.minutes - applied_min
    step = max(-limit, min(limit, difference))
    return applied_min + step
