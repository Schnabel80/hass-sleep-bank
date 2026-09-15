"""Aktionsschaltflächen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import SleepBankEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import SleepBankConfigEntry
    from .coordinator import SleepBankCoordinator

PARALLEL_UPDATES = 0

KEY_RECALCULATE = "recalculate"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SleepBankConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Schaltflächen anlegen."""
    async_add_entities([RecalculateButton(entry.runtime_data)])


class RecalculateButton(SleepBankEntity, ButtonEntity):
    """Wertet die Historie neu aus — etwa nach geänderten Quellentitäten."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calculator-variant"

    def __init__(self, coordinator: SleepBankCoordinator) -> None:
        """Schaltfläche aufsetzen."""
        super().__init__(coordinator, KEY_RECALCULATE)

    async def async_press(self) -> None:
        """Neuberechnung anstoßen."""
        await self.coordinator.async_recalculate()
