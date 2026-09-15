"""Tests für das Schlafkonto.

Die zentralen Fälle bilden publizierte Befunde ab. Wenn das Modell diese
Szenarien qualitativ nicht mehr reproduziert, ist es kaputt — unabhängig davon,
ob der Code noch läuft.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

import pytest
from sleep_ledger import debt
from sleep_ledger.models import SleepNight

START = date(2026, 1, 1)


def series(durations: list[float], start: date = START) -> list[SleepNight]:
    return [
        SleepNight(date=start + timedelta(days=i), total_min=d) for i, d in enumerate(durations)
    ]


def _today(nights: list[SleepNight]) -> date:
    return max(n.date for n in nights)


# -- Kumulation -------------------------------------------------------------------


def test_defizit_waechst_monoton_bei_dauerhafter_restriktion() -> None:
    """Van Dongen: Bei 6 h/Nacht wächst das Defizit über zwei Wochen weiter."""
    values = []
    for count in range(3, 15):
        nights = series([360.0] * count)
        result = debt.compute(nights, today=_today(nights))
        assert result.acute_min is not None
        values.append(result.acute_min)
    assert all(b > a for a, b in pairwise(values))


def test_chronisches_mittel_entspricht_dem_taeglichen_defizit() -> None:
    """14 Nächte à 6 h bei 8 h Bedarf sind 120 min Defizit pro Nacht."""
    nights = series([360.0] * 14)
    result = debt.compute(nights, today=_today(nights))
    assert result.chronic_min_per_night == pytest.approx(120.0, abs=1.0)


def test_eine_gute_nacht_setzt_das_konto_nicht_zurueck() -> None:
    """Der Kernbefund: Erholung ist kein Schalter."""
    restricted = series([360.0] * 10)
    before = debt.compute(restricted, today=_today(restricted)).acute_min
    assert before is not None

    recovered = series([360.0] * 10 + [480.0])
    after = debt.compute(recovered, today=_today(recovered)).acute_min
    assert after is not None
    assert after < before  # es hilft ...
    assert after > before * 0.5  # ... aber bei weitem nicht genug


def test_erholungsdauer_entspricht_der_literatur() -> None:
    """Nach 10 Nächten à 6 h braucht es rund eine Woche à 8 h."""
    nights = series([360.0] * 10)
    acute = debt.compute(nights, today=_today(nights)).acute_min
    assert acute is not None
    assert 5 <= debt.nights_to_recovery(acute, target_sleep_min=480.0) <= 10


def test_zu_kurzer_zielschlaf_gleicht_nie_aus() -> None:
    nights = series([360.0] * 10)
    acute = debt.compute(nights, today=_today(nights)).acute_min
    assert acute is not None
    assert debt.nights_to_recovery(acute, target_sleep_min=420.0) is None


def test_ausgeglichenes_konto_braucht_keine_erholung() -> None:
    assert debt.nights_to_recovery(0.0, target_sleep_min=480.0) == 0


# -- Asymmetrien ------------------------------------------------------------------


def test_guthaben_ist_gedeckelt() -> None:
    """Sleep Banking wirkt, aber nur begrenzt — sonst wäre Vorschlafen unbegrenzt."""
    nights = series([600.0] * 30)
    result = debt.compute(nights, today=_today(nights))
    assert result.acute_min == pytest.approx(-debt.DEFAULT_CREDIT_CAP_MIN)


def test_erholung_ist_langsamer_als_der_verlust() -> None:
    """Eine Stunde zu wenig wiegt schwerer als eine Stunde zu viel."""
    deficit = series([420.0])  # 60 min zu wenig
    surplus = series([540.0])  # 60 min zu viel
    loss = debt.compute(deficit, today=_today(deficit)).last_balance_min
    gain = debt.compute(surplus, today=_today(surplus)).last_balance_min
    assert loss is not None and gain is not None
    assert abs(gain) < abs(loss)


# -- Lücken -----------------------------------------------------------------------


def test_fehlende_nacht_ist_keine_nacht_mit_null_stunden() -> None:
    """Der wichtigste Datenschutz des Modells."""
    full = series([420.0] * 20)
    with_gap = [n for i, n in enumerate(full) if i != 10]

    complete = debt.compute(full, today=_today(full))
    gapped = debt.compute(with_gap, today=_today(full))

    assert complete.chronic_min_per_night is not None
    assert gapped.chronic_min_per_night is not None
    assert gapped.chronic_min_per_night == pytest.approx(complete.chronic_min_per_night, abs=2.0)


def test_alter_datenstand_altert_aus() -> None:
    """Ein stehengebliebener Datenstrom darf das Konto nicht einfrieren."""
    nights = series([360.0] * 14)
    fresh = debt.compute(nights, today=_today(nights)).acute_min
    stale = debt.compute(nights, today=_today(nights) + timedelta(days=14)).acute_min
    assert fresh is not None and stale is not None
    assert stale < fresh * 0.1


def test_zu_wenig_naechte_liefert_keine_zahl() -> None:
    result = debt.compute(series([360.0] * 2), today=START + timedelta(days=1))
    assert result.acute_min is None
    assert result.chronic_min_per_night is None


def test_leere_historie() -> None:
    result = debt.compute([], today=START)
    assert result.nights_used == 0
    assert result.last_balance_min is None


# -- Nickerchen -------------------------------------------------------------------


def test_nickerchen_zahlen_abgewertet_ein() -> None:
    plain = series([390.0] * 14)
    napped = [SleepNight(date=n.date, total_min=390.0, nap_min=60.0) for n in plain]
    without = debt.compute(plain, today=_today(plain)).acute_min
    with_nap = debt.compute(napped, today=_today(napped)).acute_min
    assert without is not None and with_nap is not None
    assert with_nap < without
