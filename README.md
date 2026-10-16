# GoodWe Monitor (Service)

Überwacht einen GoodWe-Wechselrichter im Netzwerk. Der Dienst sammelt die
Daten in einem Polling-Loop und stellt sie über einen **MCP-Server** als
zentrale Datenquelle bereit; WebGUI und andere Server holen sich ihre Daten
von dort. Die Historie wird in SQLite persistiert und übersteht Neustarts.

## Komponenten

Drei getrennte systemd-Services:

| Service | Technik | Port (Standard) | Zweck |
|---|---|---|---|
| `goodwe.service` | Poller (`asyncio` + `goodwe`) + FastAPI | `8000` | Fragt den Wechselrichter alle 10 s ab, schreibt Snapshot & Historie in SQLite, REST-API |
| `goodwe-mcp.service` | `mcp` (Streamable HTTP, stateless) | `8001` | MCP-Server: liest bei jedem Tool-Call frisch aus SQLite; Tools `get_all`, `get_status`, `get_data`, `get_settings`, `get_fields`, `get_history` |
| `goodwe-webui.service` | Flask | `8080` | Reiner Client: liefert HTML und ruft alle Daten per MCP-Tool-Call ab |

Der Datenaustausch läuft über die SQLite-Datei (`GOODWE_DB_PATH`, Standard
`goodwe_history.db`): Der Kern schreibt, MCP-Server und WebUI lesen. Dadurch
liefert der MCP-Server Daten auch dann weiter, wenn der Kern neu startet;
liegen die Daten länger als 3 Poll-Intervalle zurück, wird der Status als
„Offline – Daten veraltet“ gemeldet.

## Installation

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Start (lokal, ohne systemd)

Alle drei Komponenten einzeln starten:

```bash
./venv/bin/python main.py        # Kern: Poller + API (Port 8000)
./venv/bin/python mcp_main.py    # MCP-Server (Port 8001)
./venv/bin/python webui_main.py  # WebUI (Port 8080)
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
| `get_metrics` | Batterie-Kennzahlen (Ladezustand, kWh, Lade-/Entladezeit, Autonomie) + Verbrauchswarnung |
| `get_pv_stats` | PV-Tagesprofil: Ø-Leistung je Stunde + beste Stunde |
| `get_autarky` | Autarkiegrad: (Verbrauch − Netzbezug) / Verbrauch; Args: `period` (today/yesterday/week/month/year) |
| `get_energy_balance` | Vollständige Energie-Bilanz (PV, Verbrauch, Netz, Batterie, Autarkie, Eigenverbrauch); Args: `period` |
| `get_daily_energy` | Stromverbrauch je Tag (plus Produktion, Netz, Batterie) für die letzten N Tage; Args: `days` (1–400) |
| `get_daily_snapshots` | Final gespeicherte Tageswerte (23:59-Festschreibung) für den Tagesvergleich; Args: `days` |
| `get_hourly_energy` | Je Stunde gespeicherte Erzeugung/Verbrauch eines Tages; Args: `date` (YYYY-MM-DD) |
| `get_config` | Benutzer-Einstellungen aus `config.toml` |
| `update_config` | Einstellungen ändern; Args: `battery_capacity_kwh`, `warning_threshold_w`, `poll_interval_seconds` |

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Beschreibung |
|---|---|---|
| `GOODWE_IP` | `192.168.1.105` | IP des Wechselrichters |
| `GOODWE_PORT` | `8899` | TCP-Port des Wechselrichters |
| `GOODWE_FAMILY` | leer | Protokoll-Familie (`ES`/`ET`/…); leer = automatische Erkennung per Discovery |
| `GOODWE_TIMEOUT` | `5` | Timeout pro Verbindungsversuch (s) |
| `GOODWE_RETRIES` | `3` | Anzahl Verbindungsversuche |
| `GOODWE_POLL_INTERVAL` | `10` | Abfrage-Intervall in Sekunden |
| `GOODWE_SETTINGS_INTERVAL` | `30` | Settings nur alle N Polls lesen (30 = alle 5 Minuten bei 10 s) |
| `GOODWE_HISTORY_SIZE` | `8640` | Gespeicherte Messpunkte (8640 = 24 h bei 10 s) |
| `GOODWE_ENERGY_RETENTION_DAYS` | `400` | Aufbewahrung der Energie-Deltas für Bilanz/Autarkie |
| `GOODWE_UI_HOST` / `GOODWE_UI_PORT` | `0.0.0.0` / `8080` | WebUI-Bindung |
| `GOODWE_API_HOST` / `GOODWE_API_PORT` | `0.0.0.0` / `8000` | API-Bindung |
| `GOODWE_MCP_HOST` / `GOODWE_MCP_PORT` | `0.0.0.0` / `8001` | MCP-Server-Bindung |
| `GOODWE_DB_PATH` | `goodwe_history.db` | SQLite-Datei für die persistente Historie |
| `GOODWE_CONFIG_PATH` | `config.toml` | Benutzer-Einstellungen (WebUI) |

## API-Endpunkte (FastAPI, optional)

| Endpunkt | Beschreibung |
|---|---|
| `GET /api/all` | Kompletter Schnappschuss (Status, Gerät, Felder, Werte, Settings) |
| `GET /api/status` | Verbindungsstatus, Zähler, Geräte-Info, letzte Aktualisierung |
| `GET /api/data` | Aktuelle Runtime-Werte (`feld → Wert`) |
| `GET /api/settings` | Aktuelle Wechselrichter-Settings |
| `GET /api/fields` | Feld-Metadaten (Name, Einheit, Kategorie, aktueller Wert) |
| `GET /api/history` | Historie, z. B. `?fields=battery_soc,ppv&minutes=60&max_points=400` |
| `GET /api/autarky` | Autarkiegrad, z. B. `?period=today` |
| `GET /api/energy_balance` | Energie-Bilanz, z. B. `?period=week` |
| `GET /api/daily_energy` | Stromverbrauch je Tag, z. B. `?days=14` |
| `GET /api/daily_snapshots` | Final gespeicherte Tageswerte, z. B. `?days=30` |
| `GET /api/hourly_energy` | Erzeugung/Verbrauch je Stunde, z. B. `?date=2026-10-15` |

CORS ist für API und MCP freigeschaltet.

## WebUI

- Reiner Client: lädt keine Daten über Flask, sondern ruft die MCP-Tools auf.
- Zeigt alle auslesbaren Werte (Runtime, Settings, Geräte-Info).
- **Batterie & Verbrauch**: Ladezustand, gespeicherte/verbleibende kWh,
  Ladeleistung mit Restzeit bis voll, Entladeleistung mit Restzeit bis leer,
  aktueller Hausverbrauch, Netto-Verbrauch (Haus − PV) und Warnschwelle.
- **„Strom reicht noch“**: Reichweite des Speichers in Stunden/Minuten beim
  aktuellen Netto-Verbrauch; deckt die PV den Verbrauch, wird „∞ (PV deckt)“
  angezeigt.
- **PV-Produktion (Ø je Stunde)**: Balkendiagramm der durchschnittlichen
  PV-Leistung pro Stunde, kumuliert über alle Tage (persistent in SQLite) –
  inkl. „Beste Zeit“ (Stunde mit der höchsten Ø-Produktion).
- **Energie-Bilanz**: PV-Produktion, Verbrauch, Netzbezug, Einspeisung,
  Batterie laden/entladen sowie Autarkie- und Eigenverbrauchsquote – für
  heute, diese Woche, diesen Monat oder dieses Jahr. Grundlage sind die
  Energiezähler des Wechselrichters (`e_day`, `e_day_imp`, `e_day_exp`,
  `e_load_day`, `e_bat_*_day`), deren Deltas pro Poll in SQLite persistiert
  werden (Tabelle `energy_delta`, Retention 400 Tage). Fehlt der Zähler
  (andere Modelle), greift die Trapez-Integration der Leistungswerte.
  Antworten sind als Schätzung markiert und enthalten die Datenvollständigkeit
  (`data_completeness`), damit Offline-Lücken sichtbar bleiben.
- **Live-Netzbezug**: aktuelle Netz-Leistung mit Richtung
  (`grid_in_out_label`: Importing/Exporting) in den Batterie-Karten.
- **Stromverbrauch pro Tag**: Balkendiagramm (Verbrauch violett, PV-Produktion
  gelb) für die letzten 7/14/30 Tage. Heute stammt aus den Live-Zählern des
  Wechselrichters, vorherige Tage aus den pro Poll persistierten
  Zähler-Deltas; Tage mit Datenlücken werden blass markiert.
- **Final gespeicherte Tageswerte**: Jede abgelaufene Stunde wird als
  Stundensnapshot festgeschrieben (Tabelle `energy_hourly`), und um 23:59
  bzw. direkt nach Tageswechsel wird der endgültige Tageswert in
  `energy_daily_final` gespeichert – ideal zum Vergleich zwischen Tagen.
  Über die WebUI („Erzeugung & Verbrauch je Stunde“, Tag auswählbar) und
  über `get_daily_snapshots`/`get_hourly_energy` abrufbar.
- **Verbrauchswarnung**: überschreitet der Hausverbrauch die Schwelle,
  erscheint ein Warnbanner („⚡ Hoher Verbrauch …“); im Log wird die
  Überschreitung protokolliert.
- **Einstellungen** (WebUI-Formular): Batteriekapazität (kWh), Warnschwelle (W)
  und Abfrage-Intervall (s) werden in [config.toml](config.toml) gespeichert
  und wirken sofort – ohne Neustart. So kann der Poller z. B. auf 2 s
  (quasi Echtzeit) herunter- oder auf 60 s heraufgestellt werden.
- Statistiken lassen sich über Checkboxen einzeln oder gruppenweise
  (PV, AC, Batterie, Netz, …) auswählen; die Auswahl wird im Browser gespeichert.
- „Standard“ wählt die Kernwerte (SOC, Batterie/PV/Netz/Haus-Leistung, …).
- Der Verlauf zeigt bis zu 24 h Historie für ein frei wählbares numerisches Feld.
- Ist der Wechselrichter nicht erreichbar, bleibt die Seite erreichbar und
  versucht jede Minute automatisch erneut.
- Die Historie liegt in SQLite – nach einem Neustart der Seite oder des
  Dienstes werden die Daten weiterhin geliefert.

## Benutzer-Einstellungen (config.toml)

```toml
battery_capacity_kwh = 10.0   # Batteriekapazität in kWh
warning_threshold_w = 2000    # Warnung ab dieser Verbrauchsleistung (W)
poll_interval_seconds = 10    # Abfrage-Intervall (2–600 s)
```

Die Datei wird über die WebUI (Abschnitt „Einstellungen“) oder die MCP-Tools
`get_config`/`update_config` geändert. Alle Dienste lesen die Werte bei jeder
Abfrage frisch ein – das Abfrage-Intervall gilt für den Poller des
Kern-Services und kann im laufenden Betrieb angepasst werden (auch in
Richtung Echtzeit, min. 2 s). `config.toml` wird beim Deploy bewusst
nicht überschrieben, damit Remote-Anpassungen erhalten bleiben.

## Umgang mit nicht unterstützten Settings

Manche GoodWe-Firmwares lehnen einzelne Settings ab (`ILLEGAL DATA ADDRESS`).
Die Verbindung zum Wechselrichter bleibt deshalb über die Polls hinweg offen:
die Bibliothek merkt sich abgelehnte Settings und fragt sie nicht erneut an.
Sie werden einmal pro Lauf im Log gemeldet und in der WebUI unter
„Wechselrichter-Settings“ als „abgelehnt“ angezeigt.

## Deploy auf den Produktions-Host

[deploy.sh](deploy.sh) kopiert die Projektdateien per rsync/SSH auf den
Remote-Host, installiert die Abhängigkeiten und richtet alle drei Dienste
als systemd-Services ein (`goodwe`, `goodwe-mcp`, `goodwe-webui` – inkl.
Stopp alter manueller Instanzen und Verifikation aller Endpunkte):

```bash
./deploy.sh                 # Standard: root@100.103.72.61:/home/leon/goodwe-dashboard
./deploy.sh <host> <user> <remote-dir>   # abweichendes Ziel
```

Das Skript ist idempotent (mehrfach ausführbar) und erhält die auf dem
Remote konfigurierte Wechselrichter-IP (`GOODWE_IP`) beim Kopieren der
`config.py`. Ausgeschlossen werden `.venv`, `__pycache__` und `*.db*` —
die persistierte Historie auf dem Remote bleibt erhalten.

Danach auf dem Remote:

```bash
systemctl status goodwe goodwe-mcp goodwe-webui   # Status aller Services
journalctl -u goodwe -u goodwe-mcp -u goodwe-webui -f   # Logs verfolgen
```

## Als systemd-Service (optional)

Die drei Unit-Dateien liegen im Projekt:
[goodwe.service](goodwe.service), [goodwe-mcp.service](goodwe-mcp.service),
[goodwe-webui.service](goodwe-webui.service) – einfach nach
`/etc/systemd/system/` kopieren, dann `systemctl daemon-reload` und
`systemctl enable --now goodwe goodwe-mcp goodwe-webui`.
Das Deploy-Script übernimmt das automatisch.
