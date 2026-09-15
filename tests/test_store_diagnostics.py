"""Tests für Persistenz und Diagnose."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from custom_components.sleep_bank.diagnostics import async_get_config_entry_diagnostics
from custom_components.sleep_bank.models import Provenance, SleepNight
from custom_components.sleep_bank.store import NightStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

START = date(2026, 8, 15)


def _night(offset: int) -> SleepNight:
    return SleepNight(date=START + timedelta(days=offset), total_min=420.0)


async def test_speichern_und_laden(hass: HomeAssistant) -> None:
    store = NightStore(hass, "entry-1")
    await store.async_put(_night(0))
    await store.async_put(_night(1))

    reloaded = NightStore(hass, "entry-1")
    await reloaded.async_load()
    assert [n.date for n in reloaded.nights] == [START, START + timedelta(days=1)]


async def test_seeding_ueberschreibt_gemessene_naechte_nicht(hass: HomeAssistant) -> None:
    """Eine Schätzung darf keinen Messwert verdrängen."""
    store = NightStore(hass, "entry-2")
    measured = SleepNight(date=START, total_min=395.0, anchor_provenance=Provenance.FOCUS)
    await store.async_put(measured)

    seeded = SleepNight(date=START, total_min=999.0, anchor_provenance=Provenance.SEEDED)
    added = await store.async_put_many([seeded, _night(1)])

    assert added == 1
    assert store.get(START) == measured


async def test_defekter_eintrag_kippt_das_laden_nicht(hass: HomeAssistant) -> None:
    """Ein unlesbarer Datensatz darf nicht die ganze Historie unbrauchbar machen."""
    store = NightStore(hass, "entry-3")
    await store.async_put(_night(0))
    await store._store.async_save(
        {"nights": [_night(0).to_dict(), {"date": "kein datum"}, _night(1).to_dict()]}
    )

    reloaded = NightStore(hass, "entry-3")
    await reloaded.async_load()
    assert len(reloaded.nights) == 2


async def test_entfernen_leert_den_speicher(hass: HomeAssistant) -> None:
    store = NightStore(hass, "entry-4")
    await store.async_put(_night(0))
    await store.async_remove()
    assert store.nights == []


async def test_diagnose_enthaelt_historie_und_herkunft(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    """Die Diagnose muss eine unplausible Kennzahl zurückverfolgbar machen."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    await coordinator.store.async_put_many([_night(i) for i in range(20)])
    await coordinator.async_refresh()

    report = await async_get_config_entry_diagnostics(hass, config_entry)

    assert report["summary"]["stored_nights"] >= 20
    assert report["summary"]["readiness_is_heuristic"] is True
    assert len(report["nights"]) >= 20
    assert "anchor_provenance" in report["nights"][0]
    assert "confidence" in report["nights"][0]
