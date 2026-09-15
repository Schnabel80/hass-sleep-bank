"""Tests für die physiologischen Baselines."""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest
from sleep_ledger import physiology
from sleep_ledger.models import SleepNight

START = date(2026, 1, 1)


def baseline_nights(days: int = 28, seed: int = 3) -> list[SleepNight]:
    rng = random.Random(seed)
    return [
        SleepNight(
            date=START + timedelta(days=i),
            total_min=450.0,
            resting_hr=66 + rng.gauss(0, 1.5),
            hrv_ms=40 + rng.gauss(0, 5),
            respiratory_rate=14 + rng.gauss(0, 0.5),
        )
        for i in range(days)
    ]


def test_baseline_nacht_liegt_nahe_null() -> None:
    nights = [
        *baseline_nights(),
        SleepNight(
            date=START + timedelta(days=28),
            total_min=450.0,
            resting_hr=66.0,
            hrv_ms=40.0,
            respiratory_rate=14.0,
        ),
    ]
    result = physiology.compute(nights, today=START + timedelta(days=28))
    assert result.recovery_z is not None
    assert abs(result.recovery_z) < 0.6


def test_belastete_nacht_ergibt_negativen_score() -> None:
    """Erhöhter Ruhepuls, eingebrochene HRV, schnellere Atmung."""
    nights = [
        *baseline_nights(),
        SleepNight(
            date=START + timedelta(days=28),
            total_min=300.0,
            resting_hr=76.0,
            hrv_ms=22.0,
            respiratory_rate=16.0,
        ),
    ]
    result = physiology.compute(nights, today=START + timedelta(days=28))
    assert result.recovery_z is not None
    assert result.recovery_z < -1.5
    assert result.resting_hr_z is not None and result.resting_hr_z < 0
    assert result.hrv_z is not None and result.hrv_z < 0


def test_hohe_hrv_ist_positiv_hoher_puls_negativ() -> None:
    """Die Vorzeichen müssen stimmen, sonst sagt der Sensor das Gegenteil."""
    nights = [
        *baseline_nights(),
        SleepNight(date=START + timedelta(days=28), total_min=450.0, resting_hr=60.0, hrv_ms=55.0),
    ]
    result = physiology.compute(nights, today=START + timedelta(days=28))
    assert result.resting_hr_z is not None and result.resting_hr_z > 0
    assert result.hrv_z is not None and result.hrv_z > 0


def test_zu_kurze_historie_liefert_keine_zahl() -> None:
    nights = [*baseline_nights(days=5), baseline_nights(days=6)[-1]]
    result = physiology.compute(nights, today=START + timedelta(days=5))
    assert result.recovery_z is None


def test_ausreisser_verschiebt_die_baseline_kaum() -> None:
    """Median und MAD dürfen sich von einem Infekt nicht mitziehen lassen."""
    clean = baseline_nights()
    spiked = [*clean]
    spiked[10] = SleepNight(
        date=spiked[10].date, total_min=450.0, resting_hr=110.0, hrv_ms=8.0, respiratory_rate=22.0
    )
    probe = SleepNight(
        date=START + timedelta(days=28), total_min=450.0, resting_hr=66.0, hrv_ms=40.0
    )
    a = physiology.compute([*clean, probe], today=probe.date)
    b = physiology.compute([*spiked, probe], today=probe.date)
    assert a.resting_hr_z is not None and b.resting_hr_z is not None
    assert abs(a.resting_hr_z - b.resting_hr_z) < 0.5


def test_leere_historie() -> None:
    result = physiology.compute([], today=START)
    assert result.recovery_z is None
    assert result.baseline_samples == 0


def test_zu_gleichfoermige_baseline_sprengt_den_score_nicht() -> None:
    """Ohne Streuung im Nenner wären die z-Scores sonst unendlich."""
    flat = [
        SleepNight(date=START + timedelta(days=i), total_min=450.0, resting_hr=60.0)
        for i in range(28)
    ]
    probe = SleepNight(date=START + timedelta(days=28), total_min=450.0, resting_hr=62.0)
    result = physiology.compute([*flat, probe], today=probe.date)
    assert result.resting_hr_z is not None
    assert result.resting_hr_z == pytest.approx(-2.0)
