#!/bin/bash
# =============================================================================
# PlayTrack — Aggiornamento rapido codice (dopo git pull)
# Uso: sudo ./update.sh
# =============================================================================
set -e

GREEN='\033[0;32m'; NC='\033[0m'
ok() { echo -e "${GREEN}✓ $1${NC}"; }

[ "$EUID" -ne 0 ] && { echo "Esegui con: sudo ./update.sh"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Aggiornamento codice PlayTrack..."

cp "$SCRIPT_DIR/playtrack/__init__.py" /opt/playtrack/__init__.py
cp "$SCRIPT_DIR/playtrack/agent.py"    /opt/playtrack/agent.py
cp "$SCRIPT_DIR/playtrack/uploader.py" /opt/playtrack/uploader.py
chown playtrack:playtrack /opt/playtrack/__init__.py \
                          /opt/playtrack/agent.py \
                          /opt/playtrack/uploader.py

#sudo -u playtrack /opt/playtrack/venv/bin/pip install --quiet -r "$SCRIPT_DIR/requirements.txt"

systemctl restart playtrack
ok "Codice aggiornato e daemon riavviato"
echo ""
echo "  Log live: sudo journalctl -u playtrack -f"
