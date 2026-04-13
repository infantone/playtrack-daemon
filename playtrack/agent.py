import os
import time
import logging
import subprocess
import threading
import sqlite3
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import credentials, firestore, storage
from playtrack.uploader import upload_video

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurazione da .env
# ---------------------------------------------------------------------------
CAMERA_ID      = os.environ["CAMERA_ID"]
DEVICE_NAME    = os.environ["DEVICE_NAME"]
FIELD_ID       = os.environ["FIELD_ID"]
PROJECT_ID     = os.environ["FIREBASE_PROJECT_ID"]
SA_PATH        = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
RECORDINGS_DIR = Path(os.environ["RECORDINGS_DIR"])
QUEUE_DB       = os.environ["QUEUE_DB"]

RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Firebase init
# ---------------------------------------------------------------------------
cred = credentials.Certificate(SA_PATH)
firebase_admin.initialize_app(cred, {
    "projectId": PROJECT_ID,
    "storageBucket": f"{PROJECT_ID}.appspot.com",
})
db = firestore.client()

# ---------------------------------------------------------------------------
# Coda upload persistente (SQLite locale)
# ---------------------------------------------------------------------------
def init_queue():
    con = sqlite3.connect(QUEUE_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS upload_queue (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id  TEXT NOT NULL,
            file_path TEXT NOT NULL,
            status    TEXT DEFAULT 'pending',
            created   TEXT DEFAULT (datetime('now'))
        )
    """)
    con.commit()
    con.close()


def enqueue(match_id: str, file_path: str):
    con = sqlite3.connect(QUEUE_DB)
    con.execute(
        "INSERT INTO upload_queue (match_id, file_path) VALUES (?, ?)",
        (match_id, str(file_path)),
    )
    con.commit()
    con.close()
    log.info(f"[QUEUE] Aggiunto: match_id={match_id} path={file_path}")


def next_pending():
    con = sqlite3.connect(QUEUE_DB)
    row = con.execute(
        "SELECT id, match_id, file_path FROM upload_queue WHERE status='pending' LIMIT 1"
    ).fetchone()
    con.close()
    return row


def mark_done(row_id: int):
    con = sqlite3.connect(QUEUE_DB)
    con.execute("UPDATE upload_queue SET status='done' WHERE id=?", (row_id,))
    con.commit()
    con.close()


def mark_error(row_id: int):
    con = sqlite3.connect(QUEUE_DB)
    con.execute("UPDATE upload_queue SET status='error' WHERE id=?", (row_id,))
    con.commit()
    con.close()

# ---------------------------------------------------------------------------
# Stato globale
# ---------------------------------------------------------------------------
state = {
    "recording":    False,
    "match_id":     None,
    "ffmpeg_proc":  None,
    "video_path":   None,
}

# ---------------------------------------------------------------------------
# Registrazione
# ---------------------------------------------------------------------------
def start_recording(match_id: str):
    if state["recording"]:
        log.warning("[REC] start ignorato: gia' in registrazione")
        return

    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_path = RECORDINGS_DIR / f"{match_id}_{CAMERA_ID}_{timestamp}.mp4"

    # ------------------------------------------------------------------
    # Comando ffmpeg
    # Per sviluppo (senza camera) usa testsrc (video sintetico).
    # Per produzione su RPi con libcamera, sostituire con:
    #   "-f", "libcamera", "-i", "/dev/video0",x\
    # ------------------------------------------------------------------
    cmd = [
        "ffmpeg",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25",
        "-vcodec", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        str(video_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    state["recording"]   = True
    state["match_id"]    = match_id
    state["ffmpeg_proc"] = proc
    state["video_path"]  = video_path

    _update_camera_status("recording")
    db.collection("matches").document(match_id).set({
        "fieldId":   FIELD_ID,
        "startedAt": firestore.SERVER_TIMESTAMP,
        "status":    "recording",
        "videos":    {CAMERA_ID: {"uploaded": False}},
    }, merge=True)

    log.info(f"[REC] Avviata: {video_path}")


def stop_recording():
    if not state["recording"]:
        log.warning("[REC] stop ignorato: non in registrazione")
        return

    proc       = state["ffmpeg_proc"]
    match_id   = state["match_id"]
    video_path = state["video_path"]

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    state["recording"]   = False
    state["ffmpeg_proc"] = None

    _update_camera_status("uploading")
    db.collection("matches").document(match_id).set({
        "endedAt": firestore.SERVER_TIMESTAMP,
        "status":  "uploading",
    }, merge=True)

    log.info(f"[REC] Fermata: {video_path}")
    enqueue(match_id, video_path)

# ---------------------------------------------------------------------------
# Worker upload (thread separato)
# ---------------------------------------------------------------------------
def upload_worker():
    log.info("[UPLOAD] Worker avviato")
    while True:
        row = next_pending()
        if row:
            row_id, match_id, file_path = row
            log.info(f"[UPLOAD] Inizio: {file_path}")
            try:
                storage_path = upload_video(file_path, match_id, CAMERA_ID)

                db.collection("matches").document(match_id).set({
                    "videos": {
                        CAMERA_ID: {
                            "uploaded":    True,
                            "storagePath": storage_path,
                        }
                    }
                }, merge=True)

                _update_camera_status("idle")
                mark_done(row_id)
                log.info(f"[UPLOAD] Completato: {storage_path}")

            except Exception as e:
                log.error(f"[UPLOAD] Errore: {e}")
                mark_error(row_id)
                _update_camera_status("error")
        else:
            time.sleep(10)

# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------
def heartbeat_worker():
    log.info("[HB] Worker avviato")
    while True:
        try:
            status = "recording" if state["recording"] else "idle"
            _update_camera_status(status)
        except Exception as e:
            log.warning(f"[HB] Errore: {e}")
        time.sleep(60)

# ---------------------------------------------------------------------------
# Listener Firestore
# ---------------------------------------------------------------------------
def on_field_snapshot(snapshots, changes, read_time):
    for snap in snapshots:
        data = snap.to_dict()
        if not data:
            continue

        cmd      = data.get("command", {})
        action   = cmd.get("action")
        match_id = cmd.get("matchId")

        if action == "start" and match_id and not state["recording"]:
            log.info(f"[CMD] start -> match_id={match_id}")
            start_recording(match_id)

        elif action == "stop" and state["recording"]:
            log.info("[CMD] stop")
            stop_recording()

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _update_camera_status(status: str):
    db.collection("fields").document(FIELD_ID) \
      .collection("cameras").document(CAMERA_ID) \
      .set({
          "status":     status,
          "lastSeen":   firestore.SERVER_TIMESTAMP,
          "deviceName": DEVICE_NAME,
      }, merge=True)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info(f"PlayTrack agent avviato: {DEVICE_NAME} ({CAMERA_ID})")
    init_queue()

    threading.Thread(target=upload_worker,    daemon=True).start()
    threading.Thread(target=heartbeat_worker, daemon=True).start()

    field_ref = db.collection("fields").document(FIELD_ID)
    field_ref.on_snapshot(on_field_snapshot)
    log.info(f"[FS] In ascolto su fields/{FIELD_ID}")

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
