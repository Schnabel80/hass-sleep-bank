"""Persistenz der Nachthistorie.

Bewusst ein eigener Speicher statt des Recorders. Zwei Gründe:

1. Der Recorder hält Zustandsverläufe standardmäßig nur rund zehn Tage vor. Ein
   Langzeitmodell braucht Monate.
2. Die Quellsensoren tragen ``state_class: measurement``. Die Langzeitstatistik
   mittelt deren Wert über den Tag — aus 536 Minuten Schlaf wird dort ein
   zeitgewichteter Tagesmittelwert. Für einen einmal je Nacht gesetzten Wert ist
   das die falsche Aggregation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .const import MAX_STORED_NIGHTS, STORAGE_KEY, STORAGE_VERSION
from .models import SleepNight

if TYPE_CHECKING:
    from datetime import date

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class NightStore:
    """Hält die ausgewerteten Nächte eines Config-Entries."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Speicher für einen Config-Entry anlegen."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry_id}"
        )
        self._nights: dict[date, SleepNight] = {}
        self._meta: dict[str, Any] = {}

    @property
    def nights(self) -> list[SleepNight]:
        """Alle gespeicherten Nächte, nach Datum sortiert."""
        return sorted(self._nights.values(), key=lambda n: n.date)

    def get(self, day: date) -> SleepNight | None:
        """Nacht eines bestimmten Datums, falls vorhanden."""
        return self._nights.get(day)

    @property
    def meta(self) -> dict[str, Any]:
        """Zusatzangaben neben den Nächten, etwa das Kalibrierfenster."""
        return dict(self._meta)

    async def async_set_meta(self, **values: Any) -> None:
        """Metadaten setzen; ``None`` entfernt einen Schlüssel."""
        for key, value in values.items():
            if value is None:
                self._meta.pop(key, None)
            else:
                self._meta[key] = value
        await self.async_save()

    async def async_load(self) -> None:
        """Gespeicherte Nächte laden. Defekte Einträge werden übersprungen."""
        raw = await self._store.async_load()
        if not raw:
            return
        loaded: dict[date, SleepNight] = {}
        for entry in raw.get("nights", []):
            try:
                night = SleepNight.from_dict(entry)
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning("Unlesbarer Nacht-Eintrag übersprungen: %s", entry)
                continue
            loaded[night.date] = night
        self._nights = loaded
        self._meta = dict(raw.get("meta", {}))
        _LOGGER.debug("%d Nächte aus dem Speicher geladen", len(loaded))

    async def async_save(self) -> None:
        """Nächte schreiben, auf :data:`MAX_STORED_NIGHTS` begrenzt."""
        kept = self.nights[-MAX_STORED_NIGHTS:]
        await self._store.async_save({"nights": [n.to_dict() for n in kept], "meta": self._meta})

    async def async_put(self, night: SleepNight) -> None:
        """Eine Nacht ablegen oder ersetzen und sofort sichern."""
        self._nights[night.date] = night
        await self.async_save()

    async def async_put_many(self, nights: list[SleepNight], *, overwrite: bool = False) -> int:
        """Mehrere Nächte ablegen. Gibt die Zahl der tatsächlich neuen zurück.

        Ohne ``overwrite`` werden vorhandene Nächte nicht angetastet — das
        Kaltstart-Seeding darf gemessene Werte nicht durch Schätzungen ersetzen.
        """
        added = 0
        for night in nights:
            if not overwrite and night.date in self._nights:
                continue
            self._nights[night.date] = night
            added += 1
        if added:
            await self.async_save()
        return added

    async def async_remove(self) -> None:
        """Speicher löschen — wird beim Entfernen des Config-Entries aufgerufen."""
        self._nights = {}
        self._meta = {}
        await self._store.async_remove()
