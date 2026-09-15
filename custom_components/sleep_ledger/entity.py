"""Gemeinsame Basis aller Entitäten der Integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import SleepLedgerCoordinator


class SleepLedgerEntity(CoordinatorEntity["SleepLedgerCoordinator"]):
    """Basisklasse: ein Gerät je Config-Entry, stabile IDs, übersetzte Namen."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SleepLedgerCoordinator, key: str) -> None:
        """Entität an den Coordinator binden."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="Sleep Ledger",
            manufacturer="Sleep Ledger",
            model="Schlafkonto",
            entry_type=DeviceEntryType.SERVICE,
        )
