#!/bin/bash
# =============================================================================
# PlayTrack — Setup automatico su nuovo RPi
# Uso: sudo ./setup.sh
# =============================================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
err()  { echo -e "${RED}✗ $1${NC}"; exit 1; }
ask()  { echo -e "\n${YELLOW}→ $1${NC}"; }

[ "$EUID" -ne 0 ] && err "Esegui con: sudo ./setup.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "============================================"
echo "   PlayTrack — Setup RPi"
echo "============================================"
echo ""

# ---------------------------------------------------------------------------
# Parametri da configurare manualmente per ogni RPi
# ---------------------------------------------------------------------------
# Modifica questi valori prima di eseguire, oppure inseriscili a runtime.
# CAMERA_ID:    cam-a  (RPi lato A del campo)
#               cam-b  (RPi lato B del campo)
# DEVICE_NAME:  playtrack-campo1-cam-a  (nome leggibile, unico per device)
# FIELD_ID:     campo-1  (ID del campo su Firestore)
# FIREBASE_PROJECT_ID:  playtrack-xxxxx  (dalla Firebase Console)
# ---------------------------------------------------------------------------

ask "CAMERA_ID (es: cam-a o cam-b):"; read -r CAMERA_ID
ask "DEVICE_NAME (es: playtrack-campo1-cam-a):"; read -r DEVICE_NAME
ask "FIELD_ID (es: campo-1):"; read -r FIELD_ID
ask "FIREBASE_PROJECT_ID (es: playtrack-af614):"; read -r FIREBASE_PROJECT_ID

[ -z "$CAMERA_ID" ]           && err "CAMERA_ID non può essere vuoto"
[ -z "$DEVICE_NAME" ]         && err "DEVICE_NAME non può essere vuoto"
[ -z "$FIELD_ID" ]            && err "FIELD_ID non può essere vuoto"
[ -z "$FIREBASE_PROJECT_ID" ] && err "FIREBASE_PROJECT_ID non può essere vuoto"

echo ""
echo "--- Riepilogo configurazione ---"
echo "  CAMERA_ID          : $CAMERA_ID"
echo "  DEVICE_NAME        : $DEVICE_NAME"
echo "  FIELD_ID           : $FIELD_ID"
echo "  FIREBASE_PROJECT_ID: $FIREBASE_PROJECT_ID"
echo ""
ask "Confermi? (y/n):"; read -r CONFIRM
[ "$CONFIRM" != "y" ] && err "Setup annullato"

# ---------------------------------------------------------------------------
# [1/6] Dipendenze di sistema
# ---------------------------------------------------------------------------
echo ""
echo "[1/6] Installazione dipendenze di sistema..."
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv ffmpeg sqlite3 curl git
ok "Dipendenze installate"

# ---------------------------------------------------------------------------
# [2/6] Utente e cartelle
# ---------------------------------------------------------------------------
echo "[2/6] Creazione utente e cartelle..."
if ! id "playtrack" &>/dev/null; then
    useradd -r -s /bin/false -d /opt/playtrack playtrack
fi
mkdir -p /opt/playtrack /var/playtrack/recordings /var/playtrack/queue
chown -R playtrack:playtrack /opt/playtrack /var/playtrack
usermod -aG video playtrack
ok "Utente 'playtrack' e cartelle pronti"

# ---------------------------------------------------------------------------
# [3/6] Copia codice Python
# ---------------------------------------------------------------------------
echo "[3/6] Copia codice Python..."
cp "$SCRIPT_DIR/playtrack/__init__.py" /opt/playtrack/__init__.py
cp "$SCRIPT_DIR/playtrack/agent.py"    /opt/playtrack/agent.py
cp "$SCRIPT_DIR/playtrack/uploader.py" /opt/playtrack/uploader.py
cp "$SCRIPT_DIR/requirements.txt"      /opt/playtrack/requirements.txt
chown playtrack:playtrack /opt/playtrack/__init__.py \
                          /opt/playtrack/agent.py \
                          /opt/playtrack/uploader.py \
                          /opt/playtrack/requirements.txt
ok "Codice copiato in /opt/playtrack/"

# ---------------------------------------------------------------------------
# [4/6] Virtualenv e dipendenze Python
# ---------------------------------------------------------------------------
echo "[4/6] Creazione virtualenv e installazione dipendenze Python..."
sudo -u playtrack python3 -m venv /opt/playtrack/venv
sudo -u playtrack /opt/playtrack/venv/bin/pip install --quiet --upgrade pip
sudo -u playtrack /opt/playtrack/venv/bin/pip install --quiet -r /opt/playtrack/requirements.txt
ok "Virtualenv pronto: /opt/playtrack/venv"

# ---------------------------------------------------------------------------
# [5/6] Service account Firebase e .env
# ---------------------------------------------------------------------------
echo "[5/6] Configurazione credenziali..."

SA_SRC="$SCRIPT_DIR/firebase-service-account.json"
SA_DST="/opt/playtrack/firebase-service-account.json"
if [ -f "$SA_SRC" ]; then
    cp "$SA_SRC" "$SA_DST"
    chown playtrack:playtrack "$SA_DST"
    chmod 600 "$SA_DST"
    ok "Service account Firebase copiato"
else
    warn "firebase-service-account.json non trovato nella directory del repo."
    warn "Copialo manualmente: sudo cp /path/to/firebase-service-account.json $SA_DST"
    warn "Poi: sudo chown playtrack:playtrack $SA_DST && sudo chmod 600 $SA_DST"
fi

cat > /opt/playtrack/.env <<EOF
# Identità device — DIVERSO su ogni RPi
CAMERA_ID=$CAMERA_ID
DEVICE_NAME=$DEVICE_NAME
FIELD_ID=$FIELD_ID

# Firebase — stesso su tutti i RPi
FIREBASE_PROJECT_ID=$FIREBASE_PROJECT_ID
GOOGLE_APPLICATION_CREDENTIALS=/opt/playtrack/firebase-service-account.json

# Percorsi locali — fissi
RECORDINGS_DIR=/var/playtrack/recordings
QUEUE_DB=/var/playtrack/queue/uploads.db
EOF
chown playtrack:playtrack /opt/playtrack/.env
chmod 600 /opt/playtrack/.env
ok "File .env creato"

# ---------------------------------------------------------------------------
# [6/6] Servizio systemd
# ---------------------------------------------------------------------------
echo "[6/6] Configurazione servizio systemd..."
cat > /etc/systemd/system/playtrack.service <<EOF
[Unit]
Description=PlayTrack Edge Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=playtrack
WorkingDirectory=/opt
EnvironmentFile=/opt/playtrack/.env
ExecStart=/opt/playtrack/venv/bin/python -m playtrack.agent
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=playtrack

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable playtrack
systemctl start playtrack
ok "Servizio abilitato e avviato"

# ---------------------------------------------------------------------------
# Riepilogo finale
# ---------------------------------------------------------------------------
echo ""
echo "============================================"
echo -e "   ${GREEN}Setup completato!${NC}"
echo "============================================"
echo ""
echo "  Device : $DEVICE_NAME"
echo "  Camera : $CAMERA_ID"
echo "  Field  : $FIELD_ID"
echo ""
echo "  Comandi utili:"
echo "    sudo journalctl -u playtrack -f        # log live"
echo "    sudo systemctl status playtrack        # stato"
echo "    sudo systemctl restart playtrack       # riavvio"
echo ""

# Verifica avvio
sleep 2
if systemctl is-active --quiet playtrack; then
    ok "Il daemon è in esecuzione"
else
    warn "Il daemon non sembra attivo — controlla: sudo journalctl -u playtrack -n 30"
fi
