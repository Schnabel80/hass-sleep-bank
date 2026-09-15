"""Sensoren der Integration.

Grundsatz für alle Sensoren: Liegt zu wenig Historie vor, ist der Zustand
``unknown`` — nie ein Platzhalterwert. Eine Schlafschuld aus drei Nächten wäre
eine Zahl ohne Aussage, und in einem Diagramm sähe sie genauso echt aus wie eine
belastbare.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime

from .const import (
    COVERAGE_WINDOW_DAYS,
    KEY_CONFIDENCE,
    KEY_COVERAGE,
    KEY_DEBT_ACUTE,
    KEY_DEBT_CHRONIC,
    KEY_ESTIMATED_NEED,
    KEY_LAST_NIGHT_DURATION,
    KEY_MIDPOINT,
    KEY_MIDPOINT_VARIABILITY,
    KEY_NIGHTS_TO_RECOVERY,
    KEY_READINESS,
    KEY_RECOVERY,
    KEY_SOCIAL_JETLAG,
    KEY_SRI,
    TREND_WINDOW_DAYS,
)
from .entity import SleepLedgerEntity

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import SleepLedgerConfigEntry
    from .coordinator import LedgerData, SleepLedgerCoordinator

PARALLEL_UPDATES = 0


def _round(value: float | None, digits: int = 0) -> float | None:
    """Auf ``digits`` Stellen runden; ohne Nachkommastellen als ganze Zahl."""
    if value is None:
        return None
    return round(value) if digits == 0 else round(value, digits)


def _midpoint_text(data: LedgerData) -> str | None:
    minutes = data.regularity.midpoint_minutes
    if minutes is None:
        return None
    total = round(minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _recovery_attributes(data: LedgerData) -> dict[str, Any]:
    """Erholungsszenarien als Attribute — die Zahl allein sagt zu wenig."""
    return {
        f"nights_at_{int(target // 60)}h{int(target % 60):02d}": nights
        for target, nights in sorted(data.nights_to_recovery.items())
    }


def _readiness_attributes(data: LedgerData) -> dict[str, Any]:
    result = data.readiness
    return {
        "is_heuristic": result.is_heuristic,
        "components": {name: round(value, 1) for name, value in result.components.items()},
        "weights": {name: round(value, 3) for name, value in result.weights.items()},
    }


def _last_night_attributes(data: LedgerData) -> dict[str, Any]:
    night = data.last_night
    if night is None:
        return {}
    return {
        "date": night.date.isoformat(),
        "anchor_provenance": str(night.anchor_provenance),
        "onset_time": night.onset_time.isoformat() if night.onset_time else None,
        "wake_time": night.wake_time.isoformat() if night.wake_time else None,
        "interruptions": night.interruptions,
        "efficiency": _round(night.efficiency, 3),
        "nap_min": night.nap_min,
        "has_stages": night.has_stages,
        "source_device": night.source_device,
    }


def _need_attributes(data: LedgerData) -> dict[str, Any]:
    """Wie die Bedarfsschätzung zustande kam — und warum gegebenenfalls nicht.

    Der Grund gehört an den Sensor und nicht ins Protokoll: „alle Nächte enden am
    Wecker" ist für den Nutzer eine Handlungsanweisung, kein Fehler.
    """
    estimate = data.need_estimate
    if estimate is None:
        return {}
    return {
        "method": str(estimate.method),
        "reason": str(estimate.reason),
        "confidence": estimate.confidence,
        "nights_used": estimate.nights_used,
        "runs_used": estimate.runs_used,
        "uncertainty_min": estimate.uncertainty_min,
        "plateau_reached": estimate.plateau_reached,
        "applied_need_min": round(data.need_min),
        "calibration_active": data.calibration_active,
    }


@dataclass(frozen=True, kw_only=True)
class SleepLedgerSensorDescription(SensorEntityDescription):
    """Sensorbeschreibung mit Auswertungsfunktion."""

    value_fn: Callable[[LedgerData], float | str | None]
    attributes_fn: Callable[[LedgerData], dict[str, Any]] | None = None


SENSORS: tuple[SleepLedgerSensorDescription, ...] = (
    SleepLedgerSensorDescription(
        key=KEY_DEBT_ACUTE,
        # Gerechnet wird in Minuten, angezeigt in Stunden: Über eine Woche
        # summiert sich die Schuld auf mehrere Stunden, und „6,8 h" liest sich
        # besser als „408 min". Die Gerätekategorie erlaubt Home Assistant die
        # Umrechnung — auch für negative Werte, wenn ein Guthaben besteht — und
        # lässt den Nutzer die Einheit je Entität selbst wählen.
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sleep",
        value_fn=lambda data: _round(data.debt.acute_min),
        suggested_display_precision=1,
        attributes_fn=lambda data: {
            "nights_used": data.debt.nights_used,
            "sleep_need_min": round(data.need_min),
            "last_night_balance_min": _round(data.debt.last_balance_min),
        },
    ),
    SleepLedgerSensorDescription(
        key=KEY_DEBT_CHRONIC,
        # Hier bleibt es bei Minuten: Ein Defizit *pro Nacht* liegt in der
        # Größenordnung von Minuten, und „48 min pro Nacht" ist greifbarer als
        # „0,8 h pro Nacht". Umschaltbar ist es trotzdem.
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-timeline-variant",
        value_fn=lambda data: _round(data.debt.chronic_min_per_night),
        attributes_fn=lambda data: {
            "stored_nights": data.stored_nights,
            # Bedarfsunabhängig: Ein falsch angenommener Schlafbedarf verschiebt
            # beide Zeitpunkte gleich und fällt in der Differenz heraus.
            "change_vs_4_weeks_min": _round(data.chronic_change_min),
            "trend_window_days": TREND_WINDOW_DAYS,
            "sleep_need_min": round(data.need_min),
        },
    ),
    SleepLedgerSensorDescription(
        key=KEY_NIGHTS_TO_RECOVERY,
        icon="mdi:calendar-clock",
        value_fn=lambda data: data.nights_to_recovery.get(data.need_min),
        attributes_fn=_recovery_attributes,
    ),
    SleepLedgerSensorDescription(
        key=KEY_SRI,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:repeat",
        value_fn=lambda data: _round(data.regularity.sri, 1),
        attributes_fn=lambda data: {"evaluated_day_pairs": data.regularity.sri_pairs},
    ),
    SleepLedgerSensorDescription(
        key=KEY_SOCIAL_JETLAG,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:earth",
        value_fn=lambda data: _round(data.regularity.social_jetlag_hours, 2),
    ),
    SleepLedgerSensorDescription(
        key=KEY_MIDPOINT,
        icon="mdi:clock-outline",
        value_fn=_midpoint_text,
    ),
    SleepLedgerSensorDescription(
        key=KEY_MIDPOINT_VARIABILITY,
        # Eine Streuung von wenigen Dutzend Minuten bleibt in Minuten lesbarer.
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:arrow-expand-horizontal",
        value_fn=lambda data: _round(data.regularity.midpoint_variability_min),
    ),
    SleepLedgerSensorDescription(
        key=KEY_RECOVERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:heart-pulse",
        value_fn=lambda data: _round(data.physiology.recovery_z, 2),
        attributes_fn=lambda data: {
            "resting_hr_z": _round(data.physiology.resting_hr_z, 2),
            "hrv_z": _round(data.physiology.hrv_z, 2),
            "respiratory_rate_z": _round(data.physiology.respiratory_rate_z, 2),
            "baseline_samples": data.physiology.baseline_samples,
        },
    ),
    SleepLedgerSensorDescription(
        key=KEY_READINESS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart-variant",
        value_fn=lambda data: _round(data.readiness.score),
        attributes_fn=_readiness_attributes,
    ),
    SleepLedgerSensorDescription(
        key=KEY_COVERAGE,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:database-check",
        value_fn=lambda data: _round(data.coverage * 100),
        attributes_fn=lambda data: {
            "window_days": COVERAGE_WINDOW_DAYS,
            "stored_nights": data.stored_nights,
        },
    ),
    SleepLedgerSensorDescription(
        key=KEY_CONFIDENCE,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:shield-check",
        value_fn=lambda data: (
            None if data.last_night is None else round(data.last_night.confidence * 100)
        ),
        attributes_fn=_last_night_attributes,
    ),
    SleepLedgerSensorDescription(
        key=KEY_ESTIMATED_NEED,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:target",
        value_fn=lambda data: (
            None if data.need_estimate is None else _round(data.need_estimate.minutes)
        ),
        attributes_fn=_need_attributes,
    ),
    SleepLedgerSensorDescription(
        key=KEY_LAST_NIGHT_DURATION,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:bed-clock",
        value_fn=lambda data: None if data.last_night is None else data.last_night.total_min,
        attributes_fn=_last_night_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SleepLedgerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Sensoren anlegen."""
    coordinator = entry.runtime_data
    async_add_entities(SleepLedgerSensor(coordinator, description) for description in SENSORS)


class SleepLedgerSensor(SleepLedgerEntity, SensorEntity):
    """Ein aus der Nachthistorie berechneter Sensor."""

    entity_description: SleepLedgerSensorDescription

    def __init__(
        self, coordinator: SleepLedgerCoordinator, description: SleepLedgerSensorDescription
    ) -> None:
        """Sensor aus seiner Beschreibung aufbauen."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        """Aktueller Wert, oder ``None``, wenn die Datenlage nicht trägt."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Zusatzinformationen, die den Hauptwert einordnen."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
