"""Tests für das Einlesen der Quellentitäten."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from conftest import BASE_CONFIG, SLEEP_ENTITY, STEPS_ENTITY
from homeassistant.util import dt as dt_util

from custom_components.sleep_bank import ingest
from custom_components.sleep_bank.const import CONF_ONSET_TIME, CONF_WAKE_TIME
from custom_components.sleep_bank.models import Provenance

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

BERLIN = ZoneInfo("Europe/Berlin")


# -- Zahlwerte --------------------------------------------------------------------


async def test_zahlwert_lesen(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.a", "395")
    assert ingest.read_float(hass, "sensor.a") == 395.0


@pytest.mark.parametrize("state", ["unavailable", "unknown", "", "keine Zahl"])
async def test_nicht_numerische_zustaende_ergeben_none(hass: HomeAssistant, state: str) -> None:
    hass.states.async_set("sensor.a", state)
    assert ingest.read_float(hass, "sensor.a") is None


async def test_fehlende_entitaet_ergibt_none(hass: HomeAssistant) -> None:
    assert ingest.read_float(hass, "sensor.gibt_es_nicht") is None
    assert ingest.read_float(hass, None) is None


# -- Zeitstempel ------------------------------------------------------------------


async def test_zeitstempel_aus_iso_zustand(hass: HomeAssistant) -> None:
    """Der Zeitpunkt bleibt erhalten und wird in die lokale Zeitzone überführt."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.states.async_set("sensor.wake", "2026-09-14T06:09:00+02:00")
    parsed = ingest.read_datetime(hass, "sensor.wake")
    assert parsed == datetime(2026, 9, 14, 6, 9, tzinfo=BERLIN)


async def test_reine_uhrzeit_wird_auf_heute_bezogen(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.wake", "06:09:00")
    parsed = ingest.read_datetime(hass, "sensor.wake")
    assert parsed is not None
    assert (parsed.hour, parsed.minute) == (6, 9)
    assert parsed.date() == dt_util.now().date()


async def test_unlesbarer_zeitstempel_ergibt_none(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.wake", "irgendwann")
    assert ingest.read_datetime(hass, "sensor.wake") is None
    assert ingest.read_datetime(hass, None) is None


async def test_quellgeraet_wird_uebernommen(hass: HomeAssistant) -> None:
    """Trennt Uhrengenerationen in der Diagnose."""
    hass.states.async_set("sensor.a", "395", {"friendly_name": "Watch Series 11"})
    assert ingest.source_device(hass, "sensor.a") == "Watch Series 11"
    hass.states.async_set("sensor.b", "1")
    assert ingest.source_device(hass, "sensor.b") == "sensor.b"
    assert ingest.source_device(hass, "sensor.weg") is None


# -- Verläufe ---------------------------------------------------------------------


async def test_verlauf_ohne_entitaet(hass: HomeAssistant) -> None:
    start = dt_util.now() - timedelta(hours=1)
    assert await ingest.async_state_history(hass, None, start, dt_util.now()) == []


async def test_verlauf_ohne_recorder_faellt_sauber_aus(hass: HomeAssistant) -> None:
    """Ein Recorder-Ausfall darf nur die Zeiten kosten, nicht das Setup."""
    start = dt_util.now() - timedelta(hours=1)
    assert await ingest.async_state_history(hass, SLEEP_ENTITY, start, dt_util.now()) == []
    assert await ingest.async_numeric_history(hass, STEPS_ENTITY, start, dt_util.now()) == []


async def test_seeding_ohne_recorder_faellt_sauber_aus(hass: HomeAssistant) -> None:
    assert await ingest.async_seed_from_statistics(hass, SLEEP_ENTITY, days=30) == []


# -- Nacht zusammensetzen ---------------------------------------------------------


async def test_ohne_schlafdauer_entsteht_keine_nacht(hass: HomeAssistant) -> None:
    """Eine Nacht ohne Dauer ist eine Lücke und darf nicht gespeichert werden."""
    hass.states.async_set(SLEEP_ENTITY, "unavailable")
    night = await ingest.async_build_night(
        hass, dict(BASE_CONFIG), day=date(2026, 9, 14), arrival=dt_util.now()
    )
    assert night is None


async def test_explizite_zeiten_haben_vorrang(hass: HomeAssistant, source_states) -> None:
    """Vom Nutzer gelieferte Zeiten werden nicht plausibilisiert oder überschrieben."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.states.async_set("sensor.wake", "2026-09-14T06:09:00+02:00")
    hass.states.async_set("sensor.onset", "2026-09-13T23:31:00+02:00")
    config = {**BASE_CONFIG, CONF_WAKE_TIME: "sensor.wake", CONF_ONSET_TIME: "sensor.onset"}

    night = await ingest.async_build_night(
        hass,
        config,
        day=date(2026, 9, 14),
        arrival=datetime(2026, 9, 14, 6, 10, tzinfo=BERLIN),
    )
    assert night is not None
    assert night.onset_time is not None
    assert night.onset_time.hour == 23
    assert night.has_timing


async def test_nickerchen_fenster(hass: HomeAssistant) -> None:
    """Der Morgen gehört der Nacht, der Rest des Tages dem Nickerchen."""
    assert ingest.is_night_arrival(datetime(2026, 9, 14, 6, 9, tzinfo=BERLIN))
    assert ingest.is_night_arrival(datetime(2026, 9, 14, 11, 59, tzinfo=BERLIN))
    assert not ingest.is_night_arrival(datetime(2026, 9, 14, 15, 30, tzinfo=BERLIN))
    assert not ingest.is_night_arrival(datetime(2026, 9, 14, 1, 0, tzinfo=BERLIN))


async def test_seeding_liest_den_vormittagswert_nicht_das_tagesmaximum(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, freezer
) -> None:
    """Regression: Das Tagesmaximum ist das Maximum *zweier* Nächte.

    Bis zum morgendlichen Sync trägt der Quellsensor noch den Wert der vorigen
    Nacht. Ein Tag, an dem bis 06:09 Uhr 536 min standen und danach 395 min,
    hat als Tagesmaximum 536 — also den Wert der Vornacht. Das überschätzt den
    Schlaf und beschönigt damit das Defizit. Gelesen wird deshalb ein fester
    Vormittagswert.
    """
    from custom_components.sleep_bank import ingest as module

    freezer.move_to(datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN))
    await hass.config.async_set_time_zone("Europe/Berlin")

    def stunde(tag: int, hour: int, wert: float) -> dict:
        moment = datetime(2026, 9, tag, hour, 0, tzinfo=BERLIN)
        return {"start": moment.timestamp(), "mean": wert}

    rows = {
        SLEEP_ENTITY: [
            # 13.09.: durchgehend 536
            stunde(13, 3, 536.0),
            stunde(13, 10, 536.0),
            # 14.09.: früh noch die Vornacht, ab dem Sync der echte Wert
            stunde(14, 3, 536.0),
            stunde(14, 5, 536.0),
            stunde(14, 10, 395.0),
            # 15.09. ist *heute* und gehört der regulären Erfassung
            stunde(15, 10, 452.0),
        ]
    }

    class _Recorder:
        @staticmethod
        async def async_add_executor_job(call):
            return call()

    monkeypatch.setattr(module.statistics, "statistics_during_period", lambda *a, **k: rows)
    monkeypatch.setattr(module, "get_instance", lambda _hass: _Recorder())

    seeded = await module.async_seed_from_statistics(hass, SLEEP_ENTITY, days=30)

    assert [(n.date.day, n.total_min) for n in seeded] == [(13, 536.0), (14, 395.0)]
    assert all(n.anchor_provenance is Provenance.SEEDED for n in seeded)
    assert not any(n.has_timing for n in seeded), "Schätzungen tragen keine Uhrzeitmetriken"


async def test_seeding_laesst_den_heutigen_tag_aus(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, freezer
) -> None:
    """Regression: Ein geseedeter Eintrag für heute blockierte die echte Erfassung.

    Die reguläre Erfassung überschreibt eine bestehende Nacht nur, wenn sie
    besser ist — eine Schätzung für den heutigen Tag stand ihr damit dauerhaft im
    Weg, und die Nacht blieb bei Konfidenz 0,3 ohne Anker stehen.
    """
    from custom_components.sleep_bank import ingest as module

    freezer.move_to(datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN))
    await hass.config.async_set_time_zone("Europe/Berlin")

    rows = {
        SLEEP_ENTITY: [
            {"start": datetime(2026, 9, 15, 10, 0, tzinfo=BERLIN).timestamp(), "mean": 452.0}
        ]
    }

    class _Recorder:
        @staticmethod
        async def async_add_executor_job(call):
            return call()

    monkeypatch.setattr(module.statistics, "statistics_during_period", lambda *a, **k: rows)
    monkeypatch.setattr(module, "get_instance", lambda _hass: _Recorder())

    assert await module.async_seed_from_statistics(hass, SLEEP_ENTITY, days=30) == []
