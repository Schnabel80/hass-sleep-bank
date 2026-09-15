"""Ende-zu-Ende-Prüfung des Modells an synthetischen Schläfertypen.

Diese Tests prüfen keine einzelnen Formeln, sondern ob die Kennzahlen bei
bekannten Schlafmustern das Erwartete *rangfolgenrichtig* anzeigen. Sie schlagen
an, wenn eine Änderung an den Zeitkonstanten das Verhalten kippt, ohne dass ein
Unit-Test es merkt.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulator"))

from sleep_ledger import debt, physiology, regularity
from sleepers import generate

DAYS = 90
START = date(2026, 1, 1)
END = START + timedelta(days=DAYS - 1)


@pytest.fixture(scope="module")
def series() -> dict[str, list]:
    """Je eine Nachtserie pro Schläfertyp."""
    return {
        name: generate(name, days=DAYS, start=START)
        for name in ("regelmäßig", "Wochenend-Nachholer", "Schichtarbeiter")
    }


def test_regularitaet_ordnet_die_typen_richtig(series: dict[str, list]) -> None:
    """Regelmäßig > Wochenend-Nachholer > Schichtarbeiter."""
    values = {name: regularity.compute(nights, today=END).sri for name, nights in series.items()}
    assert all(value is not None for value in values.values())
    assert values["regelmäßig"] > values["Wochenend-Nachholer"] > values["Schichtarbeiter"]


def test_social_jetlag_trifft_den_wochenend_nachholer(series: dict[str, list]) -> None:
    """Wer am Wochenende zwei Stunden später schläft, hat Social Jetlag."""
    regular = regularity.compute(series["regelmäßig"], today=END).social_jetlag_hours
    shifter = regularity.compute(series["Wochenend-Nachholer"], today=END).social_jetlag_hours
    assert regular is not None and shifter is not None
    assert regular < 0.5
    assert shifter > 2.0


def test_chronisches_defizit_trifft_die_kurzschlaefer(series: dict[str, list]) -> None:
    regular = debt.compute(series["regelmäßig"], today=END).chronic_min_per_night
    shifter = debt.compute(series["Wochenend-Nachholer"], today=END).chronic_min_per_night
    assert regular is not None and shifter is not None
    assert regular < 20.0
    assert shifter > 40.0


def test_konto_und_physiologie_decken_verschiedene_blinde_flecken(
    series: dict[str, list],
) -> None:
    """Der Grund, warum beide Kennzahlen nebeneinander existieren.

    Der Wochenend-Nachholer steht chronisch deutlich im Minus. Seine
    physiologischen z-Scores sind trotzdem unauffällig — die rollierende Baseline
    hat sich längst an den Dauerzustand gewöhnt. Ein reines Erholungsmaß würde
    dieses Muster also übersehen; das Konto sieht es.
    """
    nights = series["Wochenend-Nachholer"]
    chronic = debt.compute(nights, today=END).chronic_min_per_night
    recovery = physiology.compute(nights, today=END).recovery_z

    assert chronic is not None and chronic > 40.0, "chronisch klar im Defizit"
    assert recovery is not None
    assert recovery > -1.0, "physiologisch dennoch unauffällig — die Baseline hat sich angepasst"


def test_restriktionsprotokoll_zeigt_den_erwarteten_verlauf() -> None:
    """14 Nächte à 8 h, 14 à 6 h, dann Erholung — Konto und Bereitschaft folgen."""
    nights = generate("harte Wochen", days=56, start=START)

    def acute_at(index: int) -> float:
        result = debt.compute(nights[: index + 1], today=START + timedelta(days=index))
        assert result.acute_min is not None
        return result.acute_min

    baseline = acute_at(13)
    peak = acute_at(27)
    recovered = acute_at(41)

    assert baseline < 60.0, "vor der Restriktion nahezu ausgeglichen"
    assert peak > 300.0, "am Ende der Restriktion deutliches Defizit"
    assert recovered < baseline, "nach zwei Wochen Erholung wieder im Plus"
