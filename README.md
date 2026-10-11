# GoodWe Monitor (Service)

Überwacht einen GoodWe-Wechselrichter im Netzwerk. Der Dienst sammelt die
Daten in einem Polling-Loop und stellt sie über einen **MCP-Server** als
zentrale Datenquelle bereit; WebGUI und andere Server holen sich ihre Daten
von dort. Die Historie wird in SQLite persistiert und übersteht Neustarts.

## Komponenten

| Komponente | Technik | Port (Standard) | Zweck |
|---|---|---|---|
| Poller-Loop | `asyncio` + `goodwe` | – | Fragt den Wechselrichter alle 60 s ab (langsamer Loop gegen Rate-Limits) |
| MCP-Server | `mcp` (Streamable HTTP, stateless) | `8001` (`0.0.0.0`) | Zentrale Datenquelle: Tools `get_all`, `get_status`, `get_data`, `get_settings`, `get_fields`, `get_history` |
| WebUI | Flask | `8080` (`0.0.0.0`) | Reiner Client: liefert HTML und ruft alle Daten per MCP-Tool-Call ab |
| API | FastAPI | `8000` (`0.0.0.0`) | Zusätzliche REST-API für andere Server, Doku unter `/docs` |

Alle Komponenten laufen in eigenen Threads innerhalb eines Prozesses und
teilen sich einen thread-sicheren [DataStore](datastore.py) (SQLite-Historie).

## Installation

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Start

```bash
./venv/bin/python main.py
```

Danach:

- WebUI: `http://<server-ip>:8080/`
- MCP-Server: `http://<server-ip>:8001/mcp`
- API: `http://<server-ip>:8000/` → Doku unter `http://<server-ip>:8000/docs`

## MCP-Server

Der MCP-Endpoint (`POST /mcp`) ist stateless mit JSON-Antworten und CORS –
die WebGUI ruft ihn direkt per `tools/call` auf. LLM-Clients können den
Server ebenfalls einbinden, z. B.:

```json
{"mcpServers": {
  "goodwe": {"url": "http://<server-ip>:8001/mcp"}
}}
```

Verfügbare Tools:

| Tool | Beschreibung |
|---|---|
| `get_all` | Kompletter Schnappschuss (Status, Gerät, Felder, Werte, Settings) |
| `get_status` | Verbindungsstatus, Zähler, Geräte-Info, letzte Aktualisierung |
| `get_data` | Aktuelle Runtime-Werte (`feld → Wert`) |
| `get_settings` | Aktuell unterstützte Wechselrichter-Settings |
| `get_fields` | Feld-Metadaten (Name, Einheit, Kategorie, aktueller Wert) |
| `get_history` | Historie; Args: `fields`, `minutes`, `max_points` |

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Beschreibung |
|---|---|---|
| `GOODWE_IP` | `192.168.1.105` | IP des Wechselrichters |
| `GOODWE_PORT` | `8899` | TCP-Port des Wechselrichters |
| `GOODWE_FAMILY` | leer | Protokoll-Familie (`ES`/`ET`/…); leer = automatische Erkennung per Discovery |
| `GOODWE_TIMEOUT` | `5` | Timeout pro Verbindungsversuch (s) |
| `GOODWE_RETRIES` | `3` | Anzahl Verbindungsversuche |
| `GOODWE_POLL_INTERVAL` | `60` | Abfrage-Intervall in Sekunden |
| `GOODWE_SETTINGS_INTERVAL` | `5` | Settings nur alle N Polls lesen (sie ändern sich selten) |
| `GOODWE_HISTORY_SIZE` | `1440` | Gespeicherte Messpunkte (1440 = 24 h bei 60 s) |
| `GOODWE_UI_HOST` / `GOODWE_UI_PORT` | `0.0.0.0` / `8080` | WebUI-Bindung |
| `GOODWE_API_HOST` / `GOODWE_API_PORT` | `0.0.0.0` / `8000` | API-Bindung |
| `GOODWE_MCP_HOST` / `GOODWE_MCP_PORT` | `0.0.0.0` / `8001` | MCP-Server-Bindung |
| `GOODWE_DB_PATH` | `goodwe_history.db` | SQLite-Datei für die persistente Historie |

## API-Endpunkte (FastAPI, optional)

| Endpunkt | Beschreibung |
|---|---|
| `GET /api/all` | Kompletter Schnappschuss (Status, Gerät, Felder, Werte, Settings) |
| `GET /api/status` | Verbindungsstatus, Zähler, Geräte-Info, letzte Aktualisierung |
| `GET /api/data` | Aktuelle Runtime-Werte (`feld → Wert`) |
| `GET /api/settings` | Aktuelle Wechselrichter-Settings |
| `GET /api/fields` | Feld-Metadaten (Name, Einheit, Kategorie, aktueller Wert) |
| `GET /api/history` | Historie, z. B. `?fields=battery_soc,ppv&minutes=60&max_points=400` |

CORS ist für API und MCP freigeschaltet.

## WebUI

- Reiner Client: lädt keine Daten über Flask, sondern ruft die MCP-Tools auf.
- Zeigt alle auslesbaren Werte (Runtime, Settings, Geräte-Info).
- Statistiken lassen sich über Checkboxen einzeln oder gruppenweise
  (PV, AC, Batterie, Netz, …) auswählen; die Auswahl wird im Browser gespeichert.
- „Standard“ wählt die Kernwerte (SOC, Batterie/PV/Netz/Haus-Leistung, …).
- Der Verlauf zeigt bis zu 24 h Historie für ein frei wählbares numerisches Feld.
- Ist der Wechselrichter nicht erreichbar, bleibt die Seite erreichbar und
  versucht jede Minute automatisch erneut.
- Die Historie liegt in SQLite – nach einem Neustart der Seite oder des
  Dienstes werden die Daten weiterhin geliefert.

## Umgang mit nicht unterstützten Settings

Manche GoodWe-Firmwares lehnen einzelne Settings ab (`ILLEGAL DATA ADDRESS`).
Die Verbindung zum Wechselrichter bleibt deshalb über die Polls hinweg offen:
die Bibliothek merkt sich abgelehnte Settings und fragt sie nicht erneut an.
Sie werden einmal pro Lauf im Log gemeldet und in der WebUI unter
„Wechselrichter-Settings“ als „abgelehnt“ angezeigt.

## Deploy auf den Produktions-Host

[deploy.sh](deploy.sh) kopiert die Projektdateien per rsync/SSH auf den
Remote-Host, installiert die Abhängigkeiten und richtet den Dienst als
systemd-Service ein (inkl. Stopp alter manueller Instanzen und Verifikation
aller Endpunkte):

```bash
./deploy.sh                 # Standard: root@100.103.72.61:/home/leon/goodwe-dashboard
./deploy.sh <host> <user> <remote-dir>   # abweichendes Ziel
```

Das Skript ist idempotent (mehrfach ausführbar) und erhält die auf dem
Remote konfigurierte Wechselrichter-IP (`GOODWE_IP`) beim Kopieren der
`config.py`. Ausgeschlossen werden `.venv`, `__pycache__` und `*.db` —
die persistierte Historie auf dem Remote bleibt erhalten.

Danach auf dem Remote:

```bash
systemctl status goodwe          # Status
journalctl -u goodwe -f          # Logs verfolgen
```

## Als systemd-Service (optional)

`/etc/systemd/system/goodwe.service`:

```ini
[Unit]
Description=GoodWe Monitor Service
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/Users/leon/goodwe
ExecStart=/Users/leon/goodwe/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
