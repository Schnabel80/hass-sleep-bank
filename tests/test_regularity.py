"""Tests für Regularität, Schlafmitte und Social Jetlag."""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sleep_bank import regularity
from sleep_bank.models import Provenance, SleepNight

BERLIN = timezone(timedelta(hours=2))
START = date(2026, 1, 1)


def night(day: date, onset_hour: int, onset_minute: int, hours: float) -> SleepNight:
    """Nacht mit Einschlafen am Vorabend und Aufwachen am Tag ``day``."""
    onset = datetime.combine(day - timedelta(days=1), time(0), tzinfo=BERLIN) + timedelta(
        hours=onset_hour, minutes=onset_minute
    )
    wake = onset + timedelta(hours=hours)
    return SleepNight(
        date=day,
        total_min=hours * 60,
        awake_min=0.0,
        onset_time=onset,
        wake_time=wake,
        anchor_provenance=Provenance.FOCUS,
        confidence=0.8,
    )


def regular_series(days: int = 31) -> list[SleepNight]:
    return [night(START + timedelta(days=i), 23, 0, 8) for i in range(days)]


# -- Sleep Regularity Index -------------------------------------------------------


def test_identische_naechte_ergeben_maximalen_sri() -> None:
    nights = regular_series()
    result = regularity.compute(nights, today=START + timedelta(days=30))
    assert result.sri == pytest.approx(100.0)
    assert result.sri_pairs >= regularity.MIN_SRI_PAIRS


def test_chaotischer_schlaf_senkt_den_sri() -> None:
    rng = random.Random(7)
    nights = [
        night(START + timedelta(days=i), rng.choice([20, 22, 0, 2]), 0, rng.choice([5, 7, 9]))
        for i in range(31)
    ]
    chaotic = regularity.compute(nights, today=START + timedelta(days=30))
    assert chaotic.sri is not None
    assert chaotic.sri < 80.0


def test_sri_schweigt_bei_zu_wenig_paaren() -> None:
    result = regularity.compute(regular_series(5), today=START + timedelta(days=4))
    assert result.sri is None


def test_naechte_ohne_zeitstempel_zaehlen_nicht() -> None:
    """Ohne Zeiten gibt es keinen SRI — und keine Ersatzzahl."""
    nights = [SleepNight(date=START + timedelta(days=i), total_min=480.0) for i in range(31)]
    result = regularity.compute(nights, today=START + timedelta(days=30))
    assert result.sri is None
    assert result.midpoint_minutes is None
    assert result.social_jetlag_hours is None


def test_luecke_unterbricht_das_tagespaar() -> None:
    """Ein Tag ohne bekannte Nachbarnacht darf nicht mitgezählt werden."""
    nights = [n for i, n in enumerate(regular_series()) if i != 15]
    result = regularity.compute(nights, today=START + timedelta(days=30))
    assert result.sri == pytest.approx(100.0)
    assert result.sri_pairs < 29


# -- Schlafmitte ------------------------------------------------------------------


def test_schlafmitte_und_streuung() -> None:
    result = regularity.compute(regular_series(), today=START + timedelta(days=30))
    assert result.midpoint_minutes == pytest.approx(3 * 60, abs=1.0)  # 03:00 Uhr
    assert result.midpoint_variability_min == pytest.approx(0.0, abs=0.5)


def test_zirkulaeres_mittel_ueber_mitternacht() -> None:
    """23:50 und 00:10 liegen 20 Minuten auseinander, nicht 23 Stunden 40."""
    mean = regularity.circular_mean_minutes([23 * 60 + 50, 10])
    assert mean is not None
    assert regularity.circular_difference_minutes(mean, 0.0) < 1.0


def test_zirkulaere_differenz_nimmt_den_kurzen_weg() -> None:
    assert regularity.circular_difference_minutes(23 * 60, 1 * 60) == pytest.approx(120.0)


# -- Social Jetlag ----------------------------------------------------------------


def test_social_jetlag_zwischen_werk_und_freien_tagen() -> None:
    """Werktags 23:00/7 h (Mitte 02:30), am Wochenende 01:00/9 h (Mitte 05:30)."""
    nights = []
    for index in range(28):
        day = START + timedelta(days=index)
        free = day.weekday() >= 5
        nights.append(night(day, 1 if free else 23, 0, 9 if free else 7))
    result = regularity.compute(nights, today=START + timedelta(days=27))
    assert result.social_jetlag_hours == pytest.approx(3.0, abs=0.05)


def test_social_jetlag_braucht_beide_gruppen() -> None:
    """Nur Werktage im Fenster — dann gibt es keine Vergleichsgruppe."""
    nights = [
        night(day, 23, 0, 8) for i in range(14) if (day := START + timedelta(days=i)).weekday() < 5
    ]
    result = regularity.compute(nights, today=START + timedelta(days=13))
    assert result.social_jetlag_hours is None
