#!/usr/bin/env bash
# Deploy-Script: kopiert den GoodWe-Service auf den Remote-Host und
# installiert ihn dort als drei systemd-Services:
#   goodwe.service        – Kern: Poller + FastAPI-API
#   goodwe-mcp.service    – MCP-Server (liest Daten aus der SQLite-DB)
#   goodwe-webui.service  – WebUI (Flask, ruft Daten über MCP ab)
#
# Aufruf:  ./deploy.sh [host] [user] [remote-dir]
# Standard: host=100.103.72.61  user=root  remote-dir=/home/leon/goodwe-dashboard
set -euo pipefail

REMOTE_HOST="${1:-100.103.72.61}"
REMOTE_USER="${2:-root}"
REMOTE_DIR="${3:-/home/leon/goodwe-dashboard}"
SERVICES="goodwe goodwe-mcp goodwe-webui"

SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
RSYNC="rsync -az"

# Projektverzeichnis dieses Skripts
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Deploy nach ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"

# ---------------------------------------------------------------- 1. Kopieren
echo "==> Kopiere Projektdateien (ohne venv, __pycache__, *.db*) …"

# IP aus der Remote-config vor dem Überschreiben sichern, damit die
# lokal geänderte config.py die funktionierende Wechselrichter-IP nicht kaputtmacht.
REMOTE_IP="$(${SSH} "${REMOTE_USER}@${REMOTE_HOST}" \
  "sed -n 's/.*INVERTER_IP = _str(\"GOODWE_IP\", \"\\([^\"]*\\)\".*/\\1/p' '${REMOTE_DIR}/config.py' 2>/dev/null || true")"

$RSYNC \
  --exclude '.venv/' --exclude 'venv/' --exclude '__pycache__/' \
  --exclude '*.pyc' --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
  --exclude 'config.toml' --exclude '.git/' --exclude '*.log' \
  "$SRC_DIR/" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

# config.toml (WebUI-Einstellungen) wird bewusst NICHT überschrieben –
# nur anlegen, wenn es auf dem Remote noch nicht existiert.
if ! ${SSH} "${REMOTE_USER}@${REMOTE_HOST}" "[ -f '${REMOTE_DIR}/config.toml' ]"; then
  echo "==> Lege config.toml mit Standardwerten an"
  scp -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    "$SRC_DIR/config.toml" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/config.toml"
else
  # Neue Schlüssel aus neueren Versionen ergänzen, ohne vorhandene Werte zu ändern
  ${SSH} "${REMOTE_USER}@${REMOTE_HOST}" "grep -q '^poll_interval_seconds' '${REMOTE_DIR}/config.toml' || echo 'poll_interval_seconds = 10' >> '${REMOTE_DIR}/config.toml'"
fi

# Remote-IP (falls gefunden) in die neue config.py zurückschreiben
if [ -n "${REMOTE_IP}" ]; then
  echo "==> Erhalte Wechselrichter-IP ${REMOTE_IP} in der Remote-config.py"
  ${SSH} "${REMOTE_USER}@${REMOTE_HOST}" \
    "sed -i 's|INVERTER_IP = _str(\"GOODWE_IP\", \"[^\"]*\")|INVERTER_IP = _str(\"GOODWE_IP\", \"${REMOTE_IP}\")|' '${REMOTE_DIR}/config.py'"
fi

# ------------------------------------------------- 2. Abhängigkeiten installieren
echo "==> Installiere Python-Abhängigkeiten (venv: ${REMOTE_DIR}/.venv) …"
${SSH} "${REMOTE_USER}@${REMOTE_HOST}" "
  if [ ! -x '${REMOTE_DIR}/.venv/bin/python' ]; then
    python3 -m venv '${REMOTE_DIR}/.venv'
  fi
  '${REMOTE_DIR}/.venv/bin/pip' install -q -r '${REMOTE_DIR}/requirements.txt'
"

# ----------------------------------------------------- 3. systemd-Services einrichten
echo "==> Installiere systemd-Services (${SERVICES}) …"
for svc in ${SERVICES}; do
  ${SSH} "${REMOTE_USER}@${REMOTE_HOST}" "cp '${REMOTE_DIR}/${svc}.service' '/etc/systemd/system/${svc}.service'"
done
${SSH} "${REMOTE_USER}@${REMOTE_HOST}" "systemctl daemon-reload && systemctl enable ${SERVICES}"

# ----------------------------------------------- 4. Alte manuelle Instanz stoppen
echo "==> Stoppe alte manuelle Instanz (falls vorhanden) …"
${SSH} "${REMOTE_USER}@${REMOTE_HOST}" 'bash -s' <<REMOTE
# Nur stoppen, wenn der Port von einem Prozess gehalten wird, der NICHT
# zu einem der systemd-Services gehört.
MAIN_PIDS="\$(for s in ${SERVICES}; do systemctl show -p MainPID --value "\$s" 2>/dev/null || true; done)"
for port in 8080 8000 8001; do
  PORT_PID="\$(ss -ltnp 2>/dev/null | grep ":\${port}" | grep -oP 'pid=\K[0-9]+' | head -1 || echo 0)"
  if [ -n "\${PORT_PID}" ] && [ "\${PORT_PID}" != "0" ] && ! echo "\${MAIN_PIDS}" | grep -qx "\${PORT_PID}"; then
    echo "Stoppe alte Instanz auf Port \${port} (PID \${PORT_PID})"
    kill "\${PORT_PID}" 2>/dev/null || true
    sleep 1
  fi
done
sleep 2
REMOTE

# ------------------------------------------------------------- 5. Services starten
echo "==> Starte Services (Reihenfolge: Kern -> MCP -> WebUI) …"
${SSH} "${REMOTE_USER}@${REMOTE_HOST}" "systemctl restart ${SERVICES}"
sleep 5

# -------------------------------------------------------------------- 6. Prüfen
echo "==> Verifikation …"
${SSH} "${REMOTE_USER}@${REMOTE_HOST}" 'bash -s' <<REMOTE
echo '--- systemd ---'
for s in ${SERVICES}; do
  echo "\$s: \$(systemctl is-active \$s) / \$(systemctl is-enabled \$s)"
done
echo '--- Ports ---'
ss -ltn | grep -E ':8080|:8000|:8001' || true
echo '--- Endpunkte ---'
curl -s --max-time 5 -o /dev/null -w 'WebUI:  HTTP %{http_code}\n' http://127.0.0.1:8080/ || echo 'WebUI:  FEHLER'
curl -s --max-time 5 -o /dev/null -w 'API:    HTTP %{http_code}\n' http://127.0.0.1:8000/api/status || echo 'API:    FEHLER'
curl -s --max-time 5 -X POST -o /dev/null -w 'MCP:    HTTP %{http_code}\n' \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  http://127.0.0.1:8001/mcp || echo 'MCP:    FEHLER'
REMOTE

echo "==> Fertig. Logs: ssh ${REMOTE_USER}@${REMOTE_HOST} journalctl -u goodwe -u goodwe-mcp -u goodwe-webui -f"
