"""Eigenständiger WebUI-Prozess (systemd: goodwe-webui.service).

Liefert nur die HTML-Seite aus; alle Daten holt sich die Seite per
MCP-Tool-Call vom MCP-Server (goodwe-mcp.service).
"""

import logging

import config
import webui

log = logging.getLogger("webui")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    log.info("WebUI startet auf %s:%s (MCP-Port %s)", config.UI_HOST, config.UI_PORT, config.MCP_PORT)
    webui.run(config.UI_HOST, config.UI_PORT)


if __name__ == "__main__":
    main()
