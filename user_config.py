"""Benutzer-Einstellungen in config.toml (lesen + schreiben).

Die Datei wird von allen Diensten bei jeder Abfrage frisch eingelesen
(mtime-Cache) – Änderungen über die WebUI wirken dadurch sofort,
ohne dass der MCP-Server neu gestartet werden muss.
"""

import logging
import os
import threading
import time
import tomllib

import config

log = logging.getLogger("user_config")

DEFAULTS = {
    "battery_capacity_kwh": 10.0,   # Batteriekapazität in kWh
    "warning_threshold_w": 2000,    # Warnschwelle Hausverbrauch in W
    "poll_interval_seconds": 10,    # Abfrage-Intervall in s
}

_PATH = os.environ.get("GOODWE_CONFIG_PATH") or config.USER_CONFIG_PATH

_lock = threading.Lock()
_cache: dict | None = None
_cache_mtime: float | None = None


def _read_raw() -> dict:
    try:
        with open(_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log.warning("config.toml konnte nicht gelesen werden (%s) – verwende Standardwerte", exc)
        return {}


def _to_float(value, key: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        log.warning("config.toml: '%s' ist keine Zahl – verwende Standard %s", key, DEFAULTS[key])
        return float(DEFAULTS[key])


def load(fresh: bool = False) -> dict:
    """Lädt die Einstellungen; cached per mtime bis sich die Datei ändert."""
    global _cache, _cache_mtime
    with _lock:
        try:
            mtime = os.stat(_PATH).st_mtime
        except OSError:
            mtime = None
        if not fresh and _cache is not None and mtime is not None and mtime == _cache_mtime:
            return dict(_cache)
        raw = _read_raw()
        cfg = {
            "battery_capacity_kwh": _to_float(raw.get("battery_capacity_kwh", DEFAULTS["battery_capacity_kwh"]), "battery_capacity_kwh"),
            "warning_threshold_w": _to_float(raw.get("warning_threshold_w", DEFAULTS["warning_threshold_w"]), "warning_threshold_w"),
            "poll_interval_seconds": _to_float(raw.get("poll_interval_seconds", DEFAULTS["poll_interval_seconds"]), "poll_interval_seconds"),
        }
        _cache = cfg
        _cache_mtime = mtime
        return dict(cfg)


def save(updates: dict) -> dict:
    """Validiert und schreibt die Einstellungen (atomar). Wirkt sofort."""
    unknown = set(updates) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unbekannte Einstellung: {', '.join(sorted(unknown))}")

    cfg = load(fresh=True)
    for key, value in updates.items():
        try:
            cfg[key] = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' muss eine Zahl sein")
    if cfg["battery_capacity_kwh"] <= 0:
        raise ValueError("Batteriekapazität muss größer als 0 sein")
    if cfg["warning_threshold_w"] < 0:
        raise ValueError("Warnschwelle darf nicht negativ sein")
    if not (2 <= cfg["poll_interval_seconds"] <= 600):
        raise ValueError("Abfrage-Intervall muss zwischen 2 und 600 Sekunden liegen")

    lines = [
        "# GoodWe Monitor – benutzerspezifische Einstellungen",
        "# Bearbeitbar über die WebUI (Abschnitt \"Einstellungen\").",
        "# Änderungen wirken ohne Neustart.",
        "",
        "# Batteriekapazität des Speichers in kWh",
        f"battery_capacity_kwh = {cfg['battery_capacity_kwh']:g}",
        "",
        "# Warnung, wenn der Hausverbrauch diese Leistung (W) überschreitet",
        f"warning_threshold_w = {cfg['warning_threshold_w']:g}",
        "",
        "# Abfrage-Intervall des Pollers in Sekunden (min 2, max 600)",
        f"poll_interval_seconds = {cfg['poll_interval_seconds']:g}",
        "",
    ]
    tmp = f"{_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, _PATH)
    log.info("config.toml aktualisiert: %s", cfg)

    global _cache, _cache_mtime
    with _lock:
        _cache = cfg
        try:
            _cache_mtime = os.stat(_PATH).st_mtime
        except OSError:
            _cache_mtime = None
        return dict(cfg)
