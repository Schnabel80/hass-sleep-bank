"""Architekturzusicherungen.

Die Trennung zwischen Modellmathematik und Home-Assistant-Anbindung ist die
tragende Entwurfsentscheidung dieser Integration. Sie lässt sich nicht durch
einen Importversuch prüfen — das Paket ``sleep_ledger`` ist der Einstiegspunkt
von Home Assistant und zieht HA beim Paketimport zwangsläufig mit herein.

Prüfbar ist stattdessen die Eigenschaft, auf die es ankommt: **kein Modellmodul
enthält einen Import aus** ``homeassistant``. Damit bleibt die Mathematik
unabhängig überprüfbar, im Simulator wiederverwendbar und frei von versteckten
Abhängigkeiten auf Zustand, Zeitzone oder Konfiguration der Instanz.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "sleep_ledger"

#: Module, die frei von Home-Assistant-Abhängigkeiten bleiben müssen.
PURE_MODULES = (
    "models.py",
    "debt.py",
    "regularity.py",
    "physiology.py",
    "readiness.py",
    "timing.py",
    "sleep_need.py",
)


def _imported_roots(source: str) -> set[str]:
    """Wurzelpakete aller Importe eines Moduls."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("filename", PURE_MODULES)
def test_modellmodul_ist_frei_von_home_assistant(filename: str) -> None:
    path = COMPONENT / filename
    roots = _imported_roots(path.read_text(encoding="utf-8"))
    assert "homeassistant" not in roots, (
        f"{filename} importiert Home Assistant. Die Modellmathematik muss davon "
        f"frei bleiben — gehört der Zugriff in ingest.py oder coordinator.py?"
    )


@pytest.mark.parametrize("filename", PURE_MODULES)
def test_modellmodul_importiert_nicht_aus_const(filename: str) -> None:
    """``const.py`` importiert Home Assistant und ist daher für sie tabu.

    Modellparameter stehen deshalb in ihrem jeweiligen Modellmodul, direkt neben
    der Mathematik, die sie steuert.
    """
    source = (COMPONENT / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    relative = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0 and node.module
    }
    assert "const" not in relative, f"{filename} importiert const.py"


def test_alle_modellmodule_sind_erfasst() -> None:
    """Schutz gegen ein neues Modellmodul, das die Liste nicht kennt."""
    ha_free = {
        path.name
        for path in COMPONENT.glob("*.py")
        if "homeassistant" not in _imported_roots(path.read_text(encoding="utf-8"))
        and path.name != "__init__.py"
    }
    assert ha_free == set(PURE_MODULES), (
        "Es gibt HA-freie Module außerhalb der Liste — entweder in PURE_MODULES "
        f"aufnehmen oder bewusst anbinden: {sorted(ha_free - set(PURE_MODULES))}"
    )
