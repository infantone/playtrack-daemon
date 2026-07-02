"""
Bot Telegram per PlayTrack — comando /foto per verificare l'inquadratura.

Ogni RPi ha il proprio bot (token diverso); i due bot stanno nello stesso
gruppo Telegram. Quando scrivi /foto nel gruppo, entrambi i bot lo ricevono e
ognuno risponde con uno scatto della propria camera.

Telegram NON permette il polling concorrente sullo stesso token (errore 409),
per questo serve un token diverso per ogni camera invece di uno condiviso.

Config (.env, opzionale — se TELEGRAM_BOT_TOKEN è vuoto il bot resta disattivo):
    TELEGRAM_BOT_TOKEN   token del bot di QUESTA camera (da @BotFather)
    TELEGRAM_CHAT_ID     id del gruppo autorizzato (uguale su tutti i Pi)
"""

import os
import time
import logging
import threading

import requests

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT = 30  # long-poll (secondi)


def start_telegram_worker(capture_fn, clip_fn, label: str):
    """
    Avvia il worker Telegram in un thread daemon (no-op se il token non è
    configurato).

    capture_fn() -> foto (comando /foto)
    clip_fn(duration_sec=None) -> clip video (comando /video)
    Entrambe devono restituire:
        - il path del file in caso di successo
        - None se la camera è occupata (registrazione in corso)
        - oppure sollevare un'eccezione in caso di errore
    label: etichetta della camera, es. "campo-1 / cam-a"
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        log.info("[TG] Telegram disabilitato: TELEGRAM_BOT_TOKEN non impostato")
        return

    allowed_chat = chat or None
    if allowed_chat is None:
        log.warning("[TG] TELEGRAM_CHAT_ID non impostato: modalità bootstrap "
                    "(il bot risponde con il chat id ma non scatta finché non è configurato)")

    t = threading.Thread(
        target=_run, args=(token, allowed_chat, capture_fn, clip_fn, label), daemon=True
    )
    t.start()
    log.info(f"[TG] Worker Telegram avviato (label={label})")


# ---------------------------------------------------------------------------
# Loop principale
# ---------------------------------------------------------------------------
def _run(token, allowed_chat, capture_fn, clip_fn, label):
    session = requests.Session()
    offset = None
    while True:
        try:
            updates = _get_updates(session, token, offset)
        except Exception as e:
            log.warning(f"[TG] getUpdates errore: {e}")
            time.sleep(5)
            continue

        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                _handle_update(session, token, allowed_chat, capture_fn, clip_fn, label, upd)
            except Exception as e:
                log.warning(f"[TG] Errore gestione update: {e}")


def _get_updates(session, token, offset):
    params = {"timeout": _POLL_TIMEOUT, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset
    url = _API.format(token=token, method="getUpdates")
    r = session.get(url, params=params, timeout=_POLL_TIMEOUT + 10)
    if r.status_code == 409:
        # Un altro processo sta facendo polling con lo stesso token.
        log.warning("[TG] 409 Conflict: un altro processo usa lo stesso TELEGRAM_BOT_TOKEN. "
                    "Ogni camera deve avere il proprio token.")
        time.sleep(5)
        return []
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        log.warning(f"[TG] getUpdates risposta non ok: {data}")
        return []
    return data.get("result", [])


def _handle_update(session, token, allowed_chat, capture_fn, clip_fn, label, upd):
    msg = upd.get("message")
    if not msg:
        return

    chat_id = msg.get("chat", {}).get("id")
    text    = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return

    # Normalizza il comando: "/foto@cam_a_bot args" -> "/foto", tieni gli argomenti
    parts = text.split()
    cmd   = parts[0].lower()
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    args = parts[1:]

    # Bootstrap: senza chat autorizzato aiuta a scoprire il chat id, ma non scatta.
    if allowed_chat is None:
        _send_message(
            session, token, chat_id,
            f"⚙️ Chat ID di questa conversazione: {chat_id}\n"
            f"Imposta TELEGRAM_CHAT_ID={chat_id} nel .env dei Pi e riavvia per abilitare /foto."
        )
        return

    if str(chat_id) != str(allowed_chat):
        log.warning(f"[TG] Comando '{cmd}' ignorato da chat non autorizzata: {chat_id}")
        return

    if cmd in ("/foto", "/photo"):
        _do_snapshot(session, token, chat_id, capture_fn, label)
    elif cmd in ("/video", "/clip"):
        _do_video(session, token, chat_id, clip_fn, label, args)
    elif cmd in ("/start", "/help"):
        _send_message(
            session, token, chat_id,
            f"PlayTrack — {label}\n\nComandi:\n"
            f"/foto — scatta e invia l'inquadratura attuale\n"
            f"/video [sec] — invia una clip 1080p50 (default 40s, max 45s)"
        )


def _do_snapshot(session, token, chat_id, capture_fn, label):
    _send_chat_action(session, token, chat_id, "upload_photo")
    try:
        path = capture_fn()
    except Exception as e:
        log.error(f"[TG] Scatto fallito: {e}")
        _send_message(session, token, chat_id, f"⚠️ {label}: errore nello scatto.")
        return

    if path is None:
        _send_message(session, token, chat_id,
                      f"🔴 {label}: sto registrando, riprova a fine partita.")
        return

    _send_photo(session, token, chat_id, path, caption=f"📷 {label}")


def _do_video(session, token, chat_id, clip_fn, label, args):
    duration = None
    if args:
        try:
            duration = int(args[0])
        except ValueError:
            duration = None

    _send_chat_action(session, token, chat_id, "record_video")
    _send_message(session, token, chat_id,
                  f"🎥 {label}: registro la clip, attendi qualche secondo…")
    try:
        path = clip_fn(duration)
    except Exception as e:
        log.error(f"[TG] Clip fallita: {e}")
        _send_message(session, token, chat_id, f"⚠️ {label}: {e}")
        return

    if path is None:
        _send_message(session, token, chat_id,
                      f"🔴 {label}: sto registrando, riprova a fine partita.")
        return

    _send_video(session, token, chat_id, path, caption=f"🎥 {label}")


# ---------------------------------------------------------------------------
# Wrapper API Telegram (best-effort, non sollevano mai)
# ---------------------------------------------------------------------------
def _send_message(session, token, chat_id, text):
    try:
        session.post(
            _API.format(token=token, method="sendMessage"),
            data={"chat_id": chat_id, "text": text},
            timeout=15,
        )
    except Exception as e:
        log.warning(f"[TG] sendMessage errore: {e}")


def _send_chat_action(session, token, chat_id, action):
    try:
        session.post(
            _API.format(token=token, method="sendChatAction"),
            data={"chat_id": chat_id, "action": action},
            timeout=10,
        )
    except Exception:
        pass  # puramente cosmetico ("sta inviando una foto…")


def _send_photo(session, token, chat_id, path, caption):
    try:
        with open(path, "rb") as f:
            r = session.post(
                _API.format(token=token, method="sendPhoto"),
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": ("snapshot.jpg", f, "image/jpeg")},
                timeout=60,
            )
        if r.status_code != 200:
            log.warning(f"[TG] sendPhoto risposta {r.status_code}: {r.text}")
    except Exception as e:
        log.warning(f"[TG] sendPhoto errore: {e}")


def _send_video(session, token, chat_id, path, caption):
    try:
        with open(path, "rb") as f:
            r = session.post(
                _API.format(token=token, method="sendVideo"),
                data={"chat_id": chat_id, "caption": caption, "supports_streaming": "true"},
                files={"video": ("clip.mp4", f, "video/mp4")},
                timeout=300,  # upload di decine di MB su rete di campo
            )
        if r.status_code != 200:
            log.warning(f"[TG] sendVideo risposta {r.status_code}: {r.text}")
            _send_message(session, token, chat_id,
                          f"⚠️ invio clip fallito ({r.status_code}). "
                          f"Forse troppo grande o rete lenta.")
    except Exception as e:
        log.warning(f"[TG] sendVideo errore: {e}")
        _send_message(session, token, chat_id, "⚠️ errore nell'invio della clip.")
