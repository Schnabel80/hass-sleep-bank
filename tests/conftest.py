"""Gemeinsame Test-Fixtures.

Zwei Importpfade, mit Absicht:

* ``sleep_bank.*`` — die Modellmodule, so wie der Simulator sie verwendet.
* ``custom_components.sleep_bank.*`` — die Integration, wie Home Assistant sie
  lädt. Über diesen Weg laufen die Tests der HA-Schicht.

Beide Wege benötigen Home Assistant im Environment, weil ``sleep_bank/__init__.py``
der Einstiegspunkt von HA ist und beim Paketimport mitläuft. Dass die
Modellmodule selbst frei von HA-Importen bleiben, prüft ``test_architecture.py``
statisch — ein Importversuch könnte das nicht zeigen.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "custom_components"))

import pytest  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.sleep_bank.const import (  # noqa: E402
    CONF_AWAKE,
    CONF_FOCUS,
    CONF_HRV,
    CONF_RESPIRATORY_RATE,
    CONF_RESTING_HR,
    CONF_SLEEP_DURATION,
    CONF_STEPS,
    DOMAIN,
)

SLEEP_ENTITY = "sensor.watch_sleep_duration"
AWAKE_ENTITY = "sensor.watch_awake"
FOCUS_ENTITY = "binary_sensor.phone_focus"
STEPS_ENTITY = "sensor.phone_health_steps"
RHR_ENTITY = "sensor.watch_resting_heart_rate"
HRV_ENTITY = "sensor.watch_heart_rate_variability"
RR_ENTITY = "sensor.watch_respiratory_rate"

BASE_CONFIG = {
    CONF_SLEEP_DURATION: SLEEP_ENTITY,
    CONF_AWAKE: AWAKE_ENTITY,
    CONF_FOCUS: FOCUS_ENTITY,
    CONF_STEPS: STEPS_ENTITY,
    CONF_RESTING_HR: RHR_ENTITY,
    CONF_HRV: HRV_ENTITY,
    CONF_RESPIRATORY_RATE: RR_ENTITY,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Custom Integrations in allen Tests laden."""
    return


@pytest.fixture
def source_states(hass):
    """Quellentitäten mit plausiblen Werten bestücken."""
    hass.states.async_set(SLEEP_ENTITY, "395", {"unit_of_measurement": "min"})
    hass.states.async_set(AWAKE_ENTITY, "3", {"unit_of_measurement": "min"})
    hass.states.async_set(FOCUS_ENTITY, "off")
    hass.states.async_set(STEPS_ENTITY, "1428")
    hass.states.async_set(RHR_ENTITY, "66")
    hass.states.async_set(HRV_ENTITY, "43.1")
    hass.states.async_set(RR_ENTITY, "14")
    return hass


@pytest.fixture
def config_entry():
    """Vorkonfigurierter Config-Entry."""
    return MockConfigEntry(domain=DOMAIN, title="Sleep Bank", data=dict(BASE_CONFIG))
