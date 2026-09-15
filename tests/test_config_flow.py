"""Tests für die Einrichtung über die Oberfläche."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import BASE_CONFIG, SLEEP_ENTITY
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sleep_ledger.config_flow import autodetect
from custom_components.sleep_ledger.const import (
    CONF_AUTO_SLEEP_NEED,
    CONF_FOCUS,
    CONF_FREE_DAYS,
    CONF_SLEEP_DURATION,
    CONF_SLEEP_NEED,
    CONF_STEPS,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def _start(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_vollstaendiger_ablauf(hass: HomeAssistant, source_states) -> None:
    """Drei Schritte bis zum fertigen Eintrag."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SLEEP_DURATION: SLEEP_ENTITY}
    )
    assert result["step_id"] == "timing"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_FOCUS: "binary_sensor.phone_focus"}
    )
    assert result["step_id"] == "personal"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SLEEP_NEED: 450, CONF_AUTO_SLEEP_NEED: False, CONF_FREE_DAYS: ["5", "6"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SLEEP_DURATION] == SLEEP_ENTITY
    assert result["data"][CONF_SLEEP_NEED] == 450


async def test_entitaet_ohne_zahlwert_wird_abgelehnt(hass: HomeAssistant) -> None:
    """Ohne auswertbare Schlafdauer hat die Integration keine Grundlage."""
    hass.states.async_set(SLEEP_ENTITY, "unavailable")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SLEEP_DURATION: SLEEP_ENTITY}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SLEEP_DURATION: "no_numeric_state"}


async def test_fehlende_entitaet_wird_abgelehnt(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SLEEP_DURATION: "sensor.gibt_es_nicht"}
    )
    assert result["errors"] == {CONF_SLEEP_DURATION: "no_numeric_state"}


async def test_doppelte_einrichtung_wird_abgebrochen(hass: HomeAssistant, source_states) -> None:
    """Dieselbe Schlafdauer-Entität darf nur einmal ausgewertet werden."""
    existing = MockConfigEntry(domain=DOMAIN, data=dict(BASE_CONFIG), unique_id=SLEEP_ENTITY)
    existing.add_to_hass(hass)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SLEEP_DURATION: SLEEP_ENTITY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_autoerkennung_findet_companion_sensoren(hass: HomeAssistant, source_states) -> None:
    """Die Vorauswahl erspart dem Nutzer das Suchen."""
    found = autodetect(hass)
    assert found[CONF_SLEEP_DURATION] == SLEEP_ENTITY
    assert found[CONF_STEPS] == "sensor.phone_health_steps"
    assert found[CONF_FOCUS] == "binary_sensor.phone_focus"


@pytest.mark.parametrize(
    "eingabe",
    [
        pytest.param({}, id="nichts-angefasst"),
        pytest.param({CONF_SLEEP_NEED: 480, CONF_AUTO_SLEEP_NEED: True}, id="ohne-freie-tage"),
        pytest.param({CONF_FREE_DAYS: ["6"]}, id="nur-sonntag"),
        pytest.param({CONF_FREE_DAYS: []}, id="keine-freien-tage"),
    ],
)
async def test_letzter_schritt_akzeptiert_jede_eingabe(
    hass: HomeAssistant, source_states, eingabe: dict
) -> None:
    """Regression: Der Vorgabewert der freien Tage muss zur Auswahlliste passen.

    Die Auswahlliste arbeitet mit Zeichenketten, der Vorgabewert bestand aber aus
    Ganzzahlen. Schickt die Oberfläche das Feld nicht mit — was sie regelmäßig tut
    —, setzt voluptuous den Vorgabewert ein und validiert ihn: „expected str at
    'free_days'". Die Einrichtung war dadurch praktisch immer blockiert.
    """
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SLEEP_DURATION: SLEEP_ENTITY}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "personal"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], eingabe)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_freie_tage_werden_als_wochentage_gelesen(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    """Die als Zeichenketten gespeicherten Tage müssen als Zahlen ankommen."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    hass.config_entries.async_update_entry(config_entry, options={CONF_FREE_DAYS: ["0", "6"]})
    await hass.async_block_till_done()
    assert config_entry.runtime_data.free_days == frozenset({0, 6})


async def test_optionen_aendern_modellparameter(
    hass: HomeAssistant, source_states, config_entry
) -> None:
    """Der Optionsdialog schreibt in die Optionen, nicht in die Grunddaten."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "model"}
    )
    assert result["step_id"] == "model"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tau_acute": 4.0, "recovery_efficiency": 0.6}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.options["tau_acute"] == 4.0
