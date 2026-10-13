"""MCP-Server (Model Context Protocol): zentrale Datenquelle des Dienstes.

Läuft als Streamable-HTTP-Server (stateless, JSON-Antworten) und wird sowohl
von der WebGUI als auch von LLM-Clients (z. B. Copilot/Claude) genutzt.

Tools:
    get_all     – kompletter Schnappschuss (Status, Gerät, Felder, Werte, Settings)
    get_status  – Verbindungsstatus & Geräte-Info
    get_data    – aktuelle Runtime-Werte
    get_settings– aktuelle Wechselrichter-Settings
    get_fields  – Feld-Metadaten inkl. aktuellem Wert
    get_history – Historie, optional nach Feldern/Zeitfenster gefiltert
"""

from mcp.server.mcpserver import MCPServer
from starlette.middleware.cors import CORSMiddleware

import user_config
from datastore import DataStore
from energy import EnergyCalculator


def create_server(store: DataStore) -> MCPServer:
    server = MCPServer(
        name="goodwe-monitor",
        title="GoodWe Monitor",
        description=(
            "Live- und Verlaufsdaten des GoodWe-Wechselrichters, gesammelt "
            "durch einen Polling-Loop (Abfrage alle 10 s)."
        ),
        version="1.0.0",
    )
    calculator = EnergyCalculator(store)

    @server.tool()
    def get_all() -> dict:
        """Liefert den kompletten Schnappschuss: Status, Geräte-Info, Felder, Runtime-Werte, Settings und abgelehnte Settings."""
        store.refresh_from_db()
        return store.snapshot()

    @server.tool()
    def get_status() -> dict:
        """Liefert den Verbindungsstatus (online, Zähler, letzter Fehler) und die Geräte-Info des Wechselrichters."""
        store.refresh_from_db()
        snap = store.snapshot()
        return {
            "status": snap["status"],
            "device": snap["device"],
            "last_update": snap["last_update"],
        }

    @server.tool()
    def get_data() -> dict:
        """Liefert die aktuellen Runtime-Werte des Wechselrichters (Feldname -> Wert)."""
        store.refresh_from_db()
        return store.snapshot()["runtime"]

    @server.tool()
    def get_settings() -> dict:
        """Liefert die aktuell unterstützten Wechselrichter-Settings."""
        store.refresh_from_db()
        return store.snapshot()["settings"]

    @server.tool()
    def get_fields() -> list:
        """Liefert die Metadaten aller Felder (Name, Einheit, Kategorie, aktueller Wert)."""
        store.refresh_from_db()
        return store.snapshot()["fields"]

    @server.tool()
    def get_history(
        fields: str | None = None,
        minutes: int | None = None,
        max_points: int | None = None,
    ) -> dict:
        """Liefert die Messpunkt-Historie.

        Args:
            fields: Komma-getrennte Feldnamen (leer = alle).
            minutes: Zeitfenster in Minuten (None = alles).
            max_points: Maximale Anzahl Messpunkte (None = alles).
        """
        store.refresh_from_db()
        return store.history_series(fields=fields, minutes=minutes, max_points=max_points)

    @server.tool()
    def get_metrics() -> dict:
        """Liefert berechnete Batterie-Kennzahlen: Ladezustand, gespeicherte/verbleibende
        kWh, Ladezeit bis voll, Entladezeit bis leer sowie den aktuellen
        Hausverbrauch und den Warnstatus (Verbrauchswarnung)."""
        store.refresh_from_db()
        snap = store.snapshot()
        return {"metrics": snap["metrics"], "warning": snap["warning"]}

    @server.tool()
    def get_pv_stats() -> dict:
        """Liefert das PV-Tagesprofil: durchschnittliche PV-Leistung je Stunde
        (kumuliert über alle Tage) plus die Stunde mit der höchsten Produktion."""
        store.refresh_from_db()
        stats = store.snapshot()["pv_stats"]
        hours = [
            {"hour": int(h), "avg_w": v["avg_w"], "samples": v["samples"]}
            for h, v in stats.items()
        ]
        hours.sort(key=lambda x: x["hour"])
        best = max((h for h in hours if h["samples"] > 0), key=lambda h: h["avg_w"], default=None)
        return {"hours": hours, "best_hour": best}

    @server.tool()
    def get_autarky(period: str = "today") -> dict:
        """Liefert den Autarkiegrad für einen Zeitraum.

        Autarkie = (Verbrauch − Netzbezug) / Verbrauch × 100 – wie viel Prozent
        des Verbrauchs ohne Netzbezug gedeckt wurden.

        Args:
            period: Zeitraum – "today", "week", "month" oder "year".
        """
        store.refresh_from_db()
        try:
            return calculator.compute_autarky(period)
        except ValueError as exc:
            return {"error": str(exc)}

    @server.tool()
    def get_energy_balance(period: str = "today") -> dict:
        """Liefert die vollständige Energie-Bilanz für einen Zeitraum:
        PV-Produktion, Verbrauch, Netzbezug/-einspeisung, Batterie sowie
        Autarkie- und Eigenverbrauchsquote.

        Args:
            period: Zeitraum – "today", "week", "month" oder "year".
        """
        store.refresh_from_db()
        try:
            return calculator.compute_balance(period)
        except ValueError as exc:
            return {"error": str(exc)}

    @server.tool()
    def get_config() -> dict:
        """Liefert die Benutzer-Einstellungen aus config.toml (Batteriekapazität, Warnschwelle, Abfrage-Intervall)."""
        return user_config.load()

    @server.tool()
    def update_config(
        battery_capacity_kwh: float | None = None,
        warning_threshold_w: float | None = None,
        poll_interval_seconds: float | None = None,
    ) -> dict:
        """Ändert Benutzer-Einstellungen in config.toml.

        Die Werte werden bei jeder Abfrage frisch eingelesen und wirken
        dadurch sofort – ein Neustart der Dienste ist nicht nötig.

        Args:
            battery_capacity_kwh: Batteriekapazität in kWh (> 0).
            warning_threshold_w: Warnschwelle Hausverbrauch in W (>= 0).
            poll_interval_seconds: Abfrage-Intervall in Sekunden (2–600).
        """
        updates = {}
        if battery_capacity_kwh is not None:
            updates["battery_capacity_kwh"] = battery_capacity_kwh
        if warning_threshold_w is not None:
            updates["warning_threshold_w"] = warning_threshold_w
        if poll_interval_seconds is not None:
            updates["poll_interval_seconds"] = poll_interval_seconds
        try:
            return {"ok": True, "config": user_config.save(updates)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "config": user_config.load()}

    return server


def create_http_app(store: DataStore):
    """Streamable-HTTP-App (ASGI) für uvicorn, inkl. CORS für die WebGUI."""
    server = create_server(store)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,      # JSON statt SSE – einfacher für die WebGUI
        stateless_http=True,     # keine Session-Verwaltung nötig
        # host != localhost: kein DNS-Rebinding-Schutz mit localhost-Whitelist
        # (sonst lehnt der Server Aufrufe über LAN/Tailscale mit 421 ab).
        host="0.0.0.0",
    )
    return CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
