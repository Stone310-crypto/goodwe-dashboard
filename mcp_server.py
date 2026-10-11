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

from datastore import DataStore


def create_server(store: DataStore) -> MCPServer:
    server = MCPServer(
        name="goodwe-monitor",
        title="GoodWe Monitor",
        description=(
            "Live- und Verlaufsdaten des GoodWe-Wechselrichters, gesammelt "
            "durch einen Polling-Loop (Abfrage alle 60 s)."
        ),
        version="1.0.0",
    )

    @server.tool()
    def get_all() -> dict:
        """Liefert den kompletten Schnappschuss: Status, Geräte-Info, Felder, Runtime-Werte, Settings und abgelehnte Settings."""
        return store.snapshot()

    @server.tool()
    def get_status() -> dict:
        """Liefert den Verbindungsstatus (online, Zähler, letzter Fehler) und die Geräte-Info des Wechselrichters."""
        snap = store.snapshot()
        return {
            "status": snap["status"],
            "device": snap["device"],
            "last_update": snap["last_update"],
        }

    @server.tool()
    def get_data() -> dict:
        """Liefert die aktuellen Runtime-Werte des Wechselrichters (Feldname -> Wert)."""
        return store.snapshot()["runtime"]

    @server.tool()
    def get_settings() -> dict:
        """Liefert die aktuell unterstützten Wechselrichter-Settings."""
        return store.snapshot()["settings"]

    @server.tool()
    def get_fields() -> list:
        """Liefert die Metadaten aller Felder (Name, Einheit, Kategorie, aktueller Wert)."""
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
        return store.history_series(fields=fields, minutes=minutes, max_points=max_points)

    return server


def create_http_app(store: DataStore):
    """Streamable-HTTP-App (ASGI) für uvicorn, inkl. CORS für die WebGUI."""
    server = create_server(store)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,      # JSON statt SSE – einfacher für die WebGUI
        stateless_http=True,     # keine Session-Verwaltung nötig
    )
    return CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
