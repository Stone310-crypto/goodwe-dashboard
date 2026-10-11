"""Abfrage-Loop: pollt den Wechselrichter alle POLL_INTERVAL Sekunden.

Bewusst ein langsamer Loop (Standard: 60 s), um nicht in Rate-Limits
des Wechselrichters zu laufen.
"""

import asyncio
import logging

import goodwe

import config
from datastore import DataStore

log = logging.getLogger("goodwe.poller")


def build_fields(runtime, sensors) -> list:
    """Feld-Metadaten: Runtime-Werte angereichert mit Sensor-Definitionen."""
    meta = {}
    for s in sensors or ():
        meta[s.id_] = {
            "name": s.name,
            "unit": s.unit,
            "kind": s.kind.name if getattr(s, "kind", None) else None,
        }

    fields = []
    for key, value in runtime.items():
        m = meta.get(key, {})
        fields.append(
            {
                "id": key,
                "name": m.get("name") or key,
                "unit": m.get("unit") or "",
                "kind": m.get("kind"),
                "value": value,
                "numeric": isinstance(value, (int, float)) and not isinstance(value, bool),
            }
        )
    # Sensoren ohne aktuellen Wert (z. B. nicht unterstützt) auch anzeigen
    for key, m in meta.items():
        if key not in runtime:
            fields.append(
                {
                    "id": key,
                    "name": m.get("name") or key,
                    "unit": m.get("unit") or "",
                    "kind": m.get("kind"),
                    "value": None,
                    "numeric": True,
                }
            )
    fields.sort(key=lambda f: f["id"])
    return fields


async def _poll_once(store: DataStore) -> None:
    store.begin_poll()
    try:
        inverter = await goodwe.connect(
            config.INVERTER_IP,
            port=config.INVERTER_PORT,
            family=config.INVERTER_FAMILY,
            timeout=config.INVERTER_TIMEOUT,
            retries=config.INVERTER_RETRIES,
        )

        runtime = await inverter.read_runtime_data()

        settings = {}
        try:
            settings = await inverter.read_settings_data()
        except Exception as exc:  # Settings werden nicht von allen Modellen unterstützt
            log.warning("Settings konnten nicht gelesen werden: %s", exc)

        device = {}
        try:
            device = await inverter.read_device_info()
        except Exception as exc:
            log.warning("Geräte-Info konnte nicht gelesen werden: %s", exc)

        sensors = ()
        try:
            sensors = inverter.sensors()
        except Exception as exc:
            log.warning("Sensor-Definitionen konnten nicht gelesen werden: %s", exc)

        fields = build_fields(runtime, sensors)
        store.update(runtime=runtime, settings=settings, device=device, fields=fields)
        log.info(
            "Poll OK: %d Runtime-Werte, %d Settings, %d Felder",
            len(runtime),
            len(settings),
            len(fields),
        )
    except Exception as exc:
        log.error("Poll fehlgeschlagen: %s", exc)
        store.set_error(str(exc))


async def poll_loop(store: DataStore) -> None:
    """Endlosschleife: sofortige erste Abfrage, danach alle POLL_INTERVAL Sekunden."""
    while True:
        started = asyncio.get_running_loop().time()
        await _poll_once(store)
        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(1, config.POLL_INTERVAL - elapsed))


def run(store: DataStore) -> None:
    """Einstiegspunkt für den Poller-Thread."""
    asyncio.run(poll_loop(store))
