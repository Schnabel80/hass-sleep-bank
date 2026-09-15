"""Tests für den Bereitschaftsindex."""

from __future__ import annotations

import pytest
from sleep_ledger import readiness


def test_ausgeruht_ergibt_hohen_wert() -> None:
    result = readiness.compute(acute_debt_min=-60.0, recovery_z=1.0, sri=85.0)
    assert result.score is not None
    assert result.score > 80.0


def test_ausgelaugt_ergibt_niedrigen_wert() -> None:
    result = readiness.compute(acute_debt_min=420.0, recovery_z=-2.5, sri=50.0)
    assert result.score is not None
    assert result.score < 25.0


def test_fehlende_komponenten_werden_renormiert() -> None:
    """Eine fehlende Regularität darf den Wert nicht nach unten ziehen."""
    result = readiness.compute(acute_debt_min=120.0, recovery_z=None, sri=None)
    assert result.components == {"debt": pytest.approx(75.0)}
    assert result.weights == {"debt": pytest.approx(1.0)}
    assert result.score == pytest.approx(75.0)


def test_ohne_jede_komponente_kein_wert() -> None:
    assert readiness.compute(acute_debt_min=None, recovery_z=None, sri=None).score is None


def test_wert_bleibt_im_bereich() -> None:
    for debt_min in (-10_000.0, 0.0, 10_000.0):
        for z in (-10.0, 0.0, 10.0):
            for sri in (-100.0, 0.0, 100.0):
                score = readiness.compute(acute_debt_min=debt_min, recovery_z=z, sri=sri).score
                assert score is not None
                assert 0.0 <= score <= 100.0


def test_ergebnis_ist_als_heuristik_gekennzeichnet() -> None:
    """Diese Zusicherung ist bewusst getestet — sie darf nie stillschweigend fallen."""
    assert readiness.compute(acute_debt_min=0.0, recovery_z=0.0, sri=70.0).is_heuristic
