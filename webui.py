"""Flask-WebUI: Statusseite mit wählbaren Statistiken.

Die Seite ist nur noch ein dünner Client: Sie liefert das HTML aus und
ruft alle Daten über MCP-Tool-Calls vom MCP-Server ab (gleicher Host,
MCP-Port aus der Konfiguration).
"""

from flask import Flask, render_template, request

import config
from datastore import DataStore


def create_app(store: DataStore) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        # Host aus dem Request übernehmen (funktioniert über LAN/Tailscale),
        # Port des MCP-Servers aus der Konfiguration.
        host = request.host.rsplit(":", 1)[0]
        mcp_base = f"{request.scheme}://{host}:{config.MCP_PORT}/mcp"
        return render_template("index.html", mcp_base=mcp_base)

    return app


def run(store: DataStore, host: str, port: int) -> None:
    create_app(store).run(host=host, port=port, threaded=True, use_reloader=False)
