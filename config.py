"""Konfiguration für den GoodWe-Service (per Umgebungsvariablen überschreibbar)."""

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _str(name: str, default: str) -> str:
    return os.environ.get(name) or default


# Wechselrichter
INVERTER_IP = _str("GOODWE_IP", "192.168.1.108")
INVERTER_PORT = _int("GOODWE_PORT", 8899)
INVERTER_FAMILY = _str("GOODWE_FAMILY", "") or None
INVERTER_TIMEOUT = _int("GOODWE_TIMEOUT", 5)
INVERTER_RETRIES = _int("GOODWE_RETRIES", 3)

# Polling-Loop (Abfrage alle X Sekunden – Standard 60 s, um Rate-Limits zu vermeiden)
POLL_INTERVAL = _int("GOODWE_POLL_INTERVAL", 60)

# Historie: Anzahl gespeicherter Messpunkte (bei 60 s Intervall = 1440 Punkte = 24 h)
HISTORY_SIZE = _int("GOODWE_HISTORY_SIZE", 1440)

# WebUI (Flask)
UI_HOST = _str("GOODWE_UI_HOST", "0.0.0.0")
UI_PORT = _int("GOODWE_UI_PORT", 8080)

# API (FastAPI)
API_HOST = _str("GOODWE_API_HOST", "0.0.0.0")
API_PORT = _int("GOODWE_API_PORT", 8000)
