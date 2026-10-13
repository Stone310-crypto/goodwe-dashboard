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
INVERTER_IP = _str("GOODWE_IP", "192.168.1.105")
INVERTER_PORT = _int("GOODWE_PORT", 8899)
INVERTER_FAMILY = _str("GOODWE_FAMILY", "") or None
INVERTER_TIMEOUT = _int("GOODWE_TIMEOUT", 5)
INVERTER_RETRIES = _int("GOODWE_RETRIES", 3)

# Polling-Loop (Abfrage alle X Sekunden – Standard 10 s)
POLL_INTERVAL = _int("GOODWE_POLL_INTERVAL", 10)

# Settings ändern sich selten: nur alle N Polls lesen (Standard: alle 30 Polls
# = 5 Minuten bei 10 s Intervall)
SETTINGS_INTERVAL = _int("GOODWE_SETTINGS_INTERVAL", 30)

# Historie: Anzahl gespeicherter Messpunkte (8640 = 24 h bei 10 s Intervall)
HISTORY_SIZE = _int("GOODWE_HISTORY_SIZE", 8640)

# Energie-Deltas (Wh je Poll) für Bilanz/Autarkie: Aufbewahrungsdauer in Tagen
ENERGY_RETENTION_DAYS = _int("GOODWE_ENERGY_RETENTION_DAYS", 400)

# WebUI (Flask)
UI_HOST = _str("GOODWE_UI_HOST", "0.0.0.0")
UI_PORT = _int("GOODWE_UI_PORT", 8080)

# API (FastAPI)
API_HOST = _str("GOODWE_API_HOST", "0.0.0.0")
API_PORT = _int("GOODWE_API_PORT", 8000)

# MCP-Server (Model Context Protocol) – Datenquelle für WebGUI & LLM-Clients
MCP_HOST = _str("GOODWE_MCP_HOST", "0.0.0.0")
MCP_PORT = _int("GOODWE_MCP_PORT", 8001)

# SQLite-Datei für die persistente Historie (überlebt Server-Neustarts)
DB_PATH = _str("GOODWE_DB_PATH", "goodwe_history.db")

# Benutzer-Einstellungen (config.toml, über die WebUI editierbar)
USER_CONFIG_PATH = _str("GOODWE_CONFIG_PATH", "config.toml")
