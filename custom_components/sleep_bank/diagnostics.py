"""Diagnosedaten zum Herunterladen.

Enthält die vollständige Nachthistorie samt Herkunft und Konfidenz — genau die
Information, die man braucht, um eine unplausible Kennzahl zurückzuverfolgen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import SleepBankConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SleepBankConfigEntry
) -> dict[str, Any]:
    """Diagnose für einen Config-Entry zusammenstellen."""
    coordinator = entry.runtime_data
    data = coordinator.data
    nights = coordinator.store.nights

    return {
        "config": {**entry.data, **entry.options},
        "summary": {
            "stored_nights": len(nights),
            "coverage": round(data.coverage, 3),
            "sleep_need_min": round(data.need_min),
            "debt_acute_min": data.debt.acute_min,
            "debt_chronic_min_per_night": data.debt.chronic_min_per_night,
            "nights_used": data.debt.nights_used,
            "sri": data.regularity.sri,
            "sri_pairs": data.regularity.sri_pairs,
            "social_jetlag_hours": data.regularity.social_jetlag_hours,
            "recovery_z": data.physiology.recovery_z,
            "baseline_samples": data.physiology.baseline_samples,
            "readiness": data.readiness.score,
            "readiness_components": data.readiness.components,
            "readiness_is_heuristic": data.readiness.is_heuristic,
            "nights_to_recovery": {str(k): v for k, v in data.nights_to_recovery.items()},
        },
        "nights": [night.to_dict() for night in nights],
    }
