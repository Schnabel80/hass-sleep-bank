"""Tests für die Aufwach-Ankerbestimmung.

Die Kernfälle stammen aus einem echten Sensorverlauf (siehe ``real_history``).
Jeder von ihnen hat die Integration in der Praxis zum Stolpern gebracht, bevor
die entsprechende Regel eingebaut wurde.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from real_history import BERLIN, FOCUS_HISTORY, SLEEP_ARRIVALS, _at
from sleep_bank import timing
from sleep_bank.models import Provenance


def _steps(entries: list[tuple[str, float]]) -> list[tuple[datetime, float]]:
    return [(_at(stamp), value) for stamp, value in entries]


# -- Bereinigung des Zustandsverlaufs ---------------------------------------------


def test_unavailable_blip_erzeugt_keinen_uebergang() -> None:
    """Ein App-Neustart darf nicht als Zustandswechsel gelten."""
    history = [
        (_at("2026-09-13 07:12:01"), "off"),
        (_at("2026-09-13 07:31:37"), "unavailable"),
        (_at("2026-09-13 07:31:37"), "off"),
    ]
    assert timing.clean_state_history(history) == [(_at("2026-09-13 07:12:01"), "off")]


def test_focus_phasen_aus_echtem_verlauf() -> None:
    phases = timing.focus_phases(FOCUS_HISTORY)
    assert (_at("2026-09-07 22:03:34"), _at("2026-09-08 06:17:20")) in phases
    assert (_at("2026-09-08 13:16:29"), _at("2026-09-08 14:29:23")) in phases
    assert (_at("2026-09-08 21:50:49"), _at("2026-09-10 05:46:40")) in phases


# -- Fokus-Kandidat ---------------------------------------------------------------


def test_normale_nacht_liefert_fokus_anker() -> None:
    result = timing.determine_anchor(
        day=date(2026, 9, 8), focus_history=FOCUS_HISTORY, tzinfo=BERLIN
    )
    assert result.provenance is Provenance.FOCUS
    assert result.wake_time == _at("2026-09-08 06:17:20")


def test_tages_fokus_wird_verworfen() -> None:
    """08.09., 13:16-14:29 Uhr: Fokus am Nachmittag, kein Schlaf."""
    phases = [(_at("2026-09-08 13:16:29"), _at("2026-09-08 14:29:23"))]
    rejected: dict[str, str] = {}
    window = timing.morning_window(date(2026, 9, 8), BERLIN)
    assert timing.focus_candidate(phases, window, rejected) is None


def test_haengender_fokus_wird_verworfen() -> None:
    """08.-10.09.: 32 Stunden am Stück 'on' — ein Übergang ging verloren."""
    result = timing.determine_anchor(
        day=date(2026, 9, 10), focus_history=FOCUS_HISTORY, tzinfo=BERLIN
    )
    assert result.wake_time is None
    assert result.rejected[str(Provenance.FOCUS)] == str(timing.Rejection.FOCUS_TOO_LONG)


def test_naechtlicher_aussetzer_stoert_nicht() -> None:
    """12.09.: kurzer off/on-Blip um 00:44 — die zweite Phase trägt den Anker."""
    result = timing.determine_anchor(
        day=date(2026, 9, 12), focus_history=FOCUS_HISTORY, tzinfo=BERLIN
    )
    assert result.wake_time == _at("2026-09-12 06:15:47")


# -- Ausfall des Fokus-Sensors ----------------------------------------------------


def test_ohne_fokus_traegt_die_datenankunft(caplog: pytest.LogCaptureFixture) -> None:
    """Nacht 13.->14.09.: kein Fokus-Übergang, aber Schlafdaten um 06:09."""
    arrival, _total, _awake = SLEEP_ARRIVALS["2026-09-14"]
    result = timing.determine_anchor(
        day=date(2026, 9, 14),
        focus_history=FOCUS_HISTORY,
        data_arrival=arrival,
        tzinfo=BERLIN,
    )
    assert result.provenance is Provenance.DATA_ARRIVAL
    assert result.wake_time is not None
    assert result.confidence == pytest.approx(0.5)


def test_konsens_erhoeht_die_konfidenz() -> None:
    """13.09.: Fokus-Aus 07:12 und Datenankunft 07:30 stützen sich."""
    arrival, _total, _awake = SLEEP_ARRIVALS["2026-09-13"]
    result = timing.determine_anchor(
        day=date(2026, 9, 13),
        focus_history=FOCUS_HISTORY,
        data_arrival=arrival,
        tzinfo=BERLIN,
    )
    assert result.provenance is Provenance.FOCUS
    assert result.confidence > timing.BASE_CONFIDENCE[Provenance.FOCUS]
    assert len(result.candidates) == 2


def test_ohne_jedes_signal_kein_anker() -> None:
    result = timing.determine_anchor(day=date(2026, 9, 14), tzinfo=BERLIN)
    assert result.wake_time is None
    assert result.provenance is Provenance.NONE
    assert result.confidence == 0.0


# -- Schrittzähler ----------------------------------------------------------------


def test_mitternachts_reset_erzeugt_keinen_ausschlag() -> None:
    history = _steps(
        [
            ("2026-09-13 23:40:00", 8400.0),
            ("2026-09-14 00:10:00", 0.0),
            ("2026-09-14 00:40:00", 12.0),
        ]
    )
    deltas = timing.step_deltas(history)
    assert [round(d) for _, d in deltas] == [12]


def test_naechtlicher_klogang_beendet_den_schlaf_nicht() -> None:
    """20 Schritte um 03:00, echtes Aufstehen erst um 06:30."""
    history = _steps(
        [
            ("2026-09-13 23:30:00", 0.0),
            ("2026-09-14 03:00:00", 20.0),
            ("2026-09-14 03:05:00", 34.0),
            ("2026-09-14 06:30:00", 120.0),
            ("2026-09-14 06:45:00", 260.0),
            ("2026-09-14 07:10:00", 480.0),
        ]
    )
    result = timing.determine_anchor(day=date(2026, 9, 14), steps_history=history, tzinfo=BERLIN)
    assert result.provenance is Provenance.STEPS
    assert result.wake_time is not None
    # Anker minus Signalversatz: Aktivitätsbeginn 06:30.
    assert result.wake_time == _at("2026-09-14 06:30:00") - timing.ANCHOR_LAG[Provenance.STEPS]
    assert result.interruptions == 1


def test_zu_wenig_schritte_ist_kein_aufstehen() -> None:
    history = _steps([("2026-09-14 06:30:00", 0.0), ("2026-09-14 06:40:00", 30.0)])
    result = timing.determine_anchor(day=date(2026, 9, 14), steps_history=history, tzinfo=BERLIN)
    assert result.wake_time is None


# -- Rückrechnung -----------------------------------------------------------------


def test_rueckrechnung_der_einschlafzeit() -> None:
    arrival, total, awake = SLEEP_ARRIVALS["2026-09-14"]
    result = timing.determine_anchor(day=date(2026, 9, 14), data_arrival=arrival, tzinfo=BERLIN)
    onset = timing.derive_onset(result.wake_time, total, awake)
    assert onset is not None
    assert result.wake_time is not None
    assert result.wake_time - onset == timedelta(minutes=total + awake)
    assert timing.onset_is_plausible(onset)


def test_unplausible_einschlafzeit_wird_erkannt() -> None:
    """Eine zurückgerechnete Einschlafzeit am Vormittag kann nicht stimmen."""
    assert not timing.onset_is_plausible(_at("2026-09-13 11:00:00"))


def test_einschlafzeit_muss_zum_fokus_beginn_passen() -> None:
    phases = [(_at("2026-09-13 22:59:03"), _at("2026-09-14 07:12:01"))]
    assert timing.onset_is_plausible(_at("2026-09-13 23:20:00"), phases)
    assert not timing.onset_is_plausible(_at("2026-09-13 20:00:00"), phases)


def test_ohne_dauer_keine_einschlafzeit() -> None:
    assert timing.derive_onset(_at("2026-09-14 06:09:00"), None, None) is None


def test_fehlender_fokus_macht_die_einschlafzeit_nicht_unplausibel() -> None:
    """Fehlende Evidenz ist keine Gegenevidenz.

    In der Nacht 13.->14.09. lieferte der Fokus-Sensor nichts. Ohne die
    Relevanzgrenze würde die zurückgerechnete Einschlafzeit gegen die Phase vom
    12.09. geprüft und fälschlich verworfen.
    """
    onset = _at("2026-09-13 23:21:00")
    assert timing.onset_is_plausible(onset, timing.focus_phases(FOCUS_HISTORY))


def test_widerspruch_zum_fokus_derselben_nacht_wird_erkannt() -> None:
    """Passt eine Fokus-Phase zur Nacht, muss die Einschlafzeit zu ihr passen."""
    phases = [(_at("2026-09-13 22:59:00"), _at("2026-09-14 07:12:00"))]
    assert timing.onset_is_plausible(_at("2026-09-13 23:20:00"), phases)
    assert not timing.onset_is_plausible(_at("2026-09-13 19:30:00"), phases)
