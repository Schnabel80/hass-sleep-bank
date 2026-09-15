"""Orchestrierung: Nächte erfassen, Kennzahlen berechnen, Entitäten versorgen."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date as date_type
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import debt as debt_model
from . import physiology as physiology_model
from . import readiness as readiness_model
from . import regularity as regularity_model
from . import sleep_need as need_model
from .const import (
    CONF_AUTO_SLEEP_NEED,
    CONF_CREDIT_CAP,
    CONF_DEBT_ALERT_THRESHOLD,
    CONF_FREE_DAYS,
    CONF_NEED_ADAPTED_ON,
    CONF_RECOVERY_EFFICIENCY,
    CONF_SLEEP_DURATION,
    CONF_SLEEP_NEED,
    CONF_TAU_ACUTE,
    CONF_TAU_CHRONIC,
    CONF_WEIGHT_DEBT,
    CONF_WEIGHT_PHYSIOLOGY,
    CONF_WEIGHT_REGULARITY,
    COVERAGE_WINDOW_DAYS,
    DAILY_EVALUATION_HOUR,
    DAILY_EVALUATION_MINUTE,
    DEFAULT_DEBT_ALERT_THRESHOLD_MIN,
    DEFAULT_FREE_DAYS,
    DOMAIN,
    MIN_COVERAGE,
    RECOVERY_SCENARIOS,
    STORAGE_META_CALIBRATION_END,
    STORAGE_META_CALIBRATION_START,
    TREND_WINDOW_DAYS,
    UPDATE_INTERVAL_HOURS,
)
from .ingest import (
    async_build_night,
    async_seed_from_statistics,
    is_night_arrival,
    read_float,
)
from .models import SleepNight, coverage
from .store import NightStore
from .timing import MORNING_END_HOUR

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, EventStateChangedData, HomeAssistant

_LOGGER = logging.getLogger(__name__)

#: Wie weit das Kaltstart-Seeding in die Langzeitstatistik zurückgreift.
SEED_LOOKBACK_DAYS = 90


@dataclass(slots=True)
class SleepBankData:
    """Alles, was die Entitäten anzeigen. Ein Schnappschuss je Berechnung."""

    debt: debt_model.DebtResult
    regularity: regularity_model.RegularityResult
    physiology: physiology_model.PhysiologyResult
    readiness: readiness_model.ReadinessResult
    coverage: float
    nights_to_recovery: dict[float, int | None] = field(default_factory=dict)
    last_night: SleepNight | None = None
    need_min: float = debt_model.DEFAULT_SLEEP_NEED_MIN
    need_estimate: need_model.NeedEstimate | None = None
    chronic_change_min: float | None = None
    """Veränderung des chronischen Defizits gegenüber vier Wochen zuvor.

    Bewusst mitgeführt, weil dieser Wert **bedarfsunabhängig** ist: Eine
    Fehlannahme beim Schlafbedarf verschiebt beide Zeitpunkte um denselben
    Betrag und fällt in der Differenz heraus. Er bleibt also auch dann gültig,
    wenn der absolute Wert es nicht ist.
    """

    calibration_active: bool = False
    stored_nights: int = 0


class SleepBankCoordinator(DataUpdateCoordinator[SleepBankData]):
    """Hält die Nachthistorie und berechnet daraus alle Kennzahlen.

    Die Berechnung ist rein lokal — es gibt kein Gerät und keinen Dienst
    abzufragen. Der Turnus sorgt lediglich dafür, dass das Konto über den Tag
    korrekt altert; die eigentliche Auswertung läuft ereignisgesteuert, sobald
    die Nachtdaten eintreffen.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Coordinator für einen Config-Entry aufsetzen."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
            config_entry=entry,
        )
        self.entry = entry
        self.store = NightStore(hass, entry.entry_id)
        #: Schnappschuss der zuletzt angewandten Optionen. Erlaubt dem
        #: Update-Listener zu unterscheiden, ob ein Neuladen nötig ist.
        self.applied_options: dict[str, Any] = dict(entry.options)

    # -- Konfiguration ------------------------------------------------------------

    @property
    def config(self) -> dict[str, Any]:
        """Zusammengeführte Konfiguration; Optionen überschreiben die Ersteinrichtung."""
        return {**self.entry.data, **self.entry.options}

    def _option(self, key: str, default: float) -> float:
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    @property
    def free_days(self) -> frozenset[int]:
        """Wochentage, die als frei gelten (0 = Montag)."""
        raw = self.config.get(CONF_FREE_DAYS, DEFAULT_FREE_DAYS)
        try:
            return frozenset(int(day) for day in raw)
        except (TypeError, ValueError):
            # Auch der Rückfallweg muss Zahlen liefern: Die Vorgabe liegt als
            # Zeichenketten vor, weil die Auswahlliste im Config-Flow damit
            # arbeitet — das Modell rechnet dagegen mit Wochentagsindizes.
            return frozenset(int(day) for day in DEFAULT_FREE_DAYS)

    @property
    def debt_alert_threshold(self) -> float:
        """Chronisches Defizit pro Nacht, ab dem gewarnt wird."""
        return self._option(CONF_DEBT_ALERT_THRESHOLD, DEFAULT_DEBT_ALERT_THRESHOLD_MIN)

    # -- Schlafbedarf -------------------------------------------------------------

    @property
    def calibration_window(self) -> frozenset[date_type]:
        """Alle Daten des laufenden oder zuletzt abgeschlossenen Kalibrierlaufs."""
        meta = self.store.meta
        raw_start = meta.get(STORAGE_META_CALIBRATION_START)
        if not raw_start:
            return frozenset()
        try:
            start = date_type.fromisoformat(raw_start)
        except ValueError:
            return frozenset()
        raw_end = meta.get(STORAGE_META_CALIBRATION_END)
        try:
            end = date_type.fromisoformat(raw_end) if raw_end else dt_util.now().date()
        except ValueError:
            end = dt_util.now().date()
        if end < start:
            return frozenset()
        return frozenset(start + timedelta(days=i) for i in range((end - start).days + 1))

    @property
    def calibration_active(self) -> bool:
        """Ob gerade ein Kalibrierlauf läuft."""
        meta = self.store.meta
        return bool(meta.get(STORAGE_META_CALIBRATION_START)) and not meta.get(
            STORAGE_META_CALIBRATION_END
        )

    async def async_set_calibration(self, active: bool) -> None:
        """Kalibrierlauf starten oder beenden."""
        today = dt_util.now().date().isoformat()
        if active:
            await self.store.async_set_meta(
                **{
                    STORAGE_META_CALIBRATION_START: today,
                    STORAGE_META_CALIBRATION_END: None,
                }
            )
            _LOGGER.info("Kalibrierlauf gestartet — bitte ohne Wecker schlafen")
        else:
            if not self.store.meta.get(STORAGE_META_CALIBRATION_START):
                return
            await self.store.async_set_meta(**{STORAGE_META_CALIBRATION_END: today})
            _LOGGER.info("Kalibrierlauf beendet")
        await self.async_refresh()

    def estimate_need(self, nights: list[SleepNight]) -> need_model.NeedEstimate:
        """Aktuelle Bedarfsschätzung aus der Historie."""
        return need_model.estimate(
            nights,
            free_days=self.free_days,
            calibration_window=self.calibration_window,
        )

    async def _async_apply_need(self, estimate: need_model.NeedEstimate) -> float:
        """Angewandten Bedarf bestimmen und bei Bedarf langsam nachziehen.

        Die Schätzung wird nie sprunghaft übernommen. Ein Sprung von 30 Minuten
        sähe im Verlauf des Kontos aus wie eine plötzliche Schlafkrise, obwohl
        sich nur der Nenner geändert hat — und machte den Verlauf über Jahre
        unvergleichbar.
        """
        applied = self._option(CONF_SLEEP_NEED, debt_model.DEFAULT_SLEEP_NEED_MIN)
        if not self.config.get(CONF_AUTO_SLEEP_NEED) or not estimate.is_usable:
            return applied

        today = dt_util.now().date()
        raw_last = self.config.get(CONF_NEED_ADAPTED_ON)
        try:
            last = date_type.fromisoformat(raw_last) if raw_last else None
        except ValueError:
            last = None
        elapsed = (today - last).days if last is not None else 1.0
        if elapsed <= 0:
            return applied

        adapted = need_model.adapt(applied, estimate, elapsed_days=elapsed)
        if abs(adapted - applied) < 1.0:
            return applied

        _LOGGER.info(
            "Schlafbedarf von %.0f auf %.0f min nachgezogen (Schätzung %.0f, Verfahren %s)",
            applied,
            adapted,
            estimate.minutes,
            estimate.method,
        )
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                CONF_SLEEP_NEED: round(adapted, 1),
                CONF_NEED_ADAPTED_ON: today.isoformat(),
            },
        )
        return adapted

    # -- Lebenszyklus -------------------------------------------------------------

    async def async_initialize(self) -> None:
        """Speicher laden, ggf. aus der Statistik vorbefüllen, Listener anhängen."""
        await self.store.async_load()
        await self._async_seed_if_empty()
        await self._async_catch_up()

        duration_entity = self.config.get(CONF_SLEEP_DURATION)
        if duration_entity:
            self.entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, [duration_entity], self._handle_sleep_update
                )
            )

        # Auffangnetz: Falls der Zustandswechsel verpasst wurde — etwa weil HA
        # beim Eintreffen der Daten neu startete — wird die Nacht mittags erneut
        # ausgewertet.
        self.entry.async_on_unload(
            async_track_time_change(
                self.hass,
                self._handle_daily_evaluation,
                hour=DAILY_EVALUATION_HOUR,
                minute=DAILY_EVALUATION_MINUTE,
                second=0,
            )
        )

    async def _async_seed_if_empty(self) -> None:
        """Beim ersten Start die Langzeitstatistik als Startkapital nutzen."""
        if self.store.nights:
            return
        duration_entity = self.config.get(CONF_SLEEP_DURATION)
        if not duration_entity:
            return
        seeded = await async_seed_from_statistics(
            self.hass, duration_entity, days=SEED_LOOKBACK_DAYS
        )
        if seeded:
            added = await self.store.async_put_many(seeded)
            _LOGGER.info(
                "Kaltstart: %d Nächte aus der Langzeitstatistik übernommen "
                "(als Schätzung markiert)",
                added,
            )

    # -- Erfassung ----------------------------------------------------------------

    @callback
    def _handle_sleep_update(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return
        # Ein `unavailable`-Blip beim App-Neustart ist kein neuer Messwert.
        if old_state is not None and old_state.state == new_state.state:
            return
        self.hass.async_create_task(self._async_evaluate(dt_util.as_local(new_state.last_changed)))

    @callback
    def _handle_daily_evaluation(self, now: Any) -> None:
        self.hass.async_create_task(self._async_evaluate(dt_util.as_local(now)))

    async def _async_evaluate(self, arrival: Any) -> None:
        """Eintreffende Schlafdaten einordnen: Nacht oder Nickerchen."""
        if is_night_arrival(arrival):
            await self._async_record_night(arrival)
        else:
            await self._async_record_nap(arrival)
        await self.async_refresh()

    async def _async_record_night(self, arrival: Any) -> None:
        night = await async_build_night(
            self.hass, self.config, day=arrival.date(), arrival=arrival
        )
        if night is None:
            _LOGGER.debug("Keine Schlafdauer vorhanden — keine Nacht gespeichert")
            return

        existing = self.store.get(night.date)
        if existing is not None:
            # Ein Nickerchen, das vorher schon erfasst wurde, bleibt erhalten.
            night = replace(night, nap_min=existing.nap_min)
            if existing.confidence > night.confidence and existing.has_timing:
                _LOGGER.debug("Bestehende Nacht %s hat höhere Konfidenz — behalten", night.date)
                return
        await self.store.async_put(night)
        _LOGGER.debug(
            "Nacht %s gespeichert: %s min, Anker %s (Konfidenz %.2f)",
            night.date,
            night.total_min,
            night.anchor_provenance,
            night.confidence,
        )

    async def _async_record_nap(self, arrival: Any) -> None:
        """Datenzugang außerhalb des Morgenfensters als Nickerchen verbuchen.

        Offener Validierungspunkt: Ob die Quelle Nickerchen überhaupt in die
        Tagessumme einrechnet, ist geräteabhängig. Deshalb wird nur der Zuwachs
        über den bereits gespeicherten Nachtwert als Nickerchen gewertet — steigt
        nichts, passiert nichts.
        """
        current = read_float(self.hass, self.config.get(CONF_SLEEP_DURATION))
        stored = self.store.get(arrival.date())
        if current is None or stored is None or stored.total_min is None:
            return
        gain = current - stored.total_min - (stored.nap_min or 0.0)
        if gain <= 0:
            return
        await self.store.async_put(replace(stored, nap_min=(stored.nap_min or 0.0) + gain))
        _LOGGER.debug("Nickerchen von %.0f min am %s verbucht", gain, arrival.date())

    async def _async_catch_up(self) -> None:
        """Nach einem Neustart prüfen, ob die heutige Nacht noch fehlt.

        Erst *nach* dem Morgenfenster, nie davor: In den Stunden nach Mitternacht
        trägt der Quellsensor noch den Wert der vorigen Nacht. Ihn dort als Nacht
        des neuen Datums zu verbuchen, würde eine Nacht verdoppeln.
        """
        now = dt_util.now()
        if now.hour < MORNING_END_HOUR:
            return
        if self.store.get(now.date()) is None:
            await self._async_record_night(now)

    async def async_recalculate(self) -> None:
        """Nachthistorie vollständig neu bewerten (Button-Aktion)."""
        await self._async_catch_up()
        await self.async_refresh()

    # -- Berechnung ---------------------------------------------------------------

    async def _async_update_data(self) -> SleepBankData:
        """Alle Kennzahlen aus der gespeicherten Historie berechnen."""
        nights = self.store.nights  # nach Datum sortiert
        today = dt_util.now().date()
        need_estimate = self.estimate_need(nights)
        need = await self._async_apply_need(need_estimate)

        debt_result = debt_model.compute(
            nights,
            today=today,
            need_min=need,
            tau_acute=self._option(CONF_TAU_ACUTE, debt_model.DEFAULT_TAU_ACUTE_NIGHTS),
            tau_chronic=self._option(CONF_TAU_CHRONIC, debt_model.DEFAULT_TAU_CHRONIC_NIGHTS),
            recovery_efficiency=self._option(
                CONF_RECOVERY_EFFICIENCY, debt_model.DEFAULT_RECOVERY_EFFICIENCY
            ),
            credit_cap_min=self._option(CONF_CREDIT_CAP, debt_model.DEFAULT_CREDIT_CAP_MIN),
        )
        regularity_result = regularity_model.compute(nights, today=today, free_days=self.free_days)
        physiology_result = physiology_model.compute(nights, today=today)
        data_coverage = coverage(nights, COVERAGE_WINDOW_DAYS, today)

        readiness_result = readiness_model.compute(
            acute_debt_min=debt_result.acute_min,
            recovery_z=physiology_result.recovery_z,
            sri=regularity_result.sri,
            weights={
                "debt": self._option(CONF_WEIGHT_DEBT, readiness_model.DEFAULT_WEIGHTS["debt"]),
                "physiology": self._option(
                    CONF_WEIGHT_PHYSIOLOGY, readiness_model.DEFAULT_WEIGHTS["physiology"]
                ),
                "regularity": self._option(
                    CONF_WEIGHT_REGULARITY, readiness_model.DEFAULT_WEIGHTS["regularity"]
                ),
            },
        )
        if data_coverage < MIN_COVERAGE:
            # Der Bereitschaftsindex fasst Fensterkennzahlen zusammen. Trägt das
            # Fenster nicht, darf auch die Zusammenfassung keine Zahl zeigen —
            # sonst stünde dort eine selbstbewusste 100, während der
            # Datenlage-Warnsensor gleichzeitig anschlägt.
            readiness_result = readiness_model.ReadinessResult(score=None)

        # Bedarfsunabhängige Veränderung: derselbe Rechenweg vier Wochen zuvor.
        past = today - timedelta(days=TREND_WINDOW_DAYS)
        previous = debt_model.compute(
            [n for n in nights if n.date <= past], today=past, need_min=need
        )
        change = (
            debt_result.chronic_min_per_night - previous.chronic_min_per_night
            if debt_result.chronic_min_per_night is not None
            and previous.chronic_min_per_night is not None
            else None
        )

        scenarios: dict[float, int | None] = {}
        if debt_result.acute_min is not None:
            for target in RECOVERY_SCENARIOS:
                scenarios[target] = debt_model.nights_to_recovery(
                    debt_result.acute_min, target_sleep_min=target, need_min=need
                )

        return SleepBankData(
            debt=debt_result,
            regularity=regularity_result,
            physiology=physiology_result,
            readiness=readiness_result,
            coverage=data_coverage,
            nights_to_recovery=scenarios,
            last_night=nights[-1] if nights else None,
            need_min=need,
            need_estimate=need_estimate,
            chronic_change_min=change,
            calibration_active=self.calibration_active,
            stored_nights=len(nights),
        )
