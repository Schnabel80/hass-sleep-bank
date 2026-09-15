"""Tests für Einrichtung, Entladen und die erzeugten Entitäten."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState

from custom_components.sleep_ledger.const import DOMAIN
from custom_components.sleep_ledger.models import SleepNight

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

EXPECTED_SENSORS = {
    "sensor.sleep_ledger_acute_sleep_debt",
    "sensor.sleep_ledger_chronic_deficit_per_night",
    "sensor.sleep_ledger_nights_to_recovery",
    "sensor.sleep_ledger_sleep_regularity_index",
    "sensor.sleep_ledger_social_jetlag",
    "sensor.sleep_ledger_readiness",
    "sensor.sleep_ledger_data_coverage",
}


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_einrichten_und_entladen(hass: HomeAssistant, source_states, config_entry) -> None:
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_entitaeten_werden_angelegt(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    await _setup(hass, config_entry)
    existing = set(hass.states.async_entity_ids())
    missing = EXPECTED_SENSORS - existing
    assert not missing, f"fehlende Entitäten: {sorted(missing)}"
    assert "binary_sensor.sleep_ledger_sleep_debt_warning" in existing
    assert "button.sleep_ledger_recalculate" in existing
    assert "number.sleep_ledger_sleep_need" in existing


async def test_ein_geraet_je_eintrag(hass: HomeAssistant, source_states, config_entry) -> None:
    from homeassistant.helpers import device_registry as dr

    await _setup(hass, config_entry)
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert len(devices) == 1
    assert devices[0].identifiers == {(DOMAIN, config_entry.entry_id)}


async def test_ohne_historie_keine_erfundenen_zahlen(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    """Der wichtigste Vertrag der Integration: keine Zahl ohne Datenbasis."""
    await _setup(hass, config_entry)
    for entity_id in (
        "sensor.sleep_ledger_chronic_deficit_per_night",
        "sensor.sleep_ledger_sleep_regularity_index",
        "sensor.sleep_ledger_social_jetlag",
    ):
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "unknown", f"{entity_id} = {state.state}"


async def test_unzureichende_datenlage_wird_gemeldet(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    await _setup(hass, config_entry)
    state = hass.states.get("binary_sensor.sleep_ledger_insufficient_data")
    assert state is not None
    assert state.state == "on"


async def test_eintrag_entfernen_loescht_die_historie(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    """Nach dem Entfernen darf nichts Persistiertes zurückbleiben."""
    from custom_components.sleep_ledger.store import NightStore

    await _setup(hass, config_entry)
    entry_id = config_entry.entry_id
    await config_entry.runtime_data.store.async_put_many(
        [SleepNight(date=date(2026, 8, 15) + timedelta(days=i), total_min=420.0) for i in range(5)]
    )

    assert await hass.config_entries.async_remove(entry_id)
    await hass.async_block_till_done()

    survivor = NightStore(hass, entry_id)
    await survivor.async_load()
    assert survivor.nights == []
    assert survivor.meta == {}
