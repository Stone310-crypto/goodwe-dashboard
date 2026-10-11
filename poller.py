"""Abfrage-Loop: pollt den Wechselrichter alle POLL_INTERVAL Sekunden.

Bewusst ein langsamer Loop (Standard: 60 s), um nicht in Rate-Limits
des Wechselrichters zu laufen.

Die Verbindung zum Wechselrichter bleibt über die Polls hinweg offen:
die goodwe-Bibliothek merkt sich dabei intern, welche Sensoren/Settings
die Firmware nicht unterstützt (ILLEGAL DATA ADDRESS), und fragt diese
nicht erneut an. Nur bei einem Fehler wird die Verbindung verworfen und
beim nächsten Poll neu aufgebaut.
"""

import asyncio
import logging
import time

import goodwe

import config
from datastore import DataStore

log = logging.getLogger("poller")


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


class Poller:
    def __init__(self, store: DataStore) -> None:
        self.store = store
        self.inverter = None
        self._poll_index = 0
        self._settings_cache: dict = {}
        self._unsupported_cache: list = []
        self._reported_unsupported: set = set()

    async def _connect(self):
        return await goodwe.connect(
            config.INVERTER_IP,
            port=config.INVERTER_PORT,
            family=config.INVERTER_FAMILY,
            timeout=config.INVERTER_TIMEOUT,
            retries=config.INVERTER_RETRIES,
        )

    async def poll_once(self) -> None:
        self.store.begin_poll()
        started = time.monotonic()
        try:
            if self.inverter is None:
                log.info(
                    "Verbinde mit Wechselrichter %s (Port %s) …",
                    config.INVERTER_IP,
                    config.INVERTER_PORT,
                )
                self.inverter = await self._connect()

            # Runtime-Werte sind Pflicht – ohne sie gilt der Poll als fehlgeschlagen
            runtime = await self.inverter.read_runtime_data()
            if not isinstance(runtime, dict):
                raise RuntimeError("read_runtime_data lieferte kein Dictionary")

            # Settings ändern sich selten → nur alle N Polls lesen (inkl. erstem)
            if self._poll_index % max(1, config.SETTINGS_INTERVAL) == 0:
                result = await self._read_settings()
                if result is not None:
                    self._settings_cache, self._unsupported_cache = result
            self._poll_index += 1
            unsupported = self._unsupported_cache

            device: dict = {}
            try:
                device = await self.inverter.read_device_info()
            except Exception as exc:
                log.warning("Geräte-Info konnte nicht gelesen werden: %s", exc)
            # ET: read_device_info() liefert None und setzt stattdessen Attribute
            if not isinstance(device, dict):
                device = self._device_info_from_attributes()

            sensors = ()
            try:
                sensors = self.inverter.sensors()
            except Exception as exc:
                log.warning("Sensor-Definitionen konnten nicht gelesen werden: %s", exc)

            fields = build_fields(runtime, sensors)

            # Nicht unterstützte Settings nur einmal pro Lauf melden
            new_unsupported = [u for u in unsupported if u not in self._reported_unsupported]
            if new_unsupported:
                log.warning(
                    "Settings von der Firmware abgelehnt (werden ignoriert): %s",
                    ", ".join(sorted(new_unsupported)),
                )
                self._reported_unsupported.update(new_unsupported)

            self.store.update(
                runtime=runtime,
                settings=self._settings_cache,
                device=device,
                fields=fields,
                unsupported_settings=sorted(unsupported),
            )
            log.info(
                "Poll OK: %d Runtime-Werte, %d Settings, %d Felder (%.1f s)",
                len(runtime),
                len(self._settings_cache),
                len(fields),
                time.monotonic() - started,
            )
        except Exception as exc:
            log.error("Poll fehlgeschlagen: %s", exc)
            # Verbindung verwerfen – beim nächsten Poll wird neu verbunden
            self.inverter = None
            self.store.set_error(str(exc))

    async def _read_settings(self) -> tuple[dict, list] | None:
        """Liest die Settings; nicht unterstützte Einträge werden aussortiert.

        Liefert None bei einem Lesefehler – der vorherige Cache bleibt dann erhalten.
        """
        try:
            raw = await self.inverter.read_settings_data()
        except Exception as exc:
            # Nicht kritisch: Settings werden nicht von allen Modellen unterstützt
            log.warning("Settings konnten nicht gelesen werden: %s", exc)
            return None
        if not isinstance(raw, dict):
            log.warning("read_settings_data lieferte kein Dictionary")
            return None
        settings = {k: v for k, v in raw.items() if v is not None}
        unsupported = sorted(k for k, v in raw.items() if v is None)
        return settings, unsupported

    # Attribute, die goodwe (ET/ES) bei read_device_info() am Inverter-Objekt
    # setzt, obwohl die Methode selbst None zurückliefert.
    _DEVICE_ATTRS = (
        "serial_number",
        "model_name",
        "firmware",
        "arm_firmware",
        "dsp1_version",
        "dsp2_version",
        "arm_version",
        "rated_power",
        "modbus_version",
        "ac_output_type",
    )

    def _device_info_from_attributes(self) -> dict:
        """Geräte-Info aus den Inverter-Attributen zusammenbauen."""
        return {
            attr: value
            for attr in self._DEVICE_ATTRS
            if (value := getattr(self.inverter, attr, None)) is not None
        }

    async def run(self) -> None:
        """Endlosschleife: sofortige erste Abfrage, danach alle POLL_INTERVAL Sekunden."""
        while True:
            loop = asyncio.get_running_loop()
            started = loop.time()
            await self.poll_once()
            elapsed = loop.time() - started
            await asyncio.sleep(max(1, config.POLL_INTERVAL - elapsed))


def run(store: DataStore) -> None:
    """Einstiegspunkt für den Poller-Thread."""
    asyncio.run(Poller(store).run())
