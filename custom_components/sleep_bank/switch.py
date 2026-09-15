"""Kalibrierlauf zur Bestimmung des Schlafbedarfs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from . import sleep_need as need_model
from .const import KEY_CALIBRATION
from .entity import SleepBankEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import SleepBankConfigEntry
    from .coordinator import SleepBankCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SleepBankConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Kalibrierschalter anlegen."""
    async_add_entities([CalibrationSwitch(entry.runtime_data)])


class CalibrationSwitch(SleepBankEntity, SwitchEntity):
    """Markiert einen Zeitraum ohne Wecker zur Bedarfsbestimmung.

    Der Nutzer schaltet ein, wenn eine Phase ohne Wecker beginnt — Urlaub, freie
    Woche —, und wieder aus, wenn sie endet. Über genau diese Nächte wird die
    Abklingkurve gelegt und ihre Asymptote als Schlafbedarf genommen. Das ist die
    Heimnachbildung des publizierten Laborprotokolls und für Menschen, bei denen
    der Wecker jeden Morgen klingelt, der **einzige** belastbare Weg zu einem
    individuellen Bedarf.

    Der Schalter lässt sich automatisieren, etwa über einen Urlaubskalender.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:target-variant"

    def __init__(self, coordinator: SleepBankCoordinator) -> None:
        """Schalter aufsetzen."""
        super().__init__(coordinator, KEY_CALIBRATION)

    @property
    def is_on(self) -> bool:
        """Ob gerade ein Kalibrierlauf läuft."""
        return self.coordinator.data.calibration_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Fortschritt und Ergebnis des Laufs."""
        window = sorted(self.coordinator.calibration_window)
        estimate = self.coordinator.data.need_estimate
        attributes: dict[str, Any] = {
            "nights_in_window": len(window),
            "required_nights": need_model.MIN_CALIBRATION_NIGHTS,
            "recommended_nights": need_model.PLATEAU_NIGHTS + 2,
        }
        if window:
            attributes["start"] = window[0].isoformat()
            attributes["end"] = window[-1].isoformat()
        if estimate is not None and estimate.method is need_model.NeedMethod.CALIBRATION:
            attributes["result_min"] = estimate.minutes
            attributes["plateau_reached"] = estimate.plateau_reached
        return attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Kalibrierlauf beginnen."""
        await self.coordinator.async_set_calibration(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Kalibrierlauf beenden."""
        await self.coordinator.async_set_calibration(False)
