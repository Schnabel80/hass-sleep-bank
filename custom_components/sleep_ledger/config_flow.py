"""Einrichtung über die Oberfläche."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers import entity_registry as er
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

#: Der Schlafbedarf wird in Stunden erfasst und in Minuten gespeichert.
MINUTES_PER_HOUR = 60.0

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

#: Zustände, die keinen brauchbaren Wert tragen.
_NO_VALUE = frozenset({"unavailable", "unknown", ""})


def _device_of(hass: HomeAssistant, entity_id: str | None) -> str | None:
    """Geräte-ID einer Entität aus der Registrierung."""
    if not entity_id:
        return None
    entry = er.async_get(hass).async_get(entity_id)
    return entry.device_id if entry else None


def _matching(hass: HomeAssistant, domain: str, suffixes: tuple[str, ...]) -> list[str]:
    """Alle Entitäten einer Domain, deren ID auf einen der Suffixe endet."""
    ids = [state.entity_id for state in hass.states.async_all(domain)]
    for suffix in suffixes:
        if found := [entity_id for entity_id in ids if entity_id.endswith(suffix)]:
            return found
    return []


def _best(hass: HomeAssistant, candidates: list[str], device_id: str | None) -> str | None:
    """Den passendsten Kandidaten wählen.

    Ausschlaggebend ist das **Gerät**, nicht der Name: In einem Haushalt mit
    mehreren Telefonen gibt es leicht ein halbes Dutzend Fokus-Sensoren, und die
    Entity-IDs desselben Geräts stimmen nicht zwangsläufig überein — die
    Companion-App behält beim Umbenennen eines Telefons die alten IDs bei. Wer
    hier alphabetisch sortiert, schlägt zuverlässig das falsche Gerät vor.
    """
    if not candidates:
        return None

    def rank(entity_id: str) -> tuple[bool, bool, str]:
        state = hass.states.get(entity_id)
        unusable = state is None or state.state.lower() in _NO_VALUE
        other_device = bool(device_id) and _device_of(hass, entity_id) != device_id
        return (other_device, unusable, entity_id)

    return min(candidates, key=rank)


def autodetect(hass: HomeAssistant, anchor_entity_id: str | None = None) -> dict[str, str]:
    """Passende Sensoren als Vorschlag suchen, am Gerät ausgerichtet.

    ``anchor_entity_id`` ist die bereits gewählte Schlafdauer-Entität. Ist sie
    bekannt, werden alle weiteren Vorschläge auf deren Gerät bezogen; sonst
    bestimmt die Schlafdauer selbst das Gerät.
    """
    found: dict[str, str] = {}

    anchor = anchor_entity_id or _best(
        hass, _matching(hass, "sensor", AUTODETECT_SUFFIXES[CONF_SLEEP_DURATION]), None
    )
    if anchor:
        found[CONF_SLEEP_DURATION] = anchor
    device_id = _device_of(hass, anchor)

    for key, suffixes in AUTODETECT_SUFFIXES.items():
        if key == CONF_SLEEP_DURATION:
            continue
        if best := _best(hass, _matching(hass, "sensor", suffixes), device_id):
            found[key] = best

    if best := _best(hass, _matching(hass, "binary_sensor", ("_focus",)), device_id):
        found[CONF_FOCUS] = best
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
            # In Stunden, weil man über Schlaf in Stunden spricht. Gespeichert
            # wird in Minuten — der Umrechnung dient `_needs_to_minutes`.
            vol.Required(
                CONF_SLEEP_NEED,
                default=round(
                    defaults.get(CONF_SLEEP_NEED, debt_model.DEFAULT_SLEEP_NEED_MIN)
                    / MINUTES_PER_HOUR,
                    2,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5, max=11, step=0.1, unit_of_measurement="h", mode="slider"
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
        # Ab hier ist die Schlafdauer-Entität bekannt: Fokus-Sensor und
        # Schrittzähler werden auf deren Gerät bezogen vorgeschlagen.
        defaults = autodetect(self.hass, self._data.get(CONF_SLEEP_DURATION))
        return self.async_show_form(step_id="timing", data_schema=timing_schema(defaults))

    async def async_step_personal(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Schritt 3: Schlafbedarf und freie Tage."""
        if user_input is not None:
            self._data.update(_need_to_minutes(_clean(user_input)))
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
        current = self._merged()
        defaults = {
            **autodetect(self.hass, current.get(CONF_SLEEP_DURATION)),
            **current,
        }
        return self.async_show_form(step_id="timing", data_schema=timing_schema(defaults))

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


def _need_to_minutes(user_input: dict[str, Any]) -> dict[str, Any]:
    """Den in Stunden erfassten Schlafbedarf in Minuten umrechnen.

    Das Modell rechnet durchgängig in Minuten; nur die Oberfläche spricht
    Stunden. Die Umrechnung sitzt bewusst an dieser einen Stelle.
    """
    if CONF_SLEEP_NEED not in user_input:
        return user_input
    return {
        **user_input,
        CONF_SLEEP_NEED: round(float(user_input[CONF_SLEEP_NEED]) * MINUTES_PER_HOUR),
    }
