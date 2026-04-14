#!/usr/bin/env python3
"""
PlayTrack — Setup automatico su nuovo RPi
Uso: sudo python3 setup.py
"""

import os
import sys
import shutil
import subprocess
import textwrap
from pathlib import Path

# =============================================================================
# Helpers output
# =============================================================================

RESET  = "\033[0m"
RED    = "\033[0;31m"
GREEN  = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN   = "\033[0;36m"
BOLD   = "\033[1m"

def ok(msg):    print(f"{GREEN}  ✓ {msg}{RESET}")
def warn(msg):  print(f"{YELLOW}  ⚠ {msg}{RESET}")
def info(msg):  print(f"{CYAN}  → {msg}{RESET}")
def step(n, total, msg): print(f"\n{BOLD}[{n}/{total}] {msg}...{RESET}")

def err(msg, exit_code=1):
    print(f"\n{RED}  ✗ ERRORE: {msg}{RESET}\n", file=sys.stderr)
    sys.exit(exit_code)

def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{YELLOW}  → {prompt}{suffix}: {RESET}").strip()
    except (EOFError, KeyboardInterrupt):
        err("Setup interrotto dall'utente")
    if not val and default:
        return default
    return val


# =============================================================================
# Wrapper subprocess con log verboso
# =============================================================================

def run(cmd, desc=None, check=True, capture=False, user=None):
    """
    Esegue un comando mostrando il comando stesso prima di lanciarlo.
    Se user != None lo esegue come quell'utente (sudo -u <user>).
    """
    if isinstance(cmd, str):
        cmd = cmd.split()
    if user:
        cmd = ["sudo", "-u", user] + cmd

    display = " ".join(cmd)
    if desc:
        info(f"{desc}")
    print(f"  {CYAN}$ {display}{RESET}")

    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )

    if result.stdout and not capture:
        pass  # stdout già stampato live
    if result.returncode != 0 and check:
        if result.stderr:
            print(f"  {RED}stderr: {result.stderr.strip()}{RESET}", file=sys.stderr)
        err(f"Comando fallito (exit {result.returncode}): {display}")
    return result


def chown(path, owner, group=None):
    group = group or owner
    info(f"chown {owner}:{group} {path}")
    shutil.chown(path, user=owner, group=group)


def chmod(path, mode):
    info(f"chmod {oct(mode)} {path}")
    os.chmod(path, mode)


# =============================================================================
# Main
# =============================================================================

TOTAL_STEPS = 6
SCRIPT_DIR  = Path(__file__).parent.resolve()

INSTALL_DIR    = Path("/opt/playtrack")
VAR_BASE       = Path("/var/playtrack")
RECORDINGS_DIR = VAR_BASE / "recordings"
QUEUE_DIR      = VAR_BASE / "queue"
VENV_DIR       = INSTALL_DIR / "venv"
ENV_FILE       = INSTALL_DIR / ".env"
SA_DST         = INSTALL_DIR / "firebase-service-account.json"
SERVICE_FILE   = Path("/etc/systemd/system/playtrack.service")
PLAYTRACK_USER = "playtrack"

SOURCE_FILES = [
    SCRIPT_DIR / "playtrack" / "__init__.py",
    SCRIPT_DIR / "playtrack" / "agent.py",
    SCRIPT_DIR / "playtrack" / "uploader.py",
]
REQUIREMENTS = SCRIPT_DIR / "requirements.txt"


def check_root():
    if os.geteuid() != 0:
        err("Questo script deve girare come root. Usa: sudo python3 setup.py")


def collect_params():
    print(f"\n{BOLD}Inserisci i parametri per questo RPi:{RESET}")
    print("  (ogni dispositivo deve avere valori unici)\n")

    camera_id   = ask("CAMERA_ID (es: cam-a, cam-b)")
    device_name = ask("DEVICE_NAME (es: playtrack-campo1-cam-a)")
    field_id    = ask("FIELD_ID (es: campo-1)")
    project_id  = ask("FIREBASE_PROJECT_ID (es: playtrack-af614)")

    missing = [
        (camera_id,   "CAMERA_ID"),
        (device_name, "DEVICE_NAME"),
        (field_id,    "FIELD_ID"),
        (project_id,  "FIREBASE_PROJECT_ID"),
    ]
    for val, name in missing:
        if not val:
            err(f"{name} non può essere vuoto")

    return camera_id, device_name, field_id, project_id


def confirm(camera_id, device_name, field_id, project_id):
    print(f"\n{BOLD}--- Riepilogo configurazione ---{RESET}")
    print(f"  CAMERA_ID            : {camera_id}")
    print(f"  DEVICE_NAME          : {device_name}")
    print(f"  FIELD_ID             : {field_id}")
    print(f"  FIREBASE_PROJECT_ID  : {project_id}")
    print()
    ans = ask("Confermi? (y/n)")
    if ans.lower() != "y":
        err("Setup annullato dall'utente")


# ---------------------------------------------------------------------------
# Step 1 — Dipendenze di sistema
# ---------------------------------------------------------------------------
def step1_system_deps():
    step(1, TOTAL_STEPS, "Installazione dipendenze di sistema")

    info("Aggiornamento lista pacchetti (apt-get update)")
    run(["apt-get", "update", "-qq"], desc=None)
    ok("apt-get update completato")

    packages = ["python3-pip", "python3-venv", "ffmpeg", "sqlite3", "curl", "git"]
    info(f"Installazione pacchetti: {', '.join(packages)}")
    run(["apt-get", "install", "-y", "-qq"] + packages)
    ok(f"Pacchetti installati: {', '.join(packages)}")


# ---------------------------------------------------------------------------
# Step 2 — Utente e cartelle
# ---------------------------------------------------------------------------
def step2_user_and_dirs():
    step(2, TOTAL_STEPS, "Creazione utente e cartelle")

    # Crea utente di sistema se non esiste
    result = run(["id", PLAYTRACK_USER], check=False, capture=True)
    if result.returncode != 0:
        info(f"Creazione utente di sistema '{PLAYTRACK_USER}'")
        run(["useradd", "-r", "-s", "/bin/false", "-d", str(INSTALL_DIR), PLAYTRACK_USER])
        ok(f"Utente '{PLAYTRACK_USER}' creato")
    else:
        info(f"Utente '{PLAYTRACK_USER}' già esistente, skip")

    # Crea directory
    for d in [INSTALL_DIR, RECORDINGS_DIR, QUEUE_DIR]:
        info(f"mkdir -p {d}")
        d.mkdir(parents=True, exist_ok=True)
        ok(f"Directory pronta: {d}")

    # Permessi
    for root_dir in [INSTALL_DIR, VAR_BASE]:
        for path in [root_dir, *root_dir.rglob("*")]:
            try:
                chown(path, PLAYTRACK_USER)
            except Exception as e:
                warn(f"chown fallito su {path}: {e}")

    # Aggiungi utente al gruppo video
    info(f"Aggiunta di '{PLAYTRACK_USER}' al gruppo 'video'")
    run(["usermod", "-aG", "video", PLAYTRACK_USER])
    ok(f"'{PLAYTRACK_USER}' aggiunto al gruppo 'video'")


# ---------------------------------------------------------------------------
# Step 3 — Copia codice Python
# ---------------------------------------------------------------------------
def step3_copy_code():
    step(3, TOTAL_STEPS, "Copia codice Python")

    for src in SOURCE_FILES:
        if not src.exists():
            err(f"File sorgente non trovato: {src}\n"
                f"  Assicurati di eseguire il setup dalla root del repository.")
        dst = INSTALL_DIR / src.name
        info(f"Copia {src.name}  →  {dst}")
        shutil.copy2(src, dst)
        chown(dst, PLAYTRACK_USER)
        ok(f"{src.name} copiato")

    if not REQUIREMENTS.exists():
        err(f"requirements.txt non trovato in: {REQUIREMENTS}")
    dst_req = INSTALL_DIR / "requirements.txt"
    info(f"Copia requirements.txt  →  {dst_req}")
    shutil.copy2(REQUIREMENTS, dst_req)
    chown(dst_req, PLAYTRACK_USER)
    ok("requirements.txt copiato")


# ---------------------------------------------------------------------------
# Step 4 — Virtualenv e dipendenze Python
# ---------------------------------------------------------------------------
def step4_venv():
    step(4, TOTAL_STEPS, "Creazione virtualenv e installazione dipendenze Python")

    if VENV_DIR.exists():
        info(f"Virtualenv già presente in {VENV_DIR}, verrà ricreato")
        shutil.rmtree(VENV_DIR)

    info(f"Creazione venv in {VENV_DIR}")
    run(["python3", "-m", "venv", str(VENV_DIR)], user=PLAYTRACK_USER)
    ok(f"Virtualenv creato: {VENV_DIR}")

    pip = str(VENV_DIR / "bin" / "pip")

    info("Aggiornamento pip")
    run([pip, "install", "--upgrade", "pip"], user=PLAYTRACK_USER)
    ok("pip aggiornato")

    info(f"Installazione dipendenze da {INSTALL_DIR / 'requirements.txt'}")
    run([pip, "install", "-r", str(INSTALL_DIR / "requirements.txt")], user=PLAYTRACK_USER)
    ok("Dipendenze Python installate")


# ---------------------------------------------------------------------------
# Step 5 — Credenziali Firebase e .env
# ---------------------------------------------------------------------------
def step5_credentials(camera_id, device_name, field_id, project_id):
    step(5, TOTAL_STEPS, "Configurazione credenziali")

    sa_src = SCRIPT_DIR / "firebase-service-account.json"
    if sa_src.exists():
        info(f"Copia service account: {sa_src}  →  {SA_DST}")
        shutil.copy2(sa_src, SA_DST)
        chown(SA_DST, PLAYTRACK_USER)
        chmod(SA_DST, 0o600)
        ok("Service account Firebase copiato con permessi 600")
    else:
        warn(f"firebase-service-account.json NON trovato in: {sa_src}")
        warn("Copialo manualmente dopo il setup:")
        warn(f"  sudo cp /path/to/firebase-service-account.json {SA_DST}")
        warn(f"  sudo chown {PLAYTRACK_USER}:{PLAYTRACK_USER} {SA_DST}")
        warn(f"  sudo chmod 600 {SA_DST}")

    env_content = textwrap.dedent(f"""\
        # Identità device — DIVERSO su ogni RPi
        CAMERA_ID={camera_id}
        DEVICE_NAME={device_name}
        FIELD_ID={field_id}

        # Firebase — stesso su tutti i RPi
        FIREBASE_PROJECT_ID={project_id}
        GOOGLE_APPLICATION_CREDENTIALS={SA_DST}

        # Percorsi locali — fissi
        RECORDINGS_DIR={RECORDINGS_DIR}
        QUEUE_DB={QUEUE_DIR / 'uploads.db'}
    """)

    info(f"Scrittura {ENV_FILE}")
    ENV_FILE.write_text(env_content, encoding="utf-8")
    chown(ENV_FILE, PLAYTRACK_USER)
    chmod(ENV_FILE, 0o600)
    ok(f".env scritto in {ENV_FILE} con permessi 600")

    # Mostra contenuto senza valori sensibili
    print(f"\n  {CYAN}Contenuto .env:{RESET}")
    for line in env_content.splitlines():
        if line.startswith("#") or not line.strip():
            print(f"    {line}")
        else:
            key = line.split("=")[0]
            print(f"    {key}=***")


# ---------------------------------------------------------------------------
# Step 6 — Servizio systemd
# ---------------------------------------------------------------------------
def step6_systemd():
    step(6, TOTAL_STEPS, "Configurazione servizio systemd")

    service_content = textwrap.dedent(f"""\
        [Unit]
        Description=PlayTrack Edge Agent
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=exec
        User={PLAYTRACK_USER}
        WorkingDirectory=/opt
        EnvironmentFile={ENV_FILE}
        ExecStart={VENV_DIR}/bin/python -m playtrack.agent
        Restart=always
        RestartSec=5
        StandardOutput=journal
        StandardError=journal
        SyslogIdentifier=playtrack

        [Install]
        WantedBy=multi-user.target
    """)

    info(f"Scrittura {SERVICE_FILE}")
    SERVICE_FILE.write_text(service_content, encoding="utf-8")
    ok(f"File di servizio scritto: {SERVICE_FILE}")

    info("systemctl daemon-reload")
    run(["systemctl", "daemon-reload"])
    ok("daemon-reload completato")

    info("systemctl enable playtrack")
    run(["systemctl", "enable", "playtrack"])
    ok("Servizio abilitato all'avvio")

    info("systemctl start playtrack")
    run(["systemctl", "start", "playtrack"])
    ok("Servizio avviato")


# ---------------------------------------------------------------------------
# Riepilogo finale + verifica
# ---------------------------------------------------------------------------
def final_summary(camera_id, device_name, field_id):
    print(f"\n{BOLD}{'='*44}{RESET}")
    print(f"{BOLD}{GREEN}   Setup completato con successo!{RESET}{BOLD}{'':>14}{RESET}")
    print(f"{BOLD}{'='*44}{RESET}\n")
    print(f"  Device  : {device_name}")
    print(f"  Camera  : {camera_id}")
    print(f"  Field   : {field_id}")
    print()
    print(f"  {BOLD}Comandi utili:{RESET}")
    print("    sudo journalctl -u playtrack -f        # log live")
    print("    sudo systemctl status playtrack        # stato")
    print("    sudo systemctl restart playtrack       # riavvio")
    print()

    import time
    time.sleep(2)

    result = run(["systemctl", "is-active", "playtrack"], check=False, capture=True)
    status = result.stdout.strip()
    if status == "active":
        ok(f"Daemon in esecuzione (status: {status})")
    else:
        warn(f"Daemon non attivo (status: '{status}')")
        warn("Controlla i log: sudo journalctl -u playtrack -n 30")
        print()


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    print()
    print(f"{BOLD}{'='*44}{RESET}")
    print(f"{BOLD}   PlayTrack — Setup RPi{RESET}")
    print(f"{BOLD}{'='*44}{RESET}")

    check_root()

    camera_id, device_name, field_id, project_id = collect_params()
    confirm(camera_id, device_name, field_id, project_id)

    try:
        step1_system_deps()
        step2_user_and_dirs()
        step3_copy_code()
        step4_venv()
        step5_credentials(camera_id, device_name, field_id, project_id)
        step6_systemd()
        final_summary(camera_id, device_name, field_id)
    except KeyboardInterrupt:
        err("\nSetup interrotto dall'utente (Ctrl+C)")
