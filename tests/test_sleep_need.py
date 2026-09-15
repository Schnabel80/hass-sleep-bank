"""Tests für die Schätzung des Schlafbedarfs.

Der Bedarf ist die einflussreichste Zahl des ganzen Systems: Das chronische
Defizit folgt ihm exakt 1:1. Eine Fehlschätzung von 30 Minuten erzeugt 30 Minuten
Phantomdefizit pro Nacht — mehr als die Warnschwelle. Entsprechend streng sind
diese Tests, und entsprechend wichtig ist die Richtung des Fehlers: **eine
Unterschätzung ist gefährlicher als gar keine Schätzung**, weil sie fälschlich
beruhigt.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta

import pytest
from sleep_bank import sleep_need as need
from sleep_bank.models import Provenance, SleepNight

START = date(2026, 1, 5)  # ein Montag
FREE = frozenset({5, 6})
HOLIDAY = frozenset(date(2026, 4, 4) + timedelta(days=offset) for offset in range(9))


def night(day: date, duration: float, wake_hour: int, wake_minute: float) -> SleepNight:
    wake = datetime.combine(day, time(0)) + timedelta(hours=wake_hour, minutes=wake_minute)
    return SleepNight(
        date=day,
        total_min=round(duration, 1),
        awake_min=0.0,
        wake_time=wake,
        onset_time=wake - timedelta(minutes=duration),
        anchor_provenance=Provenance.FOCUS,
        confidence=0.8,
    )


def build(
    *, alarm_on_free_days: bool, with_holiday: bool = False, days: int = 120, seed: int = 3
) -> list[SleepNight]:
    """Nachtserien mit bekanntem wahren Bedarf.

    Der Urlaub ist so gebaut, dass die Asymptote bei 500 Minuten liegt und die
    ersten Nächte durch Rebound darüber.
    """
    rng = random.Random(seed)
    nights = []
    for offset in range(days):
        day = START + timedelta(days=offset)
        if with_holiday and day in HOLIDAY:
            position = sorted(HOLIDAY).index(day)
            duration = 500 + 60 * math.exp(-position / 1.2) + rng.gauss(0, 15)
            nights.append(night(day, duration, 8, rng.gauss(0, 40)))
        elif day.weekday() >= 5 and not alarm_on_free_days:
            position = 0 if day.weekday() == 5 else 1
            duration = 540 - position * 35 + rng.gauss(0, 20)
            nights.append(night(day, duration, 8, 30 + rng.gauss(0, 35)))
        else:
            nights.append(night(day, 410 + rng.gauss(0, 22), 6, 15 + rng.gauss(0, 8)))
    return nights


# -- Weckererkennung --------------------------------------------------------------


def test_weckermuster_wird_erkannt() -> None:
    profile = need.wake_profile(build(alarm_on_free_days=True), free_days=FREE)
    assert profile.has_alarm_pattern
    assert profile.workday_center is not None


@pytest.mark.parametrize(
    ("spread_minutes", "expected"),
    [(8, True), (25, True), (70, False), (110, False)],
)
def test_weckermuster_haengt_an_der_buendelung(spread_minutes: int, expected: bool) -> None:
    """Enge Aufwachzeiten bedeuten Wecker, breite bedeuten freien Schlaf.

    Regression: Solange das Toleranzfenster mit der Streuung mitwuchs, hat sich
    jede noch so breite Verteilung selbst als eng gebündelt bescheinigt — und
    freie Nächte wären fälschlich als weckergekappt verworfen worden.
    """
    rng = random.Random(1)
    nights = [
        night(
            START + timedelta(days=offset),
            480 + rng.gauss(0, 30),
            7,
            rng.gauss(0, spread_minutes),
        )
        for offset in range(90)
    ]
    assert need.wake_profile(nights, free_days=FREE).has_alarm_pattern is expected


def test_durchgehend_weckergebunden_liefert_keine_schaetzung() -> None:
    """Der wichtigste Fall: Wenn der Wecker jeden Morgen klingelt, gibt es nichts zu messen.

    Lieber offen keine Zahl als eine weckergekappte Nacht für den Bedarf zu
    halten — das würde den Bedarf zu klein schätzen und das Defizit beschönigen.
    """
    result = need.estimate(build(alarm_on_free_days=True), free_days=FREE)
    assert result.minutes is None
    assert result.reason is need.NeedReason.ALARM_CONSTRAINED


def test_urlaubswoche_maskiert_die_weckerbindung_nicht() -> None:
    """Regression: Eine einzelne freie Woche darf die Weckerbindung nicht verdecken.

    Mit einem Gruppentest über die Streuung war das der Fall — die Urlaubswoche
    blähte die Streuung auf, alle übrigen Wochenenden galten als frei, und die
    Schätzung fiel um rund 100 Minuten zu niedrig aus.
    """
    nights = build(alarm_on_free_days=True, with_holiday=True)
    unconstrained, alarm_share = need.classify_nights(nights, free_days=FREE)

    assert alarm_share is not None and alarm_share > 0.8, "Wochenenden bleiben weckergebunden"
    holiday_hits = len(unconstrained & HOLIDAY)
    assert holiday_hits >= 6, "die Urlaubsnächte müssen erkannt werden"
    assert len(unconstrained - HOLIDAY) <= 2, "Alltagsnächte dürfen nicht hineinrutschen"


def test_urlaubstage_unter_der_woche_werden_erkannt() -> None:
    """Regression: Später als gewohnt aufzuwachen zählt genauso wie früher.

    Solange nur früheres Erwachen als 'ungestört' galt, wurden Urlaubstage von
    Montag bis Freitag komplett übersehen.
    """
    nights = build(alarm_on_free_days=True, with_holiday=True)
    unconstrained, _ = need.classify_nights(nights, free_days=FREE)
    weekday_holidays = {day for day in HOLIDAY if day.weekday() < 5}
    assert len(unconstrained & weekday_holidays) >= 3


# -- Schätzverfahren --------------------------------------------------------------


def test_kalibrierlauf_findet_die_asymptote() -> None:
    """Der markierte Urlaub liefert den Bedarf, obwohl sonst immer ein Wecker läuft."""
    nights = build(alarm_on_free_days=True, with_holiday=True)
    result = need.estimate(nights, free_days=FREE, calibration_window=HOLIDAY)

    assert result.method is need.NeedMethod.CALIBRATION
    assert result.minutes == pytest.approx(500.0, abs=25.0)
    assert result.plateau_reached
    assert result.confidence >= 0.8


def test_unmarkierter_urlaub_wird_gefunden_aber_schwaecher_bewertet() -> None:
    """Ein langer ungestörter Lauf trägt den Fit auch ohne Markierung.

    Die Konfidenz bleibt niedriger, weil niemand bestätigt hat, dass wirklich
    kein Wecker lief.
    """
    nights = build(alarm_on_free_days=True, with_holiday=True)
    result = need.estimate(nights, free_days=FREE)

    assert result.method is need.NeedMethod.POOLED_RUNS
    assert result.minutes == pytest.approx(500.0, abs=40.0)
    assert result.confidence < need.BASE_CONFIDENCE[need.NeedMethod.CALIBRATION]


def test_zu_kurzer_kalibrierlauf_wird_abgelehnt() -> None:
    window = frozenset(sorted(HOLIDAY)[:2])
    nights = build(alarm_on_free_days=True, with_holiday=True)
    result = need.estimate_from_calibration(nights, window=window)
    assert result.minutes is None
    assert result.reason is need.NeedReason.CALIBRATION_TOO_SHORT


def test_klassische_wochenenden_liefern_den_rueckfallweg() -> None:
    """Zwei freie Nächte reichen nicht für eine Asymptote — dann die letzte Nacht."""
    result = need.estimate(build(alarm_on_free_days=False), free_days=FREE)
    assert result.method is need.NeedMethod.LAST_NIGHT_OF_RUN
    assert result.confidence <= 0.5


def test_ohne_zeitstempel_wird_nicht_geschaetzt() -> None:
    """Ohne Aufwachzeiten lässt sich die Weckerbindung nicht prüfen."""
    nights = [
        SleepNight(date=START + timedelta(days=offset), total_min=480.0) for offset in range(60)
    ]
    result = need.estimate(nights, free_days=FREE)
    assert result.minutes is None
    assert result.reason is need.NeedReason.NO_TIMING


def test_kalibrierung_braucht_keine_zeitstempel() -> None:
    """Der markierte Lauf funktioniert auch ohne Aufwachzeiten.

    Der Nutzer versichert mit dem Markieren, dass kein Wecker lief — genau
    deshalb ist dies der Weg für durchgehend weckergebundene Menschen.
    """
    nights = [
        SleepNight(
            date=day,
            total_min=500 + 60 * math.exp(-position / 1.2),
            anchor_provenance=Provenance.NONE,
        )
        for position, day in enumerate(sorted(HOLIDAY))
    ]
    result = need.estimate(nights, free_days=FREE, calibration_window=HOLIDAY)
    assert result.method is need.NeedMethod.CALIBRATION
    assert result.minutes == pytest.approx(500.0, abs=10.0)


# -- Fit und Grenzen --------------------------------------------------------------


def test_asymptotenfit_trennt_rebound_vom_bedarf() -> None:
    samples = [(position, 500 + 90 * math.exp(-position / 1.2)) for position in range(8)]
    fitted = need.fit_asymptote(samples)
    assert fitted is not None
    assert fitted[0] == pytest.approx(500.0, abs=5.0)


def test_ohne_abklingen_ist_der_mittelwert_die_antwort() -> None:
    """Wer nichts nachholt, hat keinen Rebound, den man abziehen müsste."""
    samples = [(position % 4, 470.0) for position in range(12)]
    fitted = need.fit_asymptote(samples)
    assert fitted is not None
    assert fitted[0] == pytest.approx(470.0, abs=1.0)


def test_unplausibles_ergebnis_wird_begrenzt_und_abgewertet() -> None:
    """Eine ansteigende statt abklingende Kurve extrapoliert ins Absurde."""
    samples = [(position, 900 - 50 * position) for position in range(6)]
    fitted = need.fit_asymptote(samples)
    assert fitted is not None
    result = need._finalize(
        fitted[0], method=need.NeedMethod.CALIBRATION, nights=6, runs=1, spread=None
    )
    assert result.clamped
    assert result.minutes == need.MAX_PLAUSIBLE_NEED_MIN
    assert result.confidence < need.BASE_CONFIDENCE[need.NeedMethod.CALIBRATION]


# -- Langsame Übernahme -----------------------------------------------------------


def _estimate(minutes: float, confidence: float = 0.9) -> need.NeedEstimate:
    return need.NeedEstimate(
        minutes=minutes,
        method=need.NeedMethod.CALIBRATION,
        confidence=confidence,
        nights_used=8,
        runs_used=1,
        uncertainty_min=10.0,
    )


def test_bedarf_wird_nur_gedeckelt_nachgezogen() -> None:
    """Ein Sprung im Nenner sähe im Verlauf aus wie eine plötzliche Schlafkrise."""
    applied = need.adapt(480.0, _estimate(540.0), elapsed_days=1)
    assert applied == pytest.approx(480.0 + need.MAX_DRIFT_MIN_PER_DAY)


def test_nachziehen_erreicht_die_schaetzung_ueber_monate() -> None:
    applied = 480.0
    for _ in range(365):
        applied = need.adapt(applied, _estimate(540.0), elapsed_days=1)
    assert applied == pytest.approx(540.0, abs=1.0)


def test_nachziehen_ueberschiesst_nicht() -> None:
    assert need.adapt(480.0, _estimate(482.0), elapsed_days=365) == pytest.approx(482.0)


def test_schwache_schaetzung_wird_nicht_uebernommen() -> None:
    assert need.adapt(480.0, _estimate(540.0, confidence=0.3), elapsed_days=30) == 480.0


def test_fehlende_schaetzung_laesst_den_wert_stehen() -> None:
    empty = need.NeedEstimate(
        minutes=None,
        method=need.NeedMethod.NONE,
        confidence=0.0,
        nights_used=0,
        runs_used=0,
        uncertainty_min=None,
        reason=need.NeedReason.ALARM_CONSTRAINED,
    )
    assert need.adapt(480.0, empty, elapsed_days=30) == 480.0
