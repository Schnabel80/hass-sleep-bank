"""Tests für die Vorauswahl der Quellentitäten.

Die Vorauswahl darf sich **nicht** am Namen orientieren, sondern am Gerät. In
einem Haushalt mit mehreren Telefonen gibt es leicht ein halbes Dutzend
Fokus-Sensoren, und die Entity-IDs desselben Geräts stimmen nicht zwangsläufig
überein: Die Companion-App behält beim Umbenennen eines Telefons die alten IDs
bei, sodass die Schlafdaten unter `..._iphone_17_*` liegen, der Fokus-Sensor
desselben Geräts aber unter `..._iphone_focus`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sleep_bank.config_flow import autodetect
from custom_components.sleep_bank.const import (
    CONF_FOCUS,
    CONF_RESTING_HR,
    CONF_SLEEP_DURATION,
    CONF_STEPS,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _register(hass, entity_registry, device_registry, entry, geraet, eintraege):
    """Ein Gerät samt Entitäten anlegen und Zustände setzen."""
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("mobile_app", geraet)},
        name=geraet,
    )
    for domain, object_id, state in eintraege:
        entity_registry.async_get_or_create(
            domain,
            "mobile_app",
            f"{geraet}-{object_id}",
            suggested_object_id=object_id,
            device_id=device.id,
        )
        hass.states.async_set(f"{domain}.{object_id}", state)
    return device


async def _zwei_telefone(hass, entity_registry, device_registry):
    entry = MockConfigEntry(domain="mobile_app")
    entry.add_to_hass(hass)

    # Alphabetisch zuerst — und das falsche Gerät.
    _register(
        hass,
        entity_registry,
        device_registry,
        entry,
        "bruce_17e",
        [("binary_sensor", "bruce_17e_focus", "off"), ("sensor", "bruce_17e_steps", "10")],
    )
    # Das richtige Gerät. Die Entity-IDs sind bewusst uneinheitlich.
    _register(
        hass,
        entity_registry,
        device_registry,
        entry,
        "christians_iphone_17",
        [
            ("sensor", "christians_iphone_17_sleep_duration", "395"),
            ("sensor", "christians_iphone_17_resting_heart_rate", "66"),
            ("sensor", "christians_iphone_17_health_steps", "1428"),
            ("binary_sensor", "christians_iphone_focus", "off"),
        ],
    )
    await hass.async_block_till_done()


async def test_fokus_kommt_vom_geraet_der_schlafdaten(
    hass: HomeAssistant, entity_registry, device_registry
) -> None:
    """Regression: Alphabetische Auswahl schlug zuverlässig das falsche Gerät vor."""
    await _zwei_telefone(hass, entity_registry, device_registry)

    found = autodetect(hass)

    assert found[CONF_SLEEP_DURATION] == "sensor.christians_iphone_17_sleep_duration"
    assert found[CONF_FOCUS] == "binary_sensor.christians_iphone_focus"
    assert found[CONF_STEPS] == "sensor.christians_iphone_17_health_steps"
    assert found[CONF_RESTING_HR] == "sensor.christians_iphone_17_resting_heart_rate"


async def test_anker_bestimmt_das_geraet(
    hass: HomeAssistant, entity_registry, device_registry
) -> None:
    """Wählt der Nutzer eine andere Schlafquelle, folgt die Vorauswahl ihr."""
    await _zwei_telefone(hass, entity_registry, device_registry)
    entry = MockConfigEntry(domain="mobile_app")
    entry.add_to_hass(hass)
    _register(
        hass,
        entity_registry,
        device_registry,
        entry,
        "anderes_telefon",
        [
            ("sensor", "anderes_telefon_sleep_duration", "420"),
            ("binary_sensor", "anderes_telefon_focus", "off"),
        ],
    )
    await hass.async_block_till_done()

    found = autodetect(hass, "sensor.anderes_telefon_sleep_duration")
    assert found[CONF_FOCUS] == "binary_sensor.anderes_telefon_focus"


async def test_unerreichbare_entitaeten_werden_hintangestellt(
    hass: HomeAssistant, entity_registry, device_registry
) -> None:
    """Ein `unavailable` gemeldeter Sensor ist der schlechtere Vorschlag."""
    entry = MockConfigEntry(domain="mobile_app")
    entry.add_to_hass(hass)
    _register(
        hass,
        entity_registry,
        device_registry,
        entry,
        "telefon",
        [
            ("sensor", "telefon_sleep_duration", "400"),
            ("binary_sensor", "aaa_focus", "unavailable"),
            ("binary_sensor", "zzz_focus", "off"),
        ],
    )
    await hass.async_block_till_done()

    assert autodetect(hass)[CONF_FOCUS] == "binary_sensor.zzz_focus"


async def test_ohne_treffer_keine_vorauswahl(hass: HomeAssistant) -> None:
    assert autodetect(hass) == {}
