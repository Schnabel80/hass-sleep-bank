"""Sleep Bank — Langzeitauswertung von Schlafdaten in Home Assistant."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import (
    CONF_CREDIT_CAP,
    CONF_RECOVERY_EFFICIENCY,
    CONF_SLEEP_NEED,
    CONF_TAU_ACUTE,
    CONF_TAU_CHRONIC,
    CONF_WEIGHT_DEBT,
    CONF_WEIGHT_PHYSIOLOGY,
    CONF_WEIGHT_REGULARITY,
    PLATFORMS,
)
from .coordinator import SleepBankCoordinator
from .store import NightStore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

type SleepBankConfigEntry = ConfigEntry[SleepBankCoordinator]

#: Optionen, die sich zur Laufzeit ändern lassen, ohne die Integration neu zu laden.
#: Alles andere — vor allem die Quellentitäten — erzwingt einen Neustart des Eintrags.
RUNTIME_OPTIONS = frozenset(
    {
        CONF_SLEEP_NEED,
        CONF_TAU_ACUTE,
        CONF_TAU_CHRONIC,
        CONF_RECOVERY_EFFICIENCY,
        CONF_CREDIT_CAP,
        CONF_WEIGHT_DEBT,
        CONF_WEIGHT_PHYSIOLOGY,
        CONF_WEIGHT_REGULARITY,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: SleepBankConfigEntry) -> bool:
    """Config-Entry einrichten."""
    coordinator = SleepBankCoordinator(hass, entry)
    await coordinator.async_initialize()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: SleepBankConfigEntry) -> None:
    """Auf Optionsänderungen reagieren.

    Reine Modellparameter werden im laufenden Betrieb übernommen — ein Neuladen
    würde alle Entitäten kurz auf ``unavailable`` setzen, nur weil jemand am
    Schlafbedarf gedreht hat. Änderungen an den Quellentitäten erfordern dagegen
    einen Neustart des Eintrags, weil die Zustands-Listener neu gesetzt werden.
    """
    coordinator = entry.runtime_data
    previous = coordinator.applied_options
    current = dict(entry.options)
    changed = {
        key for key in previous.keys() | current.keys() if previous.get(key) != current.get(key)
    }
    coordinator.applied_options = current

    if changed - RUNTIME_OPTIONS:
        # Quellentitäten haben sich geändert — die Zustands-Listener müssen neu gesetzt werden.
        await hass.config_entries.async_reload(entry.entry_id)
        return
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: SleepBankConfigEntry) -> bool:
    """Config-Entry entladen."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SleepBankConfigEntry) -> None:
    """Beim Entfernen der Integration die gespeicherte Nachthistorie löschen."""
    await NightStore(hass, entry.entry_id).async_remove()
