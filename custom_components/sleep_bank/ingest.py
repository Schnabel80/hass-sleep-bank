"""Quellentitäten und Recorder-Verläufe in :class:`SleepNight` überführen.

Diese Schicht kennt Home Assistant, aber keine Modellmathematik. Sie normalisiert
alles auf das gemeinsame Datenmodell — deshalb kann ein späterer Webhook-Pfad
hier andocken, ohne dass die Auswertung davon erfährt.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.components.recorder import get_instance, history, statistics
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AWAKE,
    CONF_CORE_SLEEP,
    CONF_DEEP_SLEEP,
    CONF_FOCUS,
    CONF_HRV,
    CONF_ONSET_TIME,
    CONF_REM_SLEEP,
    CONF_RESPIRATORY_RATE,
    CONF_RESTING_HR,
    CONF_SLEEP_DURATION,
    CONF_STEPS,
    CONF_WAKE_TIME,
    HISTORY_LOOKBACK_HOURS,
)
from .models import Provenance, SleepNight
from .timing import (
    IMPLAUSIBLE_ONSET_PENALTY,
    MORNING_END_HOUR,
    MORNING_START_HOUR,
    derive_onset,
    determine_anchor,
    focus_phases,
    onset_is_plausible,
)

if TYPE_CHECKING:
    from datetime import date

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

#: Zustände, die keinen Messwert tragen.
_NO_VALUE = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN, "", "none"})

#: Zuordnung Config-Key -> Feld in :class:`SleepNight` für einfache Zahlwerte.
NUMERIC_FIELDS: dict[str, str] = {
    CONF_SLEEP_DURATION: "total_min",
    CONF_CORE_SLEEP: "core_min",
    CONF_DEEP_SLEEP: "deep_min",
    CONF_REM_SLEEP: "rem_min",
    CONF_AWAKE: "awake_min",
    CONF_RESTING_HR: "resting_hr",
    CONF_HRV: "hrv_ms",
    CONF_RESPIRATORY_RATE: "respiratory_rate",
}


def read_float(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Numerischen Zustand einer Entität lesen, oder ``None``."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state.lower() in _NO_VALUE:
        return None
    try:
        return float(state.state)
    except ValueError:
        _LOGGER.debug("Kein numerischer Zustand in %s: %r", entity_id, state.state)
        return None


def read_datetime(hass: HomeAssistant, entity_id: str | None) -> datetime | None:
    """Zeitstempel einer Entität lesen — als ISO-Zustand oder ``datetime``-Helfer."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state.lower() in _NO_VALUE:
        return None
    parsed = dt_util.parse_datetime(state.state)
    if parsed is not None:
        return dt_util.as_local(parsed)
    parsed_time = dt_util.parse_time(state.state)
    if parsed_time is not None:
        # Reine Uhrzeit (z. B. `input_datetime` im Zeit-Modus): auf heute beziehen.
        return dt_util.start_of_local_day() + timedelta(
            hours=parsed_time.hour, minutes=parsed_time.minute
        )
    return None


def source_device(hass: HomeAssistant, entity_id: str | None) -> str | None:
    """Anzeigename der Quelle — trennt Uhrengenerationen in der Diagnose."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return state.attributes.get("friendly_name") or entity_id


async def async_state_history(
    hass: HomeAssistant, entity_id: str | None, start: datetime, end: datetime
) -> list[tuple[datetime, str]]:
    """Zustandsverlauf einer Entität aus dem Recorder holen."""
    if not entity_id:
        return []
    call = partial(
        history.state_changes_during_period,
        hass,
        start,
        end,
        entity_id,
        no_attributes=True,
        include_start_time_state=True,
    )
    try:
        result = await get_instance(hass).async_add_executor_job(call)
    except Exception as err:  # noqa: BLE001 - ein Recorder-Ausfall darf nur die Zeiten kosten
        _LOGGER.warning("Verlauf von %s nicht abrufbar: %s", entity_id, err)
        return []
    return [
        (dt_util.as_local(state.last_changed), state.state) for state in result.get(entity_id, [])
    ]


async def async_numeric_history(
    hass: HomeAssistant, entity_id: str | None, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    """Zahlenverlauf einer Entität — nicht-numerische Zustände werden verworfen."""
    values: list[tuple[datetime, float]] = []
    for when, raw in await async_state_history(hass, entity_id, start, end):
        if raw.lower() in _NO_VALUE:
            continue
        try:
            values.append((when, float(raw)))
        except ValueError:
            continue
    return values


#: Stunde, aus der beim Kaltstart der Nachtwert gelesen wird (lokale Zeit).
#:
#: **Nicht** das Tagesmaximum: Der Quellsensor trägt bis zum morgendlichen Sync
#: noch den Wert der *vorigen* Nacht. Das Tagesmaximum ist deshalb das Maximum
#: zweier aufeinanderfolgender Nächte und überschätzt den Schlaf systematisch —
#: mit einem zu kleinen Defizit als Folge. Um 10 Uhr steht dagegen zuverlässig
#: der Wert der vergangenen Nacht.
SEED_SAMPLE_HOUR = 10

#: Stunden, aus denen ersatzweise gelesen wird, wenn die Zielstunde fehlt.
SEED_FALLBACK_HOURS = (11, 9, 12, 8)


def is_night_arrival(moment: datetime) -> bool:
    """Ob ein Datenzugang im Morgenfenster liegt — sonst ist es ein Nickerchen."""
    return MORNING_START_HOUR <= moment.hour < MORNING_END_HOUR


async def async_build_night(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    day: date,
    arrival: datetime | None,
) -> SleepNight | None:
    """Eine Nacht aus dem aktuellen Zustand der Quellentitäten zusammensetzen.

    Gibt ``None`` zurück, wenn keine Schlafdauer vorliegt — eine Nacht ohne
    Dauer ist eine Lücke und darf nicht gespeichert werden.
    """
    values: dict[str, Any] = {
        field: read_float(hass, config.get(key)) for key, field in NUMERIC_FIELDS.items()
    }
    if not values.get("total_min"):
        return None

    night = SleepNight(
        date=day,
        source_device=source_device(hass, config.get(CONF_SLEEP_DURATION)),
        **values,
    )

    end = arrival or dt_util.now()
    start = end - timedelta(hours=HISTORY_LOOKBACK_HOURS)
    focus_history = await async_state_history(hass, config.get(CONF_FOCUS), start, end)
    steps_history = await async_numeric_history(hass, config.get(CONF_STEPS), start, end)

    anchor = determine_anchor(
        day=day,
        explicit_wake=read_datetime(hass, config.get(CONF_WAKE_TIME)),
        focus_history=focus_history,
        steps_history=steps_history,
        data_arrival=arrival,
        tzinfo=dt_util.get_default_time_zone(),
    )

    explicit_onset = read_datetime(hass, config.get(CONF_ONSET_TIME))
    onset = explicit_onset or derive_onset(anchor.wake_time, night.total_min, night.awake_min)

    confidence = anchor.confidence
    provenance = anchor.provenance

    if explicit_onset is not None:
        # Eine vom Nutzer gelieferte Einschlafzeit wird nicht plausibilisiert.
        provenance = Provenance.EXPLICIT
    elif onset is not None and not onset_is_plausible(onset, focus_phases(focus_history)):
        _LOGGER.debug("Unplausible Einschlafzeit %s für %s — Konfidenz gesenkt", onset, day)
        confidence = max(0.0, confidence - IMPLAUSIBLE_ONSET_PENALTY)

    # Ohne Anker gibt es auch keine abgeleitete Einschlafzeit.
    if anchor.wake_time is None and explicit_onset is None:
        onset = None

    return replace(
        night,
        wake_time=anchor.wake_time,
        onset_time=onset,
        anchor_provenance=provenance,
        confidence=confidence,
        interruptions=anchor.interruptions,
    )


async def async_seed_from_statistics(
    hass: HomeAssistant,
    entity_id: str,
    *,
    days: int,
) -> list[SleepNight]:
    """Nächte aus der Langzeitstatistik rekonstruieren (Kaltstart).

    Gelesen wird der Stundenmittelwert einer festen Vormittagsstunde, nicht das
    Tagesmaximum: Bis zum morgendlichen Sync trägt der Sensor noch den Wert der
    vorigen Nacht, sodass das Tagesmaximum das Maximum zweier Nächte wäre.

    Der **heutige** Tag wird ausgelassen. Er gehört der regulären Erfassung, die
    ihn mit echtem Anker und echter Konfidenz aufnimmt; ein geseedeter Eintrag
    würde ihr zuvorkommen und dauerhaft an ihrer Stelle stehen bleiben.

    Die so gewonnenen Nächte tragen :attr:`Provenance.SEEDED` und damit keine
    Uhrzeit-Metriken.
    """
    end = dt_util.now()
    start = end - timedelta(days=days)
    statistic_types: set[Literal["change", "last_reset", "max", "mean", "min", "state", "sum"]] = {
        "mean"
    }
    call = partial(
        statistics.statistics_during_period,
        hass,
        start,
        end,
        {entity_id},
        "hour",
        None,
        statistic_types,
    )
    try:
        rows = await get_instance(hass).async_add_executor_job(call)
    except Exception as err:  # noqa: BLE001 - Recorder-Fehler dürfen das Setup nicht kippen
        _LOGGER.warning("Kaltstart-Seeding aus der Statistik fehlgeschlagen: %s", err)
        return []

    # Je Kalendertag die Stundenwerte des Vormittags sammeln.
    by_day: dict[date, dict[int, float]] = {}
    for row in rows.get(entity_id, []):
        value = row.get("mean")
        if value is None:
            continue
        moment = dt_util.as_local(dt_util.utc_from_timestamp(row["start"]))
        by_day.setdefault(moment.date(), {})[moment.hour] = float(value)

    today = end.date()
    seeded: list[SleepNight] = []
    for day, hours in sorted(by_day.items()):
        if day >= today:
            continue
        for hour in (SEED_SAMPLE_HOUR, *SEED_FALLBACK_HOURS):
            if (value := hours.get(hour)) is not None and value > 0:
                seeded.append(
                    SleepNight(
                        date=day,
                        total_min=round(value, 1),
                        anchor_provenance=Provenance.SEEDED,
                        confidence=0.3,
                    )
                )
                break

    _LOGGER.debug("%d Nächte aus der Langzeitstatistik rekonstruiert", len(seeded))
    return seeded
