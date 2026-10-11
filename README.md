# GoodWe Monitor (Service)

Überwacht einen GoodWe-Wechselrichter im Netzwerk und stellt die Daten als
WebUI und HTTP-API bereit.

## Komponenten

| Komponente | Technik | Port (Standard) | Zweck |
|---|---|---|---|
| Poller-Loop | `asyncio` + `goodwe` | – | Fragt den Wechselrichter alle 60 s ab (langsamer Loop gegen Rate-Limits) |
| WebUI | Flask | `8080` (`0.0.0.0`) | Statusseite, Statistiken einzeln/gruppenweise auswählbar, Verlaufs-Chart |
| API | FastAPI | `8000` (`0.0.0.0`) | HTTP-API für andere Server, interaktive Doku unter `/docs` |

Alle drei laufen in eigenen Threads innerhalb eines Prozesses und teilen sich
einen thread-sicheren [DataStore](datastore.py).

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
- API: `http://<server-ip>:8000/` → Doku unter `http://<server-ip>:8000/docs`

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Beschreibung |
|---|---|---|
| `GOODWE_IP` | `192.168.1.108` | IP des Wechselrichters |
| `GOODWE_PORT` | `8899` | TCP-Port des Wechselrichters |
| `GOODWE_FAMILY` | leer | Protokoll-Familie (`ES`/`ET`/…); leer = automatische Erkennung per Discovery |
| `GOODWE_TIMEOUT` | `5` | Timeout pro Verbindungsversuch (s) |
| `GOODWE_RETRIES` | `3` | Anzahl Verbindungsversuche |
| `GOODWE_POLL_INTERVAL` | `60` | Abfrage-Intervall in Sekunden |
| `GOODWE_HISTORY_SIZE` | `1440` | Gespeicherte Messpunkte (1440 = 24 h bei 60 s) |
| `GOODWE_UI_HOST` / `GOODWE_UI_PORT` | `0.0.0.0` / `8080` | WebUI-Bindung |
| `GOODWE_API_HOST` / `GOODWE_API_PORT` | `0.0.0.0` / `8000` | API-Bindung |

## API-Endpunkte

| Endpunkt | Beschreibung |
|---|---|
| `GET /api/all` | Kompletter Schnappschuss (Status, Gerät, Felder, Werte, Settings) |
| `GET /api/status` | Verbindungsstatus, Zähler, Geräte-Info, letzte Aktualisierung |
| `GET /api/data` | Aktuelle Runtime-Werte (`feld → Wert`) |
| `GET /api/settings` | Aktuelle Wechselrichter-Settings |
| `GET /api/fields` | Feld-Metadaten (Name, Einheit, Kategorie, aktueller Wert) |
| `GET /api/history` | Historie, z. B. `?fields=battery_soc,ppv&minutes=60&max_points=400` |

Beispiel:

```bash
curl "http://<server-ip>:8000/api/history?fields=battery_soc&minutes=120"
```

CORS ist für die API freigeschaltet, damit andere Server sie direkt abfragen können.

## WebUI

- Zeigt alle auslesbaren Werte (Runtime, Settings, Geräte-Info).
- Statistiken lassen sich über Checkboxen einzeln oder gruppenweise
  (PV, AC, Batterie, Netz, …) auswählen; die Auswahl wird im Browser gespeichert.
- „Standard“ wählt die Kernwerte (SOC, Batterie/PV/Netz/Haus-Leistung, …).
- Der Verlauf zeigt bis zu 24 h Historie für ein frei wählbares numerisches Feld.
- Ist der Wechselrichter nicht erreichbar, bleibt die Seite erreichbar und
  versucht jede Minute automatisch erneut.

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
