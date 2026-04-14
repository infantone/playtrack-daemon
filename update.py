#!/usr/bin/env python3
"""
PlayTrack — Aggiornamento rapido codice dopo git pull
Uso: sudo python3 update.py
"""

import os
import sys
import shutil
import subprocess
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

def ok(msg):   print(f"{GREEN}  ✓ {msg}{RESET}")
def warn(msg): print(f"{YELLOW}  ⚠ {msg}{RESET}")
def info(msg): print(f"{CYAN}  → {msg}{RESET}")

def err(msg, exit_code=1):
    print(f"\n{RED}  ✗ ERRORE: {msg}{RESET}\n", file=sys.stderr)
    sys.exit(exit_code)


def run(cmd, desc=None, check=True, capture=False):
    if isinstance(cmd, str):
        cmd = cmd.split()
    if desc:
        info(desc)
    print(f"  {CYAN}$ {' '.join(cmd)}{RESET}")
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if result.returncode != 0 and check:
        if result.stderr:
            print(f"  {RED}stderr: {result.stderr.strip()}{RESET}", file=sys.stderr)
        err(f"Comando fallito (exit {result.returncode}): {' '.join(cmd)}")
    return result


def chown(path, owner, group=None):
    group = group or owner
    info(f"chown {owner}:{group} {path}")
    shutil.chown(path, user=owner, group=group)


# =============================================================================
# Configurazione percorsi
# =============================================================================

SCRIPT_DIR     = Path(__file__).parent.resolve()
INSTALL_DIR    = Path("/opt/playtrack")
PLAYTRACK_USER = "playtrack"

SOURCE_FILES = [
    SCRIPT_DIR / "playtrack" / "__init__.py",
    SCRIPT_DIR / "playtrack" / "agent.py",
    SCRIPT_DIR / "playtrack" / "uploader.py",
]

# Opzionale: aggiorna anche requirements se cambiati
REQUIREMENTS_SRC = SCRIPT_DIR / "requirements.txt"
REQUIREMENTS_DST = INSTALL_DIR / "requirements.txt"

VENV_PIP = INSTALL_DIR / "venv" / "bin" / "pip"


# =============================================================================
# Funzioni di update
# =============================================================================

def check_root():
    if os.geteuid() != 0:
        err("Questo script deve girare come root. Usa: sudo python3 update.py")


def check_install_dir():
    if not INSTALL_DIR.exists():
        err(f"Directory di installazione non trovata: {INSTALL_DIR}\n"
            f"  Esegui prima il setup: sudo python3 setup.py")


def copy_source_files():
    print(f"\n{BOLD}[1/3] Copia file sorgente Python...{RESET}")
    for src in SOURCE_FILES:
        if not src.exists():
            err(f"File sorgente non trovato: {src}\n"
                f"  Assicurati di essere nella root del repository.")
        dst = INSTALL_DIR / src.name
        info(f"Copia {src.name}  →  {dst}")
        shutil.copy2(src, dst)
        chown(dst, PLAYTRACK_USER)
        ok(f"{src.name} aggiornato")


def update_requirements():
    print(f"\n{BOLD}[2/3] Verifica requirements.txt...{RESET}")

    if not REQUIREMENTS_SRC.exists():
        warn(f"requirements.txt non trovato in {REQUIREMENTS_SRC}, skip")
        return

    # Confronta i file per vedere se sono cambiati
    if REQUIREMENTS_DST.exists():
        src_content = REQUIREMENTS_SRC.read_text(encoding="utf-8")
        dst_content = REQUIREMENTS_DST.read_text(encoding="utf-8")
        if src_content == dst_content:
            info("requirements.txt invariato, skip installazione pip")
            return

    info(f"requirements.txt cambiato, aggiornamento dipendenze pip...")
    shutil.copy2(REQUIREMENTS_SRC, REQUIREMENTS_DST)
    chown(REQUIREMENTS_DST, PLAYTRACK_USER)

    if not VENV_PIP.exists():
        warn(f"pip non trovato in {VENV_PIP}, skip (venv non inizializzato?)")
        return

    run(
        ["sudo", "-u", PLAYTRACK_USER, str(VENV_PIP), "install", "-r", str(REQUIREMENTS_DST)],
        desc="Installazione nuove dipendenze Python"
    )
    ok("Dipendenze Python aggiornate")


def restart_service():
    print(f"\n{BOLD}[3/3] Riavvio servizio systemd...{RESET}")

    # Controlla che il servizio esista
    result = run(
        ["systemctl", "status", "playtrack"],
        check=False, capture=True,
        desc="Verifica stato servizio"
    )
    if result.returncode == 4:
        err("Servizio 'playtrack' non trovato.\n"
            "  Esegui prima il setup: sudo python3 setup.py")

    info("Riavvio playtrack")
    run(["systemctl", "restart", "playtrack"])
    ok("Servizio riavviato")

    import time
    time.sleep(2)

    result = run(["systemctl", "is-active", "playtrack"], check=False, capture=True)
    status = result.stdout.strip()
    if status == "active":
        ok(f"Daemon in esecuzione (status: {status})")
    else:
        warn(f"Daemon non attivo dopo il riavvio (status: '{status}')")
        warn("Controlla i log: sudo journalctl -u playtrack -n 30")


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    print()
    print(f"{BOLD}{'='*44}{RESET}")
    print(f"{BOLD}   PlayTrack — Aggiornamento codice{RESET}")
    print(f"{BOLD}{'='*44}{RESET}")

    check_root()
    check_install_dir()

    try:
        copy_source_files()
        update_requirements()
        restart_service()
    except KeyboardInterrupt:
        err("\nUpdate interrotto dall'utente (Ctrl+C)")

    print(f"\n{GREEN}{BOLD}  Update completato!{RESET}")
    print("  Log live: sudo journalctl -u playtrack -f\n")
