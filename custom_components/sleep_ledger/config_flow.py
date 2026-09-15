"""Einrichtung über die Oberfläche."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers import selector

from . import debt as debt_model
from . import readiness as readiness_model
from .const import (
    CONF_AUTO_SLEEP_NEED,
    CONF_AWAKE,
    CONF_CORE_SLEEP,
    CONF_CREDIT_CAP,
    CONF_DEBT_ALERT_THRESHOLD,
    CONF_DEEP_SLEEP,
    CONF_FOCUS,
    CONF_FREE_DAYS,
    CONF_HRV,
    CONF_ONSET_TIME,
    CONF_RECOVERY_EFFICIENCY,
    CONF_REM_SLEEP,
    CONF_RESPIRATORY_RATE,
    CONF_RESTING_HR,
    CONF_SLEEP_DURATION,
    CONF_SLEEP_NEED,
    CONF_STEPS,
    CONF_TAU_ACUTE,
    CONF_TAU_CHRONIC,
    CONF_WAKE_TIME,
    CONF_WEIGHT_DEBT,
    CONF_WEIGHT_PHYSIOLOGY,
    CONF_WEIGHT_REGULARITY,
    DEFAULT_DEBT_ALERT_THRESHOLD_MIN,
    DEFAULT_FREE_DAYS,
    DOMAIN,
)
from .ingest import read_float

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

TITLE = "Sleep Ledger"

#: Namensbestandteile, an denen die Sensoren der iOS-Companion-App erkannt werden.
#: Reine Bequemlichkeit für die Vorauswahl — die Integration ist nicht an sie gebunden.
AUTODETECT_SUFFIXES: dict[str, tuple[str, ...]] = {
    CONF_SLEEP_DURATION: ("_sleep_duration",),
    CONF_CORE_SLEEP: ("_core_sleep",),
    CONF_DEEP_SLEEP: ("_deep_sleep",),
    CONF_REM_SLEEP: ("_rem_sleep",),
    CONF_AWAKE: ("_awake",),
    CONF_RESTING_HR: ("_resting_heart_rate",),
    CONF_HRV: ("_heart_rate_variability",),
    CONF_RESPIRATORY_RATE: ("_respiratory_rate",),
    CONF_STEPS: ("_health_steps", "_steps"),
}


def autodetect(hass: HomeAssistant) -> dict[str, str]:
    """Passende Sensoren der Companion-App als Vorschlag suchen."""
    found: dict[str, str] = {}
    sensor_ids = [state.entity_id for state in hass.states.async_all("sensor")]
    for key, suffixes in AUTODETECT_SUFFIXES.items():
        for suffix in suffixes:
            matches = [entity_id for entity_id in sensor_ids if entity_id.endswith(suffix)]
            if matches:
                found[key] = sorted(matches)[0]
                break

    focus = [state.entity_id for state in hass.states.async_all("binary_sensor")]
    focus_matches = [entity_id for entity_id in focus if entity_id.endswith("_focus")]
    if focus_matches:
        found[CONF_FOCUS] = sorted(focus_matches)[0]
    return found


def _sensor(*, multiple: bool = False) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["sensor", "input_number"], multiple=multiple)
    )


def _optional(key: str, defaults: dict[str, Any]) -> vol.Marker:
    """Optionales Feld, das einen vorhandenen Wert als Vorbelegung übernimmt."""
    if defaults.get(key):
        return vol.Optional(key, description={"suggested_value": defaults[key]})
    return vol.Optional(key)


def sources_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schritt 1 — Quellentitäten."""
    return vol.Schema(
        {
            vol.Required(
                CONF_SLEEP_DURATION,
                description={"suggested_value": defaults.get(CONF_SLEEP_DURATION)},
            ): _sensor(),
            _optional(CONF_CORE_SLEEP, defaults): _sensor(),
            _optional(CONF_DEEP_SLEEP, defaults): _sensor(),
            _optional(CONF_REM_SLEEP, defaults): _sensor(),
            _optional(CONF_AWAKE, defaults): _sensor(),
            _optional(CONF_RESTING_HR, defaults): _sensor(),
            _optional(CONF_HRV, defaults): _sensor(),
            _optional(CONF_RESPIRATORY_RATE, defaults): _sensor(),
        }
    )


def timing_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schritt 2 — Zeiterfassung.

    Liegen echte Zeitstempel vor, werden sie bevorzugt. Sonst dienen Fokus-Sensor
    und Schrittzähler der Ankerbestimmung am Morgen; die Einschlafzeit wird aus
    der gemessenen Schlafdauer zurückgerechnet.
    """
    time_entities = selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["sensor", "input_datetime"])
    )
    return vol.Schema(
        {
            _optional(CONF_WAKE_TIME, defaults): time_entities,
            _optional(CONF_ONSET_TIME, defaults): time_entities,
            _optional(CONF_FOCUS, defaults): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean"])
            ),
            _optional(CONF_STEPS, defaults): _sensor(),
        }
    )


def personal_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schritt 3 — persönliche Parameter."""
    return vol.Schema(
        {
            vol.Required(
                CONF_SLEEP_NEED,
                default=defaults.get(CONF_SLEEP_NEED, debt_model.DEFAULT_SLEEP_NEED_MIN),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=300, max=660, step=5, unit_of_measurement="min", mode="slider"
                )
            ),
            vol.Optional(
                CONF_AUTO_SLEEP_NEED, default=defaults.get(CONF_AUTO_SLEEP_NEED, False)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_FREE_DAYS, default=defaults.get(CONF_FREE_DAYS, DEFAULT_FREE_DAYS)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                    # Die Wochentagsnamen kommen aus den Übersetzungen unter
                    # `selector.weekdays.options` und stehen nicht im Code.
                    translation_key="weekdays",
                    options=[str(index) for index in range(7)],
                )
            ),
        }
    )


class SleepLedgerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Mehrstufige Ersteinrichtung."""

    VERSION = 1

    def __init__(self) -> None:
        """Zwischenspeicher für die Schritte anlegen."""
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Schritt 1: Quellentitäten wählen."""
        errors: dict[str, str] = {}
        if user_input is not None:
            duration = user_input.get(CONF_SLEEP_DURATION)
            if read_float(self.hass, duration) is None:
                # Verbindungstest-Äquivalent: Ohne auswertbare Schlafdauer hat die
                # Integration keine Grundlage, und das merkt man besser sofort.
                errors[CONF_SLEEP_DURATION] = "no_numeric_state"
            else:
                await self.async_set_unique_id(str(duration))
                self._abort_if_unique_id_configured()
                self._data.update(_clean(user_input))
                return await self.async_step_timing()

        defaults = {**autodetect(self.hass), **(user_input or {})}
        return self.async_show_form(
            step_id="user", data_schema=sources_schema(defaults), errors=errors
        )

    async def async_step_timing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Schritt 2: Zeiterfassung."""
        if user_input is not None:
            self._data.update(_clean(user_input))
            return await self.async_step_personal()
        return self.async_show_form(
            step_id="timing", data_schema=timing_schema(autodetect(self.hass))
        )

    async def async_step_personal(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Schritt 3: Schlafbedarf und freie Tage."""
        if user_input is not None:
            self._data.update(_clean(user_input))
            return self.async_create_entry(title=TITLE, data=self._data)
        return self.async_show_form(step_id="personal", data_schema=personal_schema({}))

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> SleepLedgerOptionsFlow:
        """Optionsdialog bereitstellen."""
        return SleepLedgerOptionsFlow()


class SleepLedgerOptionsFlow(OptionsFlow):
    """Nachträgliche Änderung aller Parameter."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Menü aus Quellen, Zeiterfassung und Modellparametern."""
        if user_input is not None:
            return await self.async_step_sources()
        return self.async_show_menu(step_id="init", menu_options=["sources", "timing", "model"])

    def _merged(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    def _save(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **_clean(user_input)})

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Quellentitäten ändern."""
        if user_input is not None:
            return self._save(user_input)
        return self.async_show_form(step_id="sources", data_schema=sources_schema(self._merged()))

    async def async_step_timing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Zeiterfassung ändern."""
        if user_input is not None:
            return self._save(user_input)
        return self.async_show_form(step_id="timing", data_schema=timing_schema(self._merged()))

    async def async_step_model(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Modellparameter ändern."""
        if user_input is not None:
            return self._save(user_input)
        current = self._merged()
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TAU_ACUTE,
                    default=current.get(CONF_TAU_ACUTE, debt_model.DEFAULT_TAU_ACUTE_NIGHTS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=10, step=0.5, mode="box")
                ),
                vol.Optional(
                    CONF_TAU_CHRONIC,
                    default=current.get(CONF_TAU_CHRONIC, debt_model.DEFAULT_TAU_CHRONIC_NIGHTS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=7, max=60, step=1, mode="box")
                ),
                vol.Optional(
                    CONF_RECOVERY_EFFICIENCY,
                    default=current.get(
                        CONF_RECOVERY_EFFICIENCY, debt_model.DEFAULT_RECOVERY_EFFICIENCY
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.1, max=1.0, step=0.05, mode="slider")
                ),
                vol.Optional(
                    CONF_CREDIT_CAP,
                    default=current.get(CONF_CREDIT_CAP, debt_model.DEFAULT_CREDIT_CAP_MIN),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=480, step=15, unit_of_measurement="min", mode="box"
                    )
                ),
                vol.Optional(
                    CONF_DEBT_ALERT_THRESHOLD,
                    default=current.get(
                        CONF_DEBT_ALERT_THRESHOLD, DEFAULT_DEBT_ALERT_THRESHOLD_MIN
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=180, step=5, unit_of_measurement="min", mode="box"
                    )
                ),
                **{
                    vol.Optional(
                        key, default=current.get(key, readiness_model.DEFAULT_WEIGHTS[name])
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=0, max=1, step=0.05, mode="slider")
                    )
                    for key, name in (
                        (CONF_WEIGHT_DEBT, "debt"),
                        (CONF_WEIGHT_PHYSIOLOGY, "physiology"),
                        (CONF_WEIGHT_REGULARITY, "regularity"),
                    )
                },
            }
        )
        return self.async_show_form(step_id="model", data_schema=schema)


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    """Leere optionale Felder verwerfen, damit sie nicht als ``None`` landen."""
    return {key: value for key, value in user_input.items() if value not in (None, "", [])}
