"""Zur Laufzeit einstellbare Modellparameter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTime

from . import debt as debt_model
from . import readiness as readiness_model
from .const import (
    CONF_DEBT_ALERT_THRESHOLD,
    CONF_SLEEP_NEED,
    CONF_WEIGHT_DEBT,
    CONF_WEIGHT_PHYSIOLOGY,
    CONF_WEIGHT_REGULARITY,
    DEFAULT_DEBT_ALERT_THRESHOLD_MIN,
)
from .entity import SleepLedgerEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import SleepLedgerConfigEntry
    from .coordinator import SleepLedgerCoordinator

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SleepLedgerNumberDescription(NumberEntityDescription):
    """Beschreibung eines einstellbaren Parameters."""

    option_key: str
    default: float
    """Vorgabewert in der **gespeicherten** Einheit."""

    stored_per_native: float = 1.0
    """Umrechnung von der angezeigten in die gespeicherte Einheit.

    Gerechnet und gespeichert wird durchgängig in Minuten — das ist die Einheit
    des Modells. Angezeigt wird der Schlafbedarf dagegen in Stunden, weil man
    über Schlaf in Stunden spricht. Dieser Faktor hält beides auseinander, statt
    die Einheit im Modell zu wechseln.
    """


NUMBERS: tuple[SleepLedgerNumberDescription, ...] = (
    SleepLedgerNumberDescription(
        key=CONF_SLEEP_NEED,
        option_key=CONF_SLEEP_NEED,
        default=debt_model.DEFAULT_SLEEP_NEED_MIN,
        stored_per_native=60.0,
        device_class=NumberDeviceClass.DURATION,
        native_min_value=5.0,
        native_max_value=11.0,
        # Sechs-Minuten-Schritte. Feiner wäre Scheingenauigkeit: Der individuelle
        # Bedarf ist ohnehin auf etwa eine Stunde genau bekannt.
        native_step=0.1,
        native_unit_of_measurement=UnitOfTime.HOURS,
        mode=NumberMode.SLIDER,
        icon="mdi:target",
    ),
    SleepLedgerNumberDescription(
        key=CONF_DEBT_ALERT_THRESHOLD,
        option_key=CONF_DEBT_ALERT_THRESHOLD,
        default=DEFAULT_DEBT_ALERT_THRESHOLD_MIN,
        native_min_value=0,
        native_max_value=180,
        native_step=5,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:alert-outline",
    ),
    *(
        SleepLedgerNumberDescription(
            key=key,
            option_key=key,
            default=readiness_model.DEFAULT_WEIGHTS[name],
            native_min_value=0,
            native_max_value=1,
            native_step=0.05,
            mode=NumberMode.SLIDER,
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=False,
            icon="mdi:scale-balance",
        )
        for key, name in (
            (CONF_WEIGHT_DEBT, "debt"),
            (CONF_WEIGHT_PHYSIOLOGY, "physiology"),
            (CONF_WEIGHT_REGULARITY, "regularity"),
        )
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SleepLedgerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Parameter-Entitäten anlegen."""
    coordinator = entry.runtime_data
    async_add_entities(SleepLedgerNumber(coordinator, description) for description in NUMBERS)


class SleepLedgerNumber(SleepLedgerEntity, NumberEntity):
    """Ein Modellparameter, der in den Optionen des Config-Entries lebt."""

    entity_description: SleepLedgerNumberDescription

    def __init__(
        self, coordinator: SleepLedgerCoordinator, description: SleepLedgerNumberDescription
    ) -> None:
        """Parameter aus seiner Beschreibung aufbauen."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float:
        """Aktuell wirksamer Wert, in der Anzeigeeinheit."""
        raw = self.coordinator.config.get(
            self.entity_description.option_key, self.entity_description.default
        )
        try:
            stored = float(raw)
        except (TypeError, ValueError):
            stored = self.entity_description.default
        return round(stored / self.entity_description.stored_per_native, 4)

    async def async_set_native_value(self, value: float) -> None:
        """Wert in die Optionen schreiben; der Update-Listener rechnet neu."""
        stored = round(value * self.entity_description.stored_per_native, 4)
        options = {**self.coordinator.entry.options, self.entity_description.option_key: stored}
        self.hass.config_entries.async_update_entry(self.coordinator.entry, options=options)
