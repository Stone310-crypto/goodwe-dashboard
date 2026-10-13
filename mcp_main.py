"""Eigenständiger MCP-Server-Prozess (systemd: goodwe-mcp.service).

Pollt NICHT selbst – er liest den letzten Snapshot und die Historie bei
jedem Tool-Aufruf frisch aus der SQLite-DB, die der Kern-Service
(goodwe.service) schreibt. Dadurch liefert er Daten auch dann weiter,
wenn der Kern gerade neu startet.
"""

import logging

import uvicorn

import config
from datastore import DataStore
from mcp_server import create_http_app

log = logging.getLogger("mcp")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("mcp.server").setLevel(logging.WARNING)

    store = DataStore(
        history_size=config.HISTORY_SIZE,
        poll_interval=config.POLL_INTERVAL,
        db_path=config.DB_PATH or None,
    )

    log.info(
        "MCP-Server startet auf %s:%s (Endpoint: /mcp), Datenquelle: %s",
        config.MCP_HOST,
        config.MCP_PORT,
        config.DB_PATH or "keine DB (leerer Store)",
    )
    uvicorn.run(
        create_http_app(store),
        host=config.MCP_HOST,
        port=config.MCP_PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
