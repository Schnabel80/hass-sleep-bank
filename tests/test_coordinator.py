"""Tests für die Erfassung der Nächte und die Auswertung."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from conftest import SLEEP_ENTITY

from custom_components.sleep_ledger.models import Provenance, SleepNight

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

BERLIN = ZoneInfo("Europe/Berlin")


async def _setup(hass: HomeAssistant, entry) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_morgendliche_daten_erzeugen_eine_nacht(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    """Der Alltagsfall: Die Uhr meldet die Nacht um 06:09."""
    freezer.move_to(datetime(2026, 9, 14, 6, 9, tzinfo=BERLIN))
    await _setup(hass, config_entry)

    hass.states.async_set(SLEEP_ENTITY, "412")
    await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    night = coordinator.store.get(date(2026, 9, 14))
    assert night is not None
    assert night.total_min == 412.0
    assert night.awake_min == 3.0
    assert night.resting_hr == 66.0


async def test_daten_am_nachmittag_gelten_als_nickerchen(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    """Ein Zuwachs außerhalb des Morgenfensters ist kein zweiter Nachtschlaf."""
    freezer.move_to(datetime(2026, 9, 14, 6, 9, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    hass.states.async_set(SLEEP_ENTITY, "400")
    await hass.async_block_till_done()

    freezer.move_to(datetime(2026, 9, 14, 15, 30, tzinfo=BERLIN))
    hass.states.async_set(SLEEP_ENTITY, "445")
    await hass.async_block_till_done()

    night = config_entry.runtime_data.store.get(date(2026, 9, 14))
    assert night is not None
    assert night.total_min == 400.0, "die Nachtdauer darf sich nicht ändern"
    assert night.nap_min == pytest.approx(45.0)


async def test_nickerchen_ohne_zuwachs_wird_ignoriert(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    freezer.move_to(datetime(2026, 9, 14, 6, 9, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    hass.states.async_set(SLEEP_ENTITY, "400")
    await hass.async_block_till_done()

    freezer.move_to(datetime(2026, 9, 14, 15, 30, tzinfo=BERLIN))
    hass.states.async_set(SLEEP_ENTITY, "390")
    await hass.async_block_till_done()

    night = config_entry.runtime_data.store.get(date(2026, 9, 14))
    assert night is not None
    assert night.nap_min is None


async def test_unavailable_blip_erzeugt_keine_nacht(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    """App-Neustart: unavailable und zurück auf denselben Wert."""
    freezer.move_to(datetime(2026, 9, 14, 6, 9, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data

    hass.states.async_set(SLEEP_ENTITY, "400")
    await hass.async_block_till_done()
    before = coordinator.store.get(date(2026, 9, 14))

    hass.states.async_set(SLEEP_ENTITY, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(SLEEP_ENTITY, "400")
    await hass.async_block_till_done()

    assert coordinator.store.get(date(2026, 9, 14)) == before


async def test_kennzahlen_erscheinen_mit_genug_historie(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    """Mit drei Wochen Historie liefern alle Kennzahlen Werte."""
    freezer.move_to(datetime(2026, 9, 14, 13, 0, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data

    start = date(2026, 8, 15)
    nights = []
    for index in range(30):
        day = start + timedelta(days=index)
        onset = datetime(day.year, day.month, day.day, 23, 0, tzinfo=BERLIN) - timedelta(days=1)
        nights.append(
            SleepNight(
                date=day,
                total_min=400.0,
                awake_min=5.0,
                onset_time=onset,
                wake_time=onset + timedelta(minutes=405),
                resting_hr=66.0 + (index % 3),
                hrv_ms=40.0 - (index % 4),
                respiratory_rate=14.0,
                anchor_provenance=Provenance.FOCUS,
                confidence=0.8,
            )
        )
    await coordinator.store.async_put_many(nights)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    data = coordinator.data
    assert data.debt.acute_min is not None
    assert data.debt.chronic_min_per_night == pytest.approx(80.0, abs=2.0)
    assert data.regularity.sri is not None
    assert data.readiness.score is not None
    assert data.coverage == pytest.approx(1.0)

    state = hass.states.get("sensor.sleep_ledger_chronic_deficit_per_night")
    assert state is not None and state.state == "80"


async def test_luecke_verfaelscht_das_konto_nicht(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    freezer.move_to(datetime(2026, 9, 14, 13, 0, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data

    start = date(2026, 8, 15)
    nights = [
        SleepNight(date=start + timedelta(days=i), total_min=400.0)
        for i in range(30)
        if i not in (7, 8)
    ]
    await coordinator.store.async_put_many(nights)
    await coordinator.async_refresh()

    assert coordinator.data.debt.chronic_min_per_night == pytest.approx(80.0, abs=2.0)
    assert coordinator.data.coverage == pytest.approx(1.0)


async def test_schlafbedarf_wird_zur_laufzeit_uebernommen(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    """Die Number-Entität darf das Konto ändern, ohne die Integration neu zu laden."""
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    nights = [
        SleepNight(date=date(2026, 8, 15) + timedelta(days=i), total_min=400.0) for i in range(30)
    ]
    await coordinator.store.async_put_many(nights)

    # Die Entität wird in Stunden bedient, gespeichert wird in Minuten.
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.sleep_ledger_sleep_need", "value": 7.0},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert config_entry.options["sleep_need_min"] == 420
    assert coordinator.data.need_min == 420.0
    assert hass.states.get("number.sleep_ledger_sleep_need").state == "7.0"


async def test_button_stoesst_neuberechnung_an(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    await _setup(hass, config_entry)
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.sleep_ledger_recalculate"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert config_entry.runtime_data.data is not None


async def test_speicher_ueberlebt_neustart(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    """Die Nachthistorie liegt im eigenen Store, nicht im Recorder."""
    freezer.move_to(datetime(2026, 9, 14, 6, 9, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    hass.states.async_set(SLEEP_ENTITY, "412")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    night = config_entry.runtime_data.store.get(date(2026, 9, 14))
    assert night is not None
    assert night.total_min == 412.0


async def test_ungueltige_freie_tage_fallen_auf_die_vorgabe_zurueck(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    """Eine kaputte Option darf die Auswertung nicht kippen."""
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    hass.config_entries.async_update_entry(config_entry, options={"free_days": "Montag"})
    await hass.async_block_till_done()
    assert coordinator.free_days == frozenset({5, 6})


async def test_unlesbare_zahloption_faellt_auf_die_vorgabe_zurueck(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    hass.config_entries.async_update_entry(
        config_entry, options={"debt_alert_threshold_min": "viel"}
    )
    await hass.async_block_till_done()
    assert coordinator.debt_alert_threshold == 45.0


async def test_bedarf_wird_nicht_geschaetzt_wenn_immer_der_wecker_klingelt(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    """Wer jeden Morgen geweckt wird, bekommt offen keine Schätzung.

    Eine weckergekappte Nacht als Bedarf zu nehmen, würde ihn zu klein schätzen
    und das Defizit beschönigen — die gefährlichere Fehlerrichtung.
    """
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data

    nights = []
    for offset in range(90):
        day = date(2026, 6, 1) + timedelta(days=offset)
        wake = datetime(day.year, day.month, day.day, 6, 15, tzinfo=BERLIN) + timedelta(
            minutes=offset % 7 - 3
        )
        nights.append(
            SleepNight(
                date=day,
                total_min=410.0,
                wake_time=wake,
                onset_time=wake - timedelta(minutes=410),
                anchor_provenance=Provenance.FOCUS,
                confidence=0.8,
            )
        )
    await coordinator.store.async_put_many(nights)
    await coordinator.async_refresh()

    estimate = coordinator.data.need_estimate
    assert estimate is not None
    assert estimate.minutes is None
    assert str(estimate.reason) == "all_nights_alarm_constrained"
    # Der eingestellte Wert bleibt unangetastet.
    assert coordinator.data.need_min == 480.0


async def test_kalibrierlauf_liefert_den_bedarf(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    """Der markierte Urlaub ist der Weg für durchgehend weckergebundene Menschen."""
    freezer.move_to(datetime(2026, 9, 14, 13, 0, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data

    start = date(2026, 8, 1)
    nights = [
        SleepNight(
            date=start + timedelta(days=offset),
            total_min=500.0 + 70.0 * math.exp(-offset / 1.2),
            anchor_provenance=Provenance.NONE,
        )
        for offset in range(9)
    ]
    await coordinator.store.async_put_many(nights)
    await coordinator.store.async_set_meta(
        calibration_start=start.isoformat(),
        calibration_end=(start + timedelta(days=8)).isoformat(),
    )
    await coordinator.async_refresh()

    estimate = coordinator.data.need_estimate
    assert estimate is not None
    assert str(estimate.method) == "calibration"
    assert estimate.minutes == pytest.approx(500.0, abs=15.0)
    assert estimate.plateau_reached


async def test_kalibrierschalter_setzt_das_fenster(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    freezer.move_to(datetime(2026, 9, 10, 20, 0, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.sleep_ledger_sleep_need_calibration"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert coordinator.calibration_active

    freezer.move_to(datetime(2026, 9, 14, 20, 0, tzinfo=BERLIN))
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.sleep_ledger_sleep_need_calibration"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert not coordinator.calibration_active
    assert len(coordinator.calibration_window) == 5


async def test_bedarf_wird_nur_langsam_nachgezogen(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    """Der angewandte Wert springt nie — sonst wäre das Konto über Jahre unvergleichbar."""
    freezer.move_to(datetime(2026, 9, 14, 13, 0, tzinfo=BERLIN))
    await _setup(hass, config_entry)

    # Die Option ändert die Quellkonfiguration und lädt den Eintrag neu — deshalb
    # zuerst setzen und die Coordinator-Referenz danach frisch holen.
    hass.config_entries.async_update_entry(config_entry, options={"auto_sleep_need": True})
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data

    start = date(2026, 8, 1)
    nights = [
        SleepNight(
            date=start + timedelta(days=offset),
            total_min=540.0 + 70.0 * math.exp(-offset / 1.2),
            anchor_provenance=Provenance.NONE,
        )
        for offset in range(9)
    ]
    await coordinator.store.async_put_many(nights)
    await coordinator.store.async_set_meta(
        calibration_start=start.isoformat(),
        calibration_end=(start + timedelta(days=8)).isoformat(),
    )
    await coordinator.async_refresh()

    estimate = coordinator.data.need_estimate
    assert estimate is not None and estimate.minutes is not None
    assert estimate.minutes > 520.0
    # Trotz einer um 40+ Minuten höheren Schätzung bewegt sich der Wert kaum.
    assert coordinator.data.need_min < 485.0


async def test_bessere_nacht_verdraengt_die_schlechtere_nicht(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    """Eine spätere Auswertung mit weniger Konfidenz darf nichts überschreiben."""
    freezer.move_to(datetime(2026, 9, 14, 6, 9, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data

    good = SleepNight(
        date=date(2026, 9, 14),
        total_min=400.0,
        onset_time=datetime(2026, 9, 13, 23, 20, tzinfo=BERLIN),
        wake_time=datetime(2026, 9, 14, 6, 5, tzinfo=BERLIN),
        anchor_provenance=Provenance.EXPLICIT,
        confidence=0.95,
    )
    await coordinator.store.async_put(good)

    hass.states.async_set(SLEEP_ENTITY, "401")
    await hass.async_block_till_done()

    assert coordinator.store.get(date(2026, 9, 14)) == good


async def test_mittaegliche_auswertung_holt_eine_verpasste_nacht_nach(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    """Startet HA erst nach dem Morgenfenster, fehlt die Nacht sonst dauerhaft."""
    freezer.move_to(datetime(2026, 9, 14, 13, 0, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    night = config_entry.runtime_data.store.get(date(2026, 9, 14))
    assert night is not None
    assert night.total_min == 395.0


async def test_vor_dem_morgenfenster_wird_nichts_nachgeholt(
    hass: HomeAssistant, source_states, config_entry, freezer
) -> None:
    """Um 01:00 trägt der Sensor noch die vorige Nacht — sie darf sich nicht verdoppeln."""
    freezer.move_to(datetime(2026, 9, 14, 1, 0, tzinfo=BERLIN))
    await _setup(hass, config_entry)
    assert config_entry.runtime_data.store.get(date(2026, 9, 14)) is None
