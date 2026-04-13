import logging
import firebase_admin
from firebase_admin import storage

log = logging.getLogger(__name__)


def upload_video(local_path: str, match_id: str, camera_id: str) -> str:
    """
    Carica il video su Firebase Storage.
    Restituisce lo storage path del file caricato.

    MIGRAZIONE A S3: sostituire il corpo di questa funzione con
    boto3 multipart upload verso Scaleway. La firma resta identica:
        upload_video(local_path, match_id, camera_id) -> str
    """
    bucket = storage.bucket()
    destination = f"videos/{match_id}/{camera_id}.mp4"
    blob = bucket.blob(destination)

    log.info(f"[UPLOAD] Avvio: {local_path} -> {destination}")
    blob.upload_from_filename(local_path, content_type="video/mp4")
    log.info(f"[UPLOAD] Completato: {destination}")

    return destination
