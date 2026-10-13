"""GoodWe Monitor – Kern-Service (systemd: goodwe.service).

Startet zwei Komponenten:
  1. Poller-Loop  – fragt den Wechselrichter jede Minute ab (gegen Rate-Limits)
  2. API (FastAPI) – HTTP-API, damit andere Server die Daten abfragen können

Der Poller persistiert Snapshot und Historie in SQLite; daraus lesen die
eigenständigen Services goodwe-mcp.service und goodwe-webui.service.

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

log = logging.getLogger("core")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    # Interne Tracebacks der goodwe-Bibliothek ausblenden (z. B. bei
    # nicht unterstützten Settings) – der Poller protokolliert kompakt selbst.
    logging.getLogger("goodwe").setLevel(logging.CRITICAL)

    store = DataStore(
        history_size=config.HISTORY_SIZE,
        poll_interval=config.POLL_INTERVAL,
        db_path=config.DB_PATH or None,
        energy_retention_days=config.ENERGY_RETENTION_DAYS,
    )

    threads = [
        threading.Thread(target=poller.run, args=(store,), daemon=True, name="poller"),
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
        "Kern-Service startet: IP=%s, Poll-Intervall=%ss, API auf %s:%s, DB: %s",
        config.INVERTER_IP,
        config.POLL_INTERVAL,
        config.API_HOST,
        config.API_PORT,
        config.DB_PATH or "–",
    )

    for thread in threads:
        thread.start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("Beende Kern-Service …")


if __name__ == "__main__":
    main()