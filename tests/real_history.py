"""Aufgezeichnete Sensorverläufe aus einer echten Installation.

Erhoben am 14.09.2026 aus einer Home-Assistant-Instanz mit Apple Watch und
iOS-Companion-App (Zeitzone Europe/Berlin). Diese Verläufe sind keine erdachten
Randfälle, sondern die tatsächlich beobachteten Ausfallarten des Fokus-Sensors.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

BERLIN = timezone(timedelta(hours=2))


def _at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=BERLIN)


#: Verlauf von ``binary_sensor.<geraet>_focus`` über sieben Tage.
FOCUS_HISTORY: list[tuple[datetime, str]] = [
    (_at("2026-09-07 11:30:26"), "off"),
    (_at("2026-09-07 22:03:34"), "on"),
    (_at("2026-09-08 06:17:20"), "off"),
    # Tages-Fokus — kein Schlaf.
    (_at("2026-09-08 13:16:29"), "on"),
    (_at("2026-09-08 14:29:23"), "off"),
    # Verlorener Übergang: 32 Stunden am Stück "on".
    (_at("2026-09-08 21:50:49"), "on"),
    (_at("2026-09-10 05:46:40"), "off"),
    # Kurzer Aussetzer mitten in der Nacht.
    (_at("2026-09-11 23:02:08"), "on"),
    (_at("2026-09-12 00:44:37"), "off"),
    (_at("2026-09-12 00:45:08"), "on"),
    (_at("2026-09-12 06:15:47"), "off"),
    (_at("2026-09-12 22:59:03"), "on"),
    (_at("2026-09-13 07:12:01"), "off"),
    # App-Neustart: unavailable und zurück auf denselben Wert.
    (_at("2026-09-13 07:31:37"), "unavailable"),
    (_at("2026-09-13 07:31:37"), "off"),
    # Nacht 13.->14.09.: kein einziger Übergang.
]

#: Zeitpunkte, zu denen die Nachtdaten in HA eintrafen, samt gemeldeter Werte.
SLEEP_ARRIVALS: dict[str, tuple[datetime, float, float]] = {
    # Datum des Aufwachens -> (Ankunft, total_min, awake_min)
    "2026-09-13": (_at("2026-09-13 07:30:27"), 536.0, 9.0),
    "2026-09-14": (_at("2026-09-14 06:09:52"), 395.0, 3.0),
}
