import os
import signal
import time
import logging
import subprocess
import threading
import sqlite3
import queue
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import credentials, firestore, storage
from playtrack.uploader import upload_video

# ---------------------------------------------------------------------------
# Logging base (console)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/playtrack/logs", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)
# Riduci il rumore dei SDK di terze parti
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)
logging.getLogger("firebase_admin").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Firestore log handler (asincrono, non blocca il thread principale)
# ---------------------------------------------------------------------------
_log_queue: queue.Queue = queue.Queue(maxsize=500)


class FirestoreLogHandler(logging.Handler):
    """Invia ogni record di log a fields/{field_id}/cameras/{camera_id}/logs."""

    def __init__(self, db_ref, field_id: str, camera_id: str, device_name: str):
        super().__init__(level=logging.DEBUG)
        self._db        = db_ref
        self._field_id  = field_id
        self._camera_id = camera_id
        self._device    = device_name
        self._col       = (
            db_ref
            .collection("fields").document(field_id)
            .collection("cameras").document(camera_id)
            .collection("logs")
        )

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "ts":         datetime.now(timezone.utc),
                "level":      record.levelname,
                "msg":        self.format(record),
                "deviceName": self._device,
            }
            _log_queue.put_nowait(entry)
        except queue.Full:
            pass  # non bloccare mai il processo per i log

    def _flush_worker(self):
        while True:
            entry = _log_queue.get()
            try:
                self._col.add(entry)
            except Exception:
                pass  # silenzio: non loggare errori di log per evitare loop

    def start_worker(self):
        t = threading.Thread(target=self._flush_worker, daemon=True)
        t.start()


_fs_log_handler: FirestoreLogHandler | None = None


def attach_firestore_logger():
    """Chiamato dopo l'init di Firebase, installa il handler su root logger."""
    global _fs_log_handler
    fmt = logging.Formatter("%(levelname)s %(name)s %(message)s")
    _fs_log_handler = FirestoreLogHandler(db, FIELD_ID, CAMERA_ID, DEVICE_NAME)
    _fs_log_handler.setFormatter(fmt)
    _fs_log_handler.start_worker()
    logging.getLogger().addHandler(_fs_log_handler)
    log.info("[LOG] Firestore log handler attivato")

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
log.debug(f"[INIT] SA_PATH={SA_PATH}")
log.debug(f"[INIT] PROJECT_ID={PROJECT_ID} FIELD_ID={FIELD_ID} CAMERA_ID={CAMERA_ID}")
log.debug(f"[INIT] RECORDINGS_DIR={RECORDINGS_DIR} QUEUE_DB={QUEUE_DB}")
cred = credentials.Certificate(SA_PATH)
firebase_admin.initialize_app(cred, {
    "projectId": PROJECT_ID,
    "storageBucket": f"{PROJECT_ID}.firebasestorage.app",
})
log.debug("[INIT] Firebase inizializzato")
db = firestore.client()
log.debug("[INIT] Firestore client pronto")
attach_firestore_logger()

# ---------------------------------------------------------------------------
# Coda upload persistente (SQLite locale)
# ---------------------------------------------------------------------------
def init_queue():
    log.debug(f"[QUEUE] Apertura DB: {QUEUE_DB}")
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
    log.debug("[QUEUE] DB pronto")


def enqueue(match_id: str, file_path: str):
    file_size = Path(file_path).stat().st_size if Path(file_path).exists() else -1
    log.debug(f"[QUEUE] Enqueue: match_id={match_id} path={file_path} size={file_size} bytes")
    con = sqlite3.connect(QUEUE_DB)
    con.execute(
        "INSERT INTO upload_queue (match_id, file_path) VALUES (?, ?)",
        (match_id, str(file_path)),
    )
    con.commit()
    con.close()
    log.info(f"[QUEUE] Aggiunto: match_id={match_id} path={file_path} ({file_size} bytes)")


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
def _stream_stderr(proc: subprocess.Popen, label: str):
    """Legge stderr di rpicam-vid riga per riga in un thread dedicato."""
    for raw_line in proc.stderr:
        line = raw_line.decode(errors="replace").rstrip()
        if line:
            log.debug(f"[{label}] {line}")


def start_recording(match_id: str):
    if state["recording"]:
        log.warning("[REC] start ignorato: gia' in registrazione")
        return

    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_path = RECORDINGS_DIR / f"{match_id}_{CAMERA_ID}_{timestamp}.mp4"

    cmd = [
        "rpicam-vid",
        "-t", "0",
        "--width", "1920",
        "--height", "1080",
        "--framerate", "30",
        "--codec", "libav",
        "--libav-format", "mp4",
        "--bitrate", "8000000",
        "-o", str(video_path),
    ]
    log.debug(f"[REC] Comando: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    log.debug(f"[REC] PID rpicam-vid: {proc.pid}")

    # Stream stderr in real-time su un thread separato
    threading.Thread(
        target=_stream_stderr, args=(proc, "RPICAM"), daemon=True
    ).start()

    state["recording"]   = True
    state["match_id"]    = match_id
    state["ffmpeg_proc"] = proc
    state["video_path"]  = video_path

    log.debug("[REC] Aggiornamento Firestore status=recording")
    _update_camera_status("recording")
    db.collection("matches").document(match_id).set({
        "fieldId":   FIELD_ID,
        "startedAt": firestore.SERVER_TIMESTAMP,
        "status":    "recording",
        "videos":    {CAMERA_ID: {"uploaded": False}},
    }, merge=True)

    log.info(f"[REC] Avviata: {video_path} (PID={proc.pid})")


def stop_recording():
    if not state["recording"]:
        log.warning("[REC] stop ignorato: non in registrazione")
        return

    proc       = state["ffmpeg_proc"]
    match_id   = state["match_id"]
    video_path = state["video_path"]

    log.debug(f"[REC] Invio SIGINT a PID={proc.pid}")
    # SIGINT triggers graceful shutdown in rpicam-vid, allowing libav to
    # finalize the MP4 moov atom. SIGTERM/kill would leave the file without
    # the moov atom, producing a file with size but a black/unplayable video.
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=30)
        log.debug(f"[REC] rpicam-vid terminato con returncode={proc.returncode}")
    except subprocess.TimeoutExpired:
        log.warning("[REC] SIGINT timeout dopo 30s, invio SIGTERM")
        proc.terminate()
        try:
            proc.wait(timeout=10)
            log.debug(f"[REC] rpicam-vid terminato (SIGTERM) returncode={proc.returncode}")
        except subprocess.TimeoutExpired:
            log.error("[REC] SIGTERM timeout, SIGKILL")
            proc.kill()
            proc.wait()

    file_size = Path(video_path).stat().st_size if Path(video_path).exists() else -1
    log.info(f"[REC] File finale: {video_path} ({file_size} bytes)")
    if file_size <= 0:
        log.error("[REC] ATTENZIONE: file vuoto o assente dopo la registrazione!")

    state["recording"]   = False
    state["ffmpeg_proc"] = None

    log.debug("[REC] Aggiornamento Firestore status=uploading")
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
            file_size = Path(file_path).stat().st_size if Path(file_path).exists() else -1
            log.info(f"[UPLOAD] Inizio: {file_path} ({file_size} bytes)")
            if file_size <= 0:
                log.error(f"[UPLOAD] File assente o vuoto, salto: {file_path}")
                mark_error(row_id)
                continue
            try:
                log.debug(f"[UPLOAD] Chiamata upload_video match_id={match_id} camera_id={CAMERA_ID}")
                t0 = time.monotonic()
                storage_path = upload_video(file_path, match_id, CAMERA_ID)
                elapsed = time.monotonic() - t0
                log.debug(f"[UPLOAD] upload_video completato in {elapsed:.1f}s -> {storage_path}")

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
                log.info(f"[UPLOAD] Completato in {elapsed:.1f}s: {storage_path}")

            except Exception as e:
                log.error(f"[UPLOAD] Errore: {e}", exc_info=True)
                mark_error(row_id)
                _update_camera_status("error")
        else:
            log.debug("[UPLOAD] Nessun pending, attesa 10s")
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
    log.debug(f"[FS] Snapshot ricevuto: {len(snapshots)} doc(s), {len(changes)} change(s)")
    for snap in snapshots:
        data = snap.to_dict()
        log.debug(f"[FS] Documento '{snap.id}': {data}")
        if not data:
            continue

        cmd      = data.get("command", {})
        action   = cmd.get("action")
        match_id = cmd.get("matchId")
        log.debug(f"[FS] command={cmd} state.recording={state['recording']}")

        if action == "start" and match_id and not state["recording"]:
            log.info(f"[CMD] start -> match_id={match_id}")
            start_recording(match_id)

        elif action == "stop" and state["recording"]:
            log.info("[CMD] stop")
            stop_recording()

        elif action == "start" and state["recording"]:
            log.warning(f"[CMD] start ignorato: gia' in registrazione (match_id={state['match_id']})")

        elif action == "stop" and not state["recording"]:
            log.warning("[CMD] stop ignorato: non in registrazione")

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _update_camera_status(status: str):
    log.debug(f"[STATUS] fields/{FIELD_ID}/cameras/{CAMERA_ID} -> {status}")
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
