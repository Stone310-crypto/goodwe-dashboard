"""GoodWe Monitor als Service.

Startet vier Komponenten:
  1. Poller-Loop  – fragt den Wechselrichter jede Minute ab (gegen Rate-Limits)
  2. WebUI (Flask) – Statusseite; ruft ihre Daten über MCP ab
  3. API (FastAPI) – HTTP-API, damit andere Server die Daten abfragen können
  4. MCP-Server    – zentrale Datenquelle (Model Context Protocol) für
                     WebGUI und LLM-Clients; Historie persistent in SQLite

Konfiguration über Umgebungsvariablen, siehe config.py.
"""

import logging
import threading
import time

import uvicorn

import config
import poller
from api import create_app as create_api_app
from datastore import DataStore
from mcp_server import create_http_app as create_mcp_app
from webui import run as run_webui

log = logging.getLogger("service")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    # Interne Tracebacks der goodwe-Bibliothek ausblenden (z. B. bei
    # nicht unterstützten Settings) – der Poller protokolliert kompakt selbst.
    logging.getLogger("goodwe").setLevel(logging.CRITICAL)
    # Flask/Zugriffslog und MCP-Session-Logs reduzieren
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("mcp.server").setLevel(logging.WARNING)

    store = DataStore(
        history_size=config.HISTORY_SIZE,
        poll_interval=config.POLL_INTERVAL,
        db_path=config.DB_PATH or None,
    )

    threads = [
        threading.Thread(target=poller.run, args=(store,), daemon=True, name="poller"),
        threading.Thread(
            target=run_webui,
            args=(store, config.UI_HOST, config.UI_PORT),
            daemon=True,
            name="webui",
        ),
        threading.Thread(
            target=uvicorn.run,
            kwargs={
                "app": create_api_app(store),
                "host": config.API_HOST,
                "port": config.API_PORT,
                "log_level": "warning",
            },
            daemon=True,
            name="api",
        ),
        threading.Thread(
            target=uvicorn.run,
            kwargs={
                "app": create_mcp_app(store),
                "host": config.MCP_HOST,
                "port": config.MCP_PORT,
                "log_level": "warning",
            },
            daemon=True,
            name="mcp",
        ),
    ]

    log.info(
        "GoodWe-Service startet: IP=%s, Poll-Intervall=%ss, "
        "WebUI auf %s:%s, API auf %s:%s, MCP auf %s:%s (Endpoint: /mcp)",
        config.INVERTER_IP,
        config.POLL_INTERVAL,
        config.UI_HOST,
        config.UI_PORT,
        config.API_HOST,
        config.API_PORT,
        config.MCP_HOST,
        config.MCP_PORT,
    )

    for thread in threads:
        thread.start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("Beende GoodWe-Service …")


if __name__ == "__main__":
    main()