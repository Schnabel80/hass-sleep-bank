"""Konstanten der Integration.

Konvention: Hier stehen alle HA-seitigen Konstanten — Domain, Config-Keys,
Speicherformat, Entitätsschlüssel. Die *Modellparameter* (Zeitkonstanten,
Gewichte, Schwellen) stehen bewusst in ihren jeweiligen Modellmodulen, direkt
neben der Mathematik, die sie steuern. Diese Module dürfen nichts aus Home
Assistant importieren und deshalb auch nicht aus dieser Datei.
"""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "sleep_bank"

PLATFORMS: Final[list[Platform]] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.SWITCH,
]

# -- Speicher --------------------------------------------------------------------

STORAGE_KEY: Final = f"{DOMAIN}.nights"
STORAGE_VERSION: Final = 1

#: Wie viele Nächte vorgehalten werden. Deutlich mehr als das längste
#: Auswertungsfenster, damit Rückrechnungen und Diagnosen Luft haben.
MAX_STORED_NIGHTS: Final = 400

# -- Config-Keys: Quellentitäten --------------------------------------------------

CONF_SLEEP_DURATION: Final = "sleep_duration_entity"
CONF_CORE_SLEEP: Final = "core_sleep_entity"
CONF_DEEP_SLEEP: Final = "deep_sleep_entity"
CONF_REM_SLEEP: Final = "rem_sleep_entity"
CONF_AWAKE: Final = "awake_entity"
CONF_RESTING_HR: Final = "resting_hr_entity"
CONF_HRV: Final = "hrv_entity"
CONF_RESPIRATORY_RATE: Final = "respiratory_rate_entity"

# -- Config-Keys: Zeiterfassung ---------------------------------------------------

CONF_WAKE_TIME: Final = "wake_time_entity"
CONF_ONSET_TIME: Final = "onset_time_entity"
CONF_FOCUS: Final = "focus_entity"
CONF_STEPS: Final = "steps_entity"

# -- Config-Keys: Persönliches ----------------------------------------------------

CONF_SLEEP_NEED: Final = "sleep_need_min"
CONF_AUTO_SLEEP_NEED: Final = "auto_sleep_need"
CONF_FREE_DAYS: Final = "free_days"

#: Zeitpunkt, zu dem der angewandte Bedarf zuletzt nachgezogen wurde.
CONF_NEED_ADAPTED_ON: Final = "sleep_need_adapted_on"

# -- Options ----------------------------------------------------------------------

CONF_TAU_ACUTE: Final = "tau_acute"
CONF_TAU_CHRONIC: Final = "tau_chronic"
CONF_RECOVERY_EFFICIENCY: Final = "recovery_efficiency"
CONF_CREDIT_CAP: Final = "credit_cap_min"
CONF_DEBT_ALERT_THRESHOLD: Final = "debt_alert_threshold_min"
CONF_WEIGHT_DEBT: Final = "weight_debt"
CONF_WEIGHT_PHYSIOLOGY: Final = "weight_physiology"
CONF_WEIGHT_REGULARITY: Final = "weight_regularity"

#: Chronisches Defizit pro Nacht, ab dem der Warnsensor auslöst.
DEFAULT_DEBT_ALERT_THRESHOLD_MIN: Final = 45.0

#: Freie Tage als Wochentagsindizes (0 = Montag).
#:
#: Bewusst **Zeichenketten**: Die Auswahlliste im Config-Flow arbeitet mit
#: Zeichenketten, und voluptuous validiert auch den eingesetzten Vorgabewert.
#: Ganzzahlen hier führen zu „expected str at 'free_days'", sobald die Oberfläche
#: das Feld nicht mitschickt — also praktisch immer.
DEFAULT_FREE_DAYS: Final = ["5", "6"]

# -- Auswertung -------------------------------------------------------------------

#: Fenster, über das die Datenabdeckung gemeldet wird.
COVERAGE_WINDOW_DAYS: Final = 14

#: Abdeckung, unterhalb derer Fenster-Metriken als unzuverlässig gelten.
MIN_COVERAGE: Final = 0.6

#: Zielschlafdauern für die Erholungsprognose, in Minuten.
RECOVERY_SCENARIOS: Final = (420.0, 480.0, 540.0)

#: Wie weit zurück der Recorder für die Ankerbestimmung befragt wird.
HISTORY_LOOKBACK_HOURS: Final = 20

#: Turnus der Neuberechnung. Rein lokale Rechnung ohne Netzwerkzugriff; der
#: stündliche Lauf sorgt nur dafür, dass das Konto über den Tag korrekt altert.
UPDATE_INTERVAL_HOURS: Final = 1

#: Uhrzeit der täglichen Auswertung der abgeschlossenen Nacht.
DAILY_EVALUATION_HOUR: Final = 12
DAILY_EVALUATION_MINUTE: Final = 30

# -- Entitätsschlüssel ------------------------------------------------------------

KEY_DEBT_ACUTE: Final = "debt_acute"
KEY_DEBT_CHRONIC: Final = "debt_chronic"
KEY_NIGHTS_TO_RECOVERY: Final = "nights_to_recovery"
KEY_SRI: Final = "regularity_index"
KEY_SOCIAL_JETLAG: Final = "social_jetlag"
KEY_MIDPOINT: Final = "sleep_midpoint"
KEY_MIDPOINT_VARIABILITY: Final = "midpoint_variability"
KEY_RECOVERY: Final = "recovery_score"
KEY_READINESS: Final = "readiness"
KEY_COVERAGE: Final = "data_coverage"
KEY_CONFIDENCE: Final = "last_night_confidence"
KEY_LAST_NIGHT_DURATION: Final = "last_night_duration"
KEY_DEBT_ALERT: Final = "debt_alert"
KEY_LOW_CONFIDENCE: Final = "low_confidence"
KEY_ESTIMATED_NEED: Final = "estimated_sleep_need"
KEY_CALIBRATION: Final = "calibration"

# -- Kalibrierung ------------------------------------------------------------------

STORAGE_META_CALIBRATION_START: Final = "calibration_start"
STORAGE_META_CALIBRATION_END: Final = "calibration_end"

#: Fenster, über das die bedarfsunabhängige Veränderung des Defizits gemeldet wird.
TREND_WINDOW_DAYS: Final = 28
