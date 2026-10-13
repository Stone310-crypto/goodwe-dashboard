"""Abfrage-Loop: pollt den Wechselrichter alle POLL_INTERVAL Sekunden
(Standard: 10 s).

Settings werden nur alle N Polls gelesen (GOODWE_SETTINGS_INTERVAL), da sie
sich selten ändern – das hält die Last pro Abfrage klein.

Die Verbindung zum Wechselrichter bleibt über die Polls hinweg offen:
die goodwe-Bibliothek merkt sich dabei intern, welche Sensoren/Settings
die Firmware nicht unterstützt (ILLEGAL DATA ADDRESS), und fragt diese
nicht erneut an. Nur bei einem Fehler wird die Verbindung verworfen und
beim nächsten Poll neu aufgebaut.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import goodwe

import config
import user_config
from datastore import DataStore
from metrics import compute_metrics

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
        self._warning: dict = {
            "active": False,
            "started_at": None,
            "cleared_at": None,
            "trigger_count": 0,
            "current_w": None,
            "threshold_w": 0.0,
        }

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

            # Benutzer-Einstellungen + Batterie-/Verbrauchskennzahlen
            uc = user_config.load()
            metrics = compute_metrics(runtime, uc)
            warning = self._track_warning(runtime, uc)

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
                metrics=metrics,
                warning=warning,
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

    def _track_warning(self, runtime: dict, uc: dict) -> dict:
        """Überwacht den Hausverbrauch gegen die Warnschwelle aus config.toml."""
        house = runtime.get("house_consumption")
        threshold = uc["warning_threshold_w"]
        w = self._warning
        w["threshold_w"] = threshold
        w["current_w"] = house if isinstance(house, (int, float)) else None

        over = (
            isinstance(house, (int, float))
            and threshold > 0
            and house > threshold
        )
        if over and not w["active"]:
            w["active"] = True
            w["started_at"] = datetime.now(timezone.utc).isoformat()
            w["trigger_count"] = int(w.get("trigger_count") or 0) + 1
            log.warning(
                "Hoher Verbrauch: %.0f W überschreitet die Schwelle von %.0f W!",
                house,
                threshold,
            )
        elif not over and w["active"]:
            w["active"] = False
            w["cleared_at"] = datetime.now(timezone.utc).isoformat()
            log.info("Verbrauch wieder unter der Schwelle (%.0f W)", threshold)
        return w

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
        """Endlosschleife: Abfrage im Intervall aus config.toml (dynamisch)."""
        while True:
            loop = asyncio.get_running_loop()
            interval = float(
                user_config.load().get("poll_interval_seconds", config.POLL_INTERVAL)
            )
            self.store.set_poll_interval(interval)
            started = loop.time()
            await self.poll_once()
            elapsed = loop.time() - started
            await asyncio.sleep(max(1, interval - elapsed))


def run(store: DataStore) -> None:
    """Einstiegspunkt für den Poller-Thread."""
    asyncio.run(Poller(store).run())
