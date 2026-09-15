"""Datenmodell für Sleep Bank.

Reines Python — dieses Modul importiert bewusst nichts aus ``homeassistant``,
damit es im Simulator und in Unit-Tests ohne HA-Testharness nutzbar ist.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any


class Provenance(StrEnum):
    """Herkunft des Aufwach-Ankers einer Nacht.

    Bestimmt zusammen mit :attr:`SleepNight.confidence`, welche Metriken für
    diese Nacht berechnet werden dürfen. Die Reihenfolge entspricht absteigender
    Verlässlichkeit.
    """

    EXPLICIT = "explicit"
    """Direkte Aufwachzeit-Entität — gemessen, keine Ableitung."""

    FOCUS = "focus"
    """Fokus-Sensor (Schlafmodus) hat im Morgenfenster abgeschaltet."""

    STEPS = "steps"
    """Beginn anhaltender Schrittaktivität am Morgen."""

    DATA_ARRIVAL = "data_arrival"
    """Zeitpunkt, zu dem die Nachtdaten in HA eintrafen — Obergrenze."""

    SEEDED = "seeded"
    """Aus Langzeitstatistik nachträglich rekonstruiert (Kaltstart)."""

    NONE = "none"
    """Kein Anker ermittelbar — die Nacht hat keine verwertbaren Zeiten."""


#: Anker-Herkünfte, die keine Uhrzeit-Metriken (SRI, Social Jetlag) tragen dürfen.
UNTIMED_PROVENANCE = frozenset({Provenance.SEEDED, Provenance.NONE})


@dataclass(frozen=True, slots=True)
class SleepNight:
    """Eine ausgewertete Nacht.

    ``date`` ist der Kalendertag des *Aufwachens* — eine Nacht vom 13. auf den
    14. wird unter dem 14. geführt. Alle Dauern in Minuten.

    Fehlende Felder sind ``None`` und bedeuten *nicht gemessen*. Eine Nacht ohne
    Messung wird gar nicht erst angelegt; sie ist eine Lücke und darf nirgends
    als „0 Minuten Schlaf" auftauchen.
    """

    date: date
    total_min: float | None = None
    core_min: float | None = None
    deep_min: float | None = None
    rem_min: float | None = None
    awake_min: float | None = None
    onset_time: datetime | None = None
    wake_time: datetime | None = None
    interruptions: int | None = None
    nap_min: float | None = None
    resting_hr: float | None = None
    hrv_ms: float | None = None
    respiratory_rate: float | None = None
    source_device: str | None = None
    anchor_provenance: Provenance = Provenance.NONE
    confidence: float = 0.0

    # -- abgeleitete Eigenschaften -------------------------------------------------

    @property
    def has_duration(self) -> bool:
        """Ob eine verwertbare Schlafdauer vorliegt."""
        return self.total_min is not None and self.total_min > 0

    @property
    def has_stages(self) -> bool:
        """Ob die Uhr Schlafstadien geliefert hat (ältere Modelle tun das nicht)."""
        return self.deep_min is not None and self.rem_min is not None

    @property
    def has_timing(self) -> bool:
        """Ob Einschlaf- und Aufwachzeit für Uhrzeit-Metriken taugen."""
        return (
            self.onset_time is not None
            and self.wake_time is not None
            and self.anchor_provenance not in UNTIMED_PROVENANCE
        )

    @property
    def span_min(self) -> float | None:
        """Spanne vom Einschlafen bis zum Aufwachen, inkl. nächtlicher Wachzeit."""
        if self.total_min is None:
            return None
        return self.total_min + (self.awake_min or 0.0)

    @property
    def efficiency(self) -> float | None:
        """Schlafeffizienz: Anteil echten Schlafs an der Schlafspanne (0–1)."""
        span = self.span_min
        if span is None or span <= 0 or self.total_min is None:
            return None
        return self.total_min / span

    @property
    def midpoint(self) -> datetime | None:
        """Schlafmitte — Grundlage für Social Jetlag und Regularität."""
        if not self.has_timing:
            return None
        assert self.onset_time is not None and self.wake_time is not None
        return self.onset_time + (self.wake_time - self.onset_time) / 2

    # -- Serialisierung ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """In einen JSON-serialisierbaren Dict umwandeln (für den Store)."""
        raw = asdict(self)
        raw["date"] = self.date.isoformat()
        for key in ("onset_time", "wake_time"):
            value = getattr(self, key)
            raw[key] = value.isoformat() if value is not None else None
        raw["anchor_provenance"] = str(self.anchor_provenance)
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SleepNight:
        """Aus einem Store-Dict rekonstruieren."""
        data = dict(raw)
        data["date"] = date.fromisoformat(data["date"])
        for key in ("onset_time", "wake_time"):
            value = data.get(key)
            data[key] = datetime.fromisoformat(value) if value else None
        data["anchor_provenance"] = Provenance(data.get("anchor_provenance", "none"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def coverage(nights: list[SleepNight], window_days: int, today: date) -> float:
    """Anteil der Tage im Fenster, für die eine Nacht mit Dauer vorliegt (0–1).

    Das Vertrauensmaß für alle Fenster-Metriken. Fehlende Nächte sind Lücken,
    keine Nullwerte — deshalb wird hier gezählt, nicht summiert.
    """
    if window_days <= 0:
        return 0.0
    start = today - timedelta(days=window_days - 1)
    present = {n.date for n in nights if n.has_duration and start <= n.date <= today}
    return len(present) / window_days
