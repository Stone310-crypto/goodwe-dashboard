"""Thread-sicherer gemeinsamer Datenspeicher: Poller schreibt, WebUI/API lesen."""

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DataStore:
    def __init__(self, history_size: int = 1440, poll_interval: int = 60) -> None:
        self._lock = threading.Lock()
        self._history_size = history_size
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
        self._history: Deque[Dict[str, Any]] = deque(maxlen=history_size)

    # ---------- Schreibseite (Poller) ----------

    def begin_poll(self) -> None:
        with self._lock:
            self._status["poll_count"] += 1

    def update(
        self,
        runtime: Dict[str, Any],
        settings: Dict[str, Any],
        device: Dict[str, Any],
        fields: list,
    ) -> None:
        with self._lock:
            self._runtime = runtime
            self._settings = settings
            self._device = device
            self._fields = fields
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
            last_ts = self._history[-1]["iso"] if self._history else None
        return {
            "status": status,
            "last_update": last_ts,
            "device": device,
            "fields": fields,
            "runtime": runtime,
            "settings": settings,
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
