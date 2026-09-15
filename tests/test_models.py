"""Tests für das Datenmodell."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sleep_bank.models import Provenance, SleepNight, coverage

BERLIN = timezone(timedelta(hours=2))


def sample_night() -> SleepNight:
    return SleepNight(
        date=date(2026, 9, 14),
        total_min=395.0,
        core_min=264.0,
        deep_min=39.0,
        rem_min=93.0,
        awake_min=3.0,
        onset_time=datetime(2026, 9, 13, 23, 31, tzinfo=BERLIN),
        wake_time=datetime(2026, 9, 14, 6, 9, tzinfo=BERLIN),
        anchor_provenance=Provenance.FOCUS,
        confidence=0.8,
    )


def test_abgeleitete_werte() -> None:
    night = sample_night()
    assert night.span_min == pytest.approx(398.0)
    assert night.efficiency == pytest.approx(395 / 398)
    assert night.has_stages
    assert night.has_timing
    assert night.midpoint == datetime(2026, 9, 14, 2, 50, tzinfo=BERLIN)


def test_serialisierung_ist_verlustfrei() -> None:
    night = sample_night()
    assert SleepNight.from_dict(night.to_dict()) == night


def test_unbekannte_felder_im_store_brechen_nicht() -> None:
    """Ein Downgrade nach einem Update darf den Store nicht unlesbar machen."""
    raw = sample_night().to_dict()
    raw["ein_feld_aus_der_zukunft"] = 42
    assert SleepNight.from_dict(raw).date == date(2026, 9, 14)


def test_nacht_ohne_stadien_ist_gueltig() -> None:
    """Ältere Uhrenmodelle liefern keine Schlafstadien."""
    night = SleepNight(date=date(2026, 9, 14), total_min=420.0)
    assert night.has_duration
    assert not night.has_stages
    assert not night.has_timing
    assert night.midpoint is None


def test_geseedete_nacht_traegt_keine_uhrzeitmetriken() -> None:
    night = SleepNight(
        date=date(2026, 9, 14),
        total_min=420.0,
        onset_time=datetime(2026, 9, 13, 23, 0, tzinfo=BERLIN),
        wake_time=datetime(2026, 9, 14, 6, 0, tzinfo=BERLIN),
        anchor_provenance=Provenance.SEEDED,
    )
    assert not night.has_timing


def test_abdeckung_zaehlt_naechte_nicht_minuten() -> None:
    nights = [
        SleepNight(date=date(2026, 9, 8) + timedelta(days=i), total_min=420.0) for i in range(5)
    ]
    assert coverage(nights, 7, date(2026, 9, 14)) == pytest.approx(5 / 7)
    assert coverage(nights, 0, date(2026, 9, 14)) == 0.0
