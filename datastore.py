"""Thread-sicherer gemeinsamer Datenspeicher: Poller schreibt, WebUI/API/MCP lesen.

Die Historie wird optional in SQLite persistiert, damit sie Server-Neustarts
übersteht und weiter geliefert werden kann.
"""

import json
import logging
import sqlite3
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Deque, Dict, Optional

log = logging.getLogger("datastore")

# Tageszähler des Wechselrichters (kWh) für die Energie-Bilanz.
# Deltas werden pro Poll in die Tabelle energy_delta persistiert.
ENERGY_COUNTER_FIELDS = {
    "pv_wh": "e_day",
    "import_wh": "e_day_imp",
    "export_wh": "e_day_exp",
    "load_wh": "e_load_day",
    "bat_charge_wh": "e_bat_charge_day",
    "bat_discharge_wh": "e_bat_discharge_day",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    """Wandelt beliebige Wechselrichter-Werte in JSON-kompatible Typen um.

    Die goodwe-Bibliothek liefert z. B. datetime-Objekte (Timestamp-Sensoren),
    Enums oder bytes – Flask/jsonify wirft darauf sonst einen 500er.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


class DataStore:
    def __init__(
        self,
        history_size: int = 1440,
        poll_interval: int = 60,
        db_path: Optional[str] = None,
        energy_retention_days: int = 400,
    ) -> None:
        self._lock = threading.Lock()
        self._history_size = history_size
        self._energy_retention_days = energy_retention_days
        self._status: Dict[str, Any] = {
            "online": False,
            "last_error": None,
            "started_at": _now_iso(),
            "poll_count": 0,
            "success_count": 0,
            "fail_count": 0,
            "connected_since": None,
            "poll_interval": poll_interval,
        }
        self._device: Dict[str, Any] = {}
        self._runtime: Dict[str, Any] = {}
        self._settings: Dict[str, Any] = {}
        self._fields: list = []
        self._unsupported_settings: list = []
        self._metrics: Dict[str, Any] = {}
        self._warning: Dict[str, Any] = {}
        self._pv_stats: Dict[int, Dict[str, float]] = {}  # Stunde -> {total, count}
        self._history: Deque[Dict[str, Any]] = deque(maxlen=history_size)
        self._poll_interval = poll_interval
        # Energie-Zähler-Deltas (Kern-Prozess)
        self._prev_counters: Optional[dict] = None
        self._last_counter_ts: Optional[float] = None
        # Cursor für den Stunden-/Tagesabschluss
        self._finalize_cursor: Optional[tuple] = None

        # Persistente Historie (SQLite) – überlebt Neustarts des Dienstes
        self._db: Optional[sqlite3.Connection] = None
        if db_path:
            self._load_history_db(db_path)

    # ---------- Schreibseite (Poller) ----------

    def begin_poll(self) -> None:
        with self._lock:
            self._status["poll_count"] += 1

    def _load_history_db(self, db_path: str) -> None:
        try:
            self._db = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
            self._db.execute("PRAGMA journal_mode=WAL")  # Lesen + Schreiben parallel
            self._db.execute("PRAGMA busy_timeout=10000")
            self._db.execute(
                'CREATE TABLE IF NOT EXISTS history '
                '(ts REAL PRIMARY KEY, iso TEXT NOT NULL, "values" TEXT NOT NULL)'
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS snapshot "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS pv_stats "
                "(hour INTEGER PRIMARY KEY, total REAL NOT NULL, count INTEGER NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS energy_delta ("
                "ts REAL PRIMARY KEY, iso TEXT NOT NULL, dt REAL NOT NULL,"
                "pv_wh REAL NOT NULL, import_wh REAL NOT NULL, export_wh REAL NOT NULL,"
                "load_wh REAL NOT NULL, bat_charge_wh REAL NOT NULL, bat_discharge_wh REAL NOT NULL)"
            )
            # Migration: Tages-/Stundenspalte für die Tages-Statistik
            cols = [r[1] for r in self._db.execute("PRAGMA table_info(energy_delta)").fetchall()]
            if "day" not in cols:
                self._db.execute("ALTER TABLE energy_delta ADD COLUMN day TEXT")
            if "hour" not in cols:
                self._db.execute("ALTER TABLE energy_delta ADD COLUMN hour INTEGER")
            # Stundensnapshots: abgeschlossene Stunden (erzeugt/verbraucht je Stunde)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS energy_hourly ("
                "date TEXT NOT NULL, hour INTEGER NOT NULL,"
                "pv_wh REAL NOT NULL, import_wh REAL NOT NULL, export_wh REAL NOT NULL,"
                "load_wh REAL NOT NULL, bat_charge_wh REAL NOT NULL, bat_discharge_wh REAL NOT NULL,"
                "samples INTEGER NOT NULL, finalized_at TEXT NOT NULL,"
                "PRIMARY KEY (date, hour))"
            )
            # Tagesfinale: der endgültige Tageswert (23:59 bzw. nach Tageswechsel)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS energy_daily_final ("
                "date TEXT PRIMARY KEY,"
                "pv_wh REAL NOT NULL, import_wh REAL NOT NULL, export_wh REAL NOT NULL,"
                "load_wh REAL NOT NULL, bat_charge_wh REAL NOT NULL, bat_discharge_wh REAL NOT NULL,"
                "samples INTEGER NOT NULL, finalized_at TEXT NOT NULL)"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_energy_delta_day_hour ON energy_delta(day, hour)"
            )
            self._db.commit()
            rows = self._db.execute(
                'SELECT ts, iso, "values" FROM history ORDER BY ts DESC LIMIT ?',
                (self._history_size,),
            ).fetchall()
            for ts, iso, values in reversed(rows):
                self._history.append({"ts": ts, "iso": iso, "values": json.loads(values)})
            if rows:
                log.info("Historie aus %s geladen: %d Messpunkte", db_path, len(rows))
            # PV-Stundenstatistik laden
            for hour, total, count in self._db.execute(
                "SELECT hour, total, count FROM pv_stats"
            ).fetchall():
                self._pv_stats[hour] = {"total": total, "count": count}
            # Zuletzt persistierten Snapshot laden (Runtime, Settings, Status …)
            self._load_snapshot_db()
        except Exception as exc:
            log.warning("Historie konnte nicht aus %s geladen werden: %s", db_path, exc)
            self._db = None

    def _load_snapshot_db(self) -> None:
        rows = self._db.execute("SELECT key, value FROM snapshot").fetchall()
        data = {k: json.loads(v) for k, v in rows}
        self._runtime = data.get("runtime", self._runtime)
        self._settings = data.get("settings", self._settings)
        self._device = data.get("device", self._device)
        self._fields = data.get("fields", self._fields)
        self._unsupported_settings = data.get("unsupported_settings", self._unsupported_settings)
        self._metrics = data.get("metrics", self._metrics)
        self._warning = data.get("warning", self._warning)
        self._status.update(data.get("status", {}))
        # Der Kern (Poller) setzt poll_interval bei jeder Loop-Iteration
        # selbst; andere Prozesse (MCP/WebUI) übernehmen den DB-Wert.
        if rows:
            log.info("Snapshot aus DB geladen (%d Einträge)", len(rows))

    def refresh_from_db(self) -> None:
        """Lädt Snapshot & Historie neu aus der DB.

        Wird von eigenständigen Prozessen (z. B. MCP-Server) genutzt, die
        nicht selbst pollen, sondern die Daten des Kern-Services lesen.
        """
        if self._db is None:
            return
        with self._lock:
            try:
                self._load_snapshot_db()
                rows = self._db.execute(
                    'SELECT ts, iso, "values" FROM history ORDER BY ts DESC LIMIT ?',
                    (self._history_size,),
                ).fetchall()
                self._history = deque(
                    ({"ts": t, "iso": i, "values": json.loads(v)} for t, i, v in reversed(rows)),
                    maxlen=self._history_size,
                )
                self._pv_stats = {
                    hour: {"total": total, "count": count}
                    for hour, total, count in self._db.execute(
                        "SELECT hour, total, count FROM pv_stats"
                    ).fetchall()
                }
            except Exception as exc:
                log.warning("Refresh aus DB fehlgeschlagen: %s", exc)

    def _persist_snapshot(self) -> None:
        """Schreibt den kompletten Snapshot in die DB (für andere Prozesse)."""
        if self._db is None:
            return
        try:
            payload = {
                "runtime": self._runtime,
                "settings": self._settings,
                "device": self._device,
                "fields": self._fields,
                "unsupported_settings": self._unsupported_settings,
                "metrics": self._metrics,
                "warning": self._warning,
                "status": self._status,
            }
            for key, value in payload.items():
                self._db.execute(
                    "INSERT OR REPLACE INTO snapshot (key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )
            self._db.commit()
        except Exception as exc:
            log.warning("Snapshot konnte nicht in DB geschrieben werden: %s", exc)

    def _persist_history_point(self, point: Dict[str, Any]) -> None:
        if self._db is None:
            return
        try:
            self._db.execute(
                'INSERT OR REPLACE INTO history (ts, iso, "values") VALUES (?, ?, ?)',
                (point["ts"], point["iso"], json.dumps(point["values"])),
            )
            # Alte Punkte außerhalb des Ringpuffers aufräumen
            self._db.execute(
                "DELETE FROM history WHERE ts < "
                "(SELECT ts FROM history ORDER BY ts DESC LIMIT 1 OFFSET ?)",
                (max(0, self._history_size - 1),),
            )
            self._db.commit()
        except Exception as exc:
            log.warning("Messpunkt konnte nicht in DB geschrieben werden: %s", exc)

    def set_poll_interval(self, interval: float) -> None:
        """Aktualisiert das effektive Abfrage-Intervall (aus config.toml)."""
        with self._lock:
            self._poll_interval = int(interval)
            self._status["poll_interval"] = self._poll_interval

    def add_pv_sample(self, hour: int, value: float) -> None:
        """Führt einen ppv-Messwert in die Stundenstatistik ein (persistent)."""
        with self._lock:
            self._add_pv_sample_locked(hour, value)

    def _add_pv_sample_locked(self, hour: int, value: float) -> None:
        """Wie add_pv_sample, aber ohne Lock (Caller hält den Lock bereits)."""
        slot = self._pv_stats.setdefault(hour, {"total": 0.0, "count": 0.0})
        slot["total"] += value
        slot["count"] += 1
        if self._db is not None:
            try:
                self._db.execute(
                    "INSERT INTO pv_stats (hour, total, count) VALUES (?, ?, ?) "
                    "ON CONFLICT(hour) DO UPDATE SET "
                    "total = total + excluded.total, count = count + excluded.count",
                    (hour, float(value), 1),
                )
                self._db.commit()
            except Exception as exc:
                log.warning("PV-Statistik konnte nicht geschrieben werden: %s", exc)

    def update(
        self,
        runtime: Dict[str, Any],
        settings: Dict[str, Any],
        device: Dict[str, Any],
        fields: list,
        unsupported_settings: Optional[list] = None,
        metrics: Optional[Dict[str, Any]] = None,
        warning: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self._runtime = {k: _jsonable(v) for k, v in (runtime or {}).items()}
            self._settings = {k: _jsonable(v) for k, v in (settings or {}).items()}
            self._device = {k: _jsonable(v) for k, v in (device or {}).items()}
            self._fields = [{**f, "value": _jsonable(f.get("value"))} for f in fields]
            self._unsupported_settings = list(unsupported_settings or [])
            self._metrics = {k: _jsonable(v) for k, v in (metrics or {}).items()}
            self._warning = {k: _jsonable(v) for k, v in (warning or {}).items()}
            # PV-Leistung in die Stundenstatistik einfließen lassen
            ppv = self._runtime.get("ppv")
            if isinstance(ppv, (int, float)) and not isinstance(ppv, bool):
                self._add_pv_sample_locked(datetime.now().astimezone().hour, float(ppv))
            self._status["online"] = True
            self._status["last_error"] = None
            self._status["success_count"] += 1
            self._status["connected_since"] = (
                self._status["connected_since"] or _now_iso()
            )
            # Messpunkt für die Historie (nur numerische Werte)
            numeric = {
                k: v
                for k, v in runtime.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }
            self._history.append(
                {
                    "ts": time.time(),
                    "iso": _now_iso(),
                    "values": numeric,
                }
            )
            self._persist_history_point(self._history[-1])
            self._persist_snapshot()
            self._update_energy_counters()
            self._finalize_energy_periods()

    def _update_energy_counters(self) -> None:
        """Persistiert Zähler-Deltas (kWh → Wh) der Wechselrichter-Tageszähler.

        Wird nur im Kern (Poller) aufgerufen. Überspringt Lücken
        (Neustart/Offline), damit keine falschen Deltas entstehen.
        """
        cur = {}
        for k, field in ENERGY_COUNTER_FIELDS.items():
            v = self._runtime.get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cur[k] = float(v)
        now_ts = time.time()
        if len(cur) != len(ENERGY_COUNTER_FIELDS):
            self._prev_counters = None
            self._last_counter_ts = now_ts
            return

        prev = self._prev_counters
        self._prev_counters = cur
        if prev is None or self._last_counter_ts is None:
            self._last_counter_ts = now_ts
            return

        dt = now_ts - self._last_counter_ts
        self._last_counter_ts = now_ts
        if dt <= 0 or dt > max(5 * self._poll_interval, 120):
            return  # Lücke – kein Delta, damit die Bilanz nicht verfälscht wird

        deltas = {}
        for k in ENERGY_COUNTER_FIELDS:
            new, old = cur[k], prev[k]
            # Tageszähler-Reset um Mitternacht (new < old) → neuer Tageswert
            delta = new if new < old else new - old
            deltas[k] = max(0.0, delta) * 1000.0  # kWh → Wh

        if self._db is not None:
            try:
                self._db.execute(
                    "INSERT OR REPLACE INTO energy_delta "
                    "(ts, iso, dt, pv_wh, import_wh, export_wh, load_wh, "
                    "bat_charge_wh, bat_discharge_wh, day, hour) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now_ts, _now_iso(), dt, deltas["pv_wh"], deltas["import_wh"],
                     deltas["export_wh"], deltas["load_wh"],
                     deltas["bat_charge_wh"], deltas["bat_discharge_wh"],
                     datetime.now().astimezone().strftime("%Y-%m-%d"),
                     datetime.now().astimezone().hour),
                )
                # Alte Deltas aufräumen (Retention)
                self._db.execute(
                    "DELETE FROM energy_delta WHERE ts < ?",
                    (now_ts - self._energy_retention_days * 86400,),
                )
                self._db.commit()
            except Exception as exc:
                log.warning("Energie-Delta konnte nicht geschrieben werden: %s", exc)

    def query_daily_energy(self, from_day: str, to_day: str) -> list:
        """Tages-Aggregate (Wh je Fluss + Abdeckung) je lokalem Datum."""
        if self._db is None:
            return []
        try:
            rows = self._db.execute(
                "SELECT day, SUM(pv_wh), SUM(import_wh), SUM(export_wh), "
                "SUM(load_wh), SUM(bat_charge_wh), SUM(bat_discharge_wh), SUM(dt) "
                "FROM energy_delta WHERE day >= ? AND day <= ? AND day IS NOT NULL "
                "GROUP BY day ORDER BY day",
                (from_day, to_day),
            ).fetchall()
            return [
                {
                    "day": r[0], "pv_wh": r[1] or 0.0, "import_wh": r[2] or 0.0,
                    "export_wh": r[3] or 0.0, "load_wh": r[4] or 0.0,
                    "bat_charge_wh": r[5] or 0.0, "bat_discharge_wh": r[6] or 0.0,
                    "covered_seconds": r[7] or 0.0,
                }
                for r in rows
            ]
        except Exception as exc:
            log.warning("Tages-Aggregate konnten nicht gelesen werden: %s", exc)
            return []

    def _finalize_energy_periods(self) -> None:
        """Schließt vergangene Stunden und Tage in den Snapshot-Tabellen ab.

        - Jede abgelaufene Stunde: Zeile in energy_hourly (Erzeugung, Verbrauch, …)
        - Jeder abgelaufene Tag: finaler Tageswert in energy_daily_final
        - 23:59-Regel: in der letzten Minute des Tages wird der aktuelle
          Tageswert vorab als finaler Stand gespeichert.
        Aufholen nach Neustart/Lücken erfolgt automatisch über den Cursor.
        """
        if self._db is None:
            return
        try:
            # Fehlende Tages-/Stundenspalten nachpflegen (Migration älterer Zeilen)
            self._backfill_energy_period_columns()
            now = datetime.now().astimezone()
            today = now.strftime("%Y-%m-%d")
            now_key = (today, now.hour)

            if self._finalize_cursor is None:
                row = self._db.execute(
                    "SELECT date, MAX(hour) FROM energy_hourly "
                    "WHERE date = (SELECT MAX(date) FROM energy_hourly)"
                ).fetchone()
                if row and row[0]:
                    last_day, last_hour = row[0], int(row[1] or -1)
                    if last_hour < 23:
                        self._finalize_cursor = (last_day, last_hour + 1)
                    else:
                        nxt = datetime.strptime(last_day, "%Y-%m-%d") + timedelta(days=1)
                        self._finalize_cursor = (nxt.strftime("%Y-%m-%d"), 0)
                else:
                    first = self._db.execute(
                        "SELECT MIN(day) FROM energy_delta WHERE day IS NOT NULL"
                    ).fetchone()[0]
                    self._finalize_cursor = (first or today, 0)
            cursor_day, cursor_hour = self._finalize_cursor

            # Tagesabschluss: alle abgelaufenen Tage
            self._db.execute(
                "INSERT OR IGNORE INTO energy_daily_final "
                "(date, pv_wh, import_wh, export_wh, load_wh, bat_charge_wh, "
                "bat_discharge_wh, samples, finalized_at) "
                "SELECT day, SUM(pv_wh), SUM(import_wh), SUM(export_wh), SUM(load_wh), "
                "SUM(bat_charge_wh), SUM(bat_discharge_wh), COUNT(*), ? "
                "FROM energy_delta WHERE day IS NOT NULL AND day < ? GROUP BY day",
                (_now_iso(), today),
            )

            # 23:59-Regel: finaler Stand des heutigen Tages in der letzten Minute
            if now.hour == 23 and now.minute >= 59:
                self._db.execute(
                    "INSERT OR REPLACE INTO energy_daily_final "
                    "(date, pv_wh, import_wh, export_wh, load_wh, bat_charge_wh, "
                    "bat_discharge_wh, samples, finalized_at) "
                    "SELECT day, SUM(pv_wh), SUM(import_wh), SUM(export_wh), SUM(load_wh), "
                    "SUM(bat_charge_wh), SUM(bat_discharge_wh), COUNT(*), ? "
                    "FROM energy_delta WHERE day = ?",
                    (_now_iso(), today),
                )

            # Stundenabschluss: alle Stunden vor der aktuellen, ab Cursor
            if (cursor_day, cursor_hour) < now_key:
                self._db.execute(
                    "INSERT OR IGNORE INTO energy_hourly "
                    "(date, hour, pv_wh, import_wh, export_wh, load_wh, "
                    "bat_charge_wh, bat_discharge_wh, samples, finalized_at) "
                    "SELECT day, hour, SUM(pv_wh), SUM(import_wh), SUM(export_wh), "
                    "SUM(load_wh), SUM(bat_charge_wh), SUM(bat_discharge_wh), COUNT(*), ? "
                    "FROM energy_delta "
                    "WHERE day IS NOT NULL AND hour IS NOT NULL "
                    "AND (day > ? OR (day = ? AND hour >= ?)) "
                    "AND (day < ? OR (day = ? AND hour < ?)) "
                    "GROUP BY day, hour",
                    (_now_iso(), cursor_day, cursor_day, cursor_hour,
                     today, today, now.hour),
                )
                self._finalize_cursor = now_key
            self._db.commit()
        except Exception as exc:
            log.warning("Periodenabschluss (Stunden/Tage) fehlgeschlagen: %s", exc)

    def _backfill_energy_period_columns(self) -> None:
        """Setzt day/hour für ältere energy_delta-Zeilen nach (einmalig je Charge)."""
        if self._db is None:
            return
        missing = self._db.execute(
            "SELECT ts FROM energy_delta WHERE day IS NULL OR hour IS NULL LIMIT 20000"
        ).fetchall()
        for (ts,) in missing:
            local = datetime.fromtimestamp(ts).astimezone()
            self._db.execute(
                "UPDATE energy_delta SET day=?, hour=? WHERE ts=?",
                (local.strftime("%Y-%m-%d"), local.hour, ts),
            )

    def query_hourly_energy(self, date: str) -> list:
        """Stundensnapshots eines Tages (abgeschlossene Stunden)."""
        if self._db is None:
            return []
        try:
            rows = self._db.execute(
                "SELECT hour, pv_wh, import_wh, export_wh, load_wh, "
                "bat_charge_wh, bat_discharge_wh, samples, finalized_at "
                "FROM energy_hourly WHERE date = ? ORDER BY hour",
                (date,),
            ).fetchall()
            return [
                {
                    "hour": r[0], "pv_wh": r[1], "import_wh": r[2],
                    "export_wh": r[3], "load_wh": r[4],
                    "bat_charge_wh": r[5], "bat_discharge_wh": r[6],
                    "samples": r[7], "finalized_at": r[8],
                }
                for r in rows
            ]
        except Exception as exc:
            log.warning("Stundensnapshots konnten nicht gelesen werden: %s", exc)
            return []

    def query_daily_finals(self, from_day: str, to_day: str) -> list:
        """Final gespeicherte Tageswerte (energy_daily_final)."""
        if self._db is None:
            return []
        try:
            rows = self._db.execute(
                "SELECT date, pv_wh, import_wh, export_wh, load_wh, "
                "bat_charge_wh, bat_discharge_wh, samples, finalized_at "
                "FROM energy_daily_final WHERE date >= ? AND date <= ? ORDER BY date",
                (from_day, to_day),
            ).fetchall()
            return [
                {
                    "date": r[0], "pv_wh": r[1], "import_wh": r[2],
                    "export_wh": r[3], "load_wh": r[4],
                    "bat_charge_wh": r[5], "bat_discharge_wh": r[6],
                    "samples": r[7], "finalized_at": r[8],
                }
                for r in rows
            ]
        except Exception as exc:
            log.warning("Tagesfinale konnten nicht gelesen werden: %s", exc)
            return []

    def query_energy_deltas(self, from_ts: float, to_ts: float) -> list:
        """Liefert die persistierten Energie-Deltas im Zeitraum (aufsteigend)."""
        if self._db is None:
            return []
        try:
            rows = self._db.execute(
                "SELECT ts, dt, pv_wh, import_wh, export_wh, load_wh, "
                "bat_charge_wh, bat_discharge_wh FROM energy_delta "
                "WHERE ts >= ? AND ts <= ? ORDER BY ts",
                (from_ts, to_ts),
            ).fetchall()
            return [
                {
                    "ts": r[0], "dt": r[1], "pv_wh": r[2], "import_wh": r[3],
                    "export_wh": r[4], "load_wh": r[5],
                    "bat_charge_wh": r[6], "bat_discharge_wh": r[7],
                }
                for r in rows
            ]
        except Exception as exc:
            log.warning("Energie-Deltas konnten nicht gelesen werden: %s", exc)
            return []

    def set_error(self, message: str) -> None:
        with self._lock:
            self._status["online"] = False
            self._status["last_error"] = message
            self._status["fail_count"] += 1
            self._status["connected_since"] = None

    # ---------- Leseseite (WebUI / API) ----------

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            runtime = dict(self._runtime)
            settings = dict(self._settings)
            device = dict(self._device)
            fields = [dict(f) for f in self._fields]
            status = dict(self._status)
            unsupported_settings = list(self._unsupported_settings)
            metrics = dict(self._metrics)
            warning = dict(self._warning)
            last_ts = self._history[-1]["iso"] if self._history else None
            last_point_ts = self._history[-1]["ts"] if self._history else None
            poll_interval = status.get("poll_interval", 60)

        # Daten-Alter prüfen: liefert der Kern-Service keine aktuellen Daten
        # mehr (z. B. gestoppt), den Status als offline markieren.
        if last_point_ts is not None:
            age = time.time() - last_point_ts
            if age > max(90, 3 * poll_interval):
                status["online"] = False
                status["last_error"] = (
                    f"Daten veraltet (letzte Abfrage vor {age / 60:.0f} min) – "
                    "läuft der Kern-Service (goodwe.service)?"
                )

        return {
            "status": status,
            "last_update": last_ts,
            "device": device,
            "fields": fields,
            "runtime": runtime,
            "settings": settings,
            "unsupported_settings": unsupported_settings,
            "metrics": metrics,
            "warning": warning,
            "pv_stats": {
                str(hour): {
                    "avg_w": round(slot["total"] / slot["count"], 1) if slot["count"] else 0.0,
                    "samples": int(slot["count"]),
                }
                for hour, slot in self._pv_stats.items()
            },
        }

    def history(self, minutes: Optional[int] = None, max_points: Optional[int] = None) -> list:
        """Historie als Liste von {ts, iso, values}; optional zeitlich begrenzt."""
        with self._lock:
            data = [dict(p) for p in self._history]
        if minutes is not None:
            cutoff = time.time() - minutes * 60
            data = [p for p in data if p["ts"] >= cutoff]
        if max_points is not None and len(data) > max_points:
            data = data[-max_points:]
        return data

    def history_series(
        self,
        fields: Optional[str] = None,
        minutes: Optional[int] = None,
        max_points: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Historie als API-Antwort; optional auf komma-getrennte Felder gefiltert."""
        data = self.history(minutes=minutes, max_points=max_points)
        series = [{"ts": p["ts"], "iso": p["iso"], "values": p["values"]} for p in data]
        if fields:
            wanted = {f.strip() for f in fields.split(",") if f.strip()}
            series = [
                {"ts": p["ts"], "iso": p["iso"],
                 "values": {k: v for k, v in p["values"].items() if k in wanted}}
                for p in series
            ]
        return {"count": len(series), "points": series}
