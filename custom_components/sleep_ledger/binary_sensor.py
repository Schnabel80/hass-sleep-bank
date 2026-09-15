"""Warnsensoren."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from .const import COVERAGE_WINDOW_DAYS, KEY_DEBT_ALERT, KEY_LOW_CONFIDENCE, MIN_COVERAGE
from .entity import SleepLedgerEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import SleepLedgerConfigEntry
    from .coordinator import SleepLedgerCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SleepLedgerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Warnsensoren anlegen."""
    coordinator = entry.runtime_data
    async_add_entities([DebtAlert(coordinator), LowConfidence(coordinator)])


class DebtAlert(SleepLedgerEntity, BinarySensorEntity):
    """Schlägt an, wenn das chronische Defizit die Schwelle überschreitet."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SleepLedgerCoordinator) -> None:
        """Warnsensor aufsetzen."""
        super().__init__(coordinator, KEY_DEBT_ALERT)

    @property
    def is_on(self) -> bool | None:
        """``None``, solange kein belastbarer chronischer Wert vorliegt."""
        chronic = self.coordinator.data.debt.chronic_min_per_night
        if chronic is None:
            return None
        return chronic > self.coordinator.debt_alert_threshold

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Schwelle und aktueller Wert, damit die Warnung nachvollziehbar ist."""
        chronic = self.coordinator.data.debt.chronic_min_per_night
        return {
            "threshold_min": self.coordinator.debt_alert_threshold,
            "chronic_min_per_night": None if chronic is None else round(chronic),
        }


class LowConfidence(SleepLedgerEntity, BinarySensorEntity):
    """Meldet, wenn die Datenbasis zu dünn für die Langzeitmetriken ist.

    Bewusst eine eigene Entität statt nur eines Attributs: Wer Automationen auf
    das Schlafkonto baut, sollte sie an genau dieser Bedingung aussetzen können.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SleepLedgerCoordinator) -> None:
        """Diagnosesensor aufsetzen."""
        super().__init__(coordinator, KEY_LOW_CONFIDENCE)

    @property
    def is_on(self) -> bool:
        """Wahr bei zu geringer Abdeckung im Auswertungsfenster."""
        return self.coordinator.data.coverage < MIN_COVERAGE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Abdeckung, Fenster und Schwelle."""
        return {
            "coverage": round(self.coordinator.data.coverage, 3),
            "window_days": COVERAGE_WINDOW_DAYS,
            "required_coverage": MIN_COVERAGE,
        }
