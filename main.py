"""GoodWe Monitor als Service.

Startet drei Komponenten:
  1. Poller-Loop  – fragt den Wechselrichter jede Minute ab (gegen Rate-Limits)
  2. WebUI (Flask) – Statusseite, Statistiken auswählbar
  3. API (FastAPI) – HTTP-API, damit andere Server die Daten abfragen können

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
from webui import run as run_webui

log = logging.getLogger("goodwe.service")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    store = DataStore(
        history_size=config.HISTORY_SIZE,
        poll_interval=config.POLL_INTERVAL,
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
    ]

    log.info(
        "GoodWe-Service startet: IP=%s, Poll-Intervall=%ss, "
        "WebUI auf %s:%s, API auf %s:%s (Doku: /docs)",
        config.INVERTER_IP,
        config.POLL_INTERVAL,
        config.UI_HOST,
        config.UI_PORT,
        config.API_HOST,
        config.API_PORT,
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