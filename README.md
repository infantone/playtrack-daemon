# playtrack-daemon

Daemon edge per Raspberry Pi — progetto PlayTrack.

Ogni campo ha 2 RPi (cam-a e cam-b), uno per lato lungo.
I RPi registrano video, li caricano su Firebase Storage, e ascoltano comandi da Firestore.

## Setup su un nuovo RPi

```bash
# 1. Clona il repo
git clone https://github.com/infantone/playtrack-daemon
cd playtrack-daemon

# 2. Esegui il setup (chiederà CAMERA_ID, DEVICE_NAME, FIELD_ID, FIREBASE_PROJECT_ID)
sudo python3 setup.py
```

Il setup mostra ogni comando eseguito e il relativo output.
Se un passaggio fallisce, viene stampato il messaggio di errore esatto prima di uscire.

### Firebase service account

Il service account JSON è necessario per autenticarsi su Firebase Storage e Firestore.
Il setup lo cerca automaticamente nella root del repo (`./firebase-service-account.json`).
Se non lo trova durante il setup, copialo manualmente **dopo**:

```bash
sudo cp /path/to/firebase-service-account.json /opt/playtrack/firebase-service-account.json
sudo chown playtrack:playtrack /opt/playtrack/firebase-service-account.json
sudo chmod 600 /opt/playtrack/firebase-service-account.json
```

> Il file non va mai committato nel repo (è già nel `.gitignore`).
> Lo stesso service account funziona su tutti i RPi del progetto.

## Aggiornare il codice su un RPi già configurato

```bash
git pull
sudo python3 update.py
```

`update.py` copia i sorgenti Python, verifica se `requirements.txt` è cambiato
(e installa le nuove dipendenze solo se necessario), poi riavvia il servizio.

## Variabili d'ambiente (.env)

| Variabile | Esempio | Note |
|---|---|---|
| `CAMERA_ID` | `cam-a` | `cam-a` o `cam-b` — diverso su ogni RPi |
| `DEVICE_NAME` | `playtrack-campo1-cam-a` | nome leggibile, unico per device |
| `FIELD_ID` | `campo-1` | ID del campo su Firestore |
| `FIREBASE_PROJECT_ID` | `playtrack-af614` | dalla Firebase Console |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/opt/playtrack/firebase-service-account.json` | fisso |
| `RECORDINGS_DIR` | `/var/playtrack/recordings` | fisso |
| `QUEUE_DB` | `/var/playtrack/queue/uploads.db` | fisso |
| `TELEGRAM_BOT_TOKEN` | `123456:ABC-...` | opzionale — **diverso** per ogni camera |
| `TELEGRAM_CHAT_ID` | `-1001234567890` | opzionale — id del gruppo, **uguale** su tutti i Pi |

## Struttura su disco dopo il setup

```
/opt/playtrack/
├── agent.py
├── uploader.py
├── telegram_bot.py
├── __init__.py
├── requirements.txt
├── .env                          (600, non nel repo)
├── firebase-service-account.json (600, non nel repo)
└── venv/

/var/playtrack/
├── recordings/
└── queue/
    └── uploads.db
```

## Comandi utili

```bash
sudo systemctl status playtrack       # stato
sudo journalctl -u playtrack -f       # log live
sudo journalctl -u playtrack -n 50    # ultimi 50 log
sudo systemctl restart playtrack      # riavvio
```

## Comandi Firestore

Scrivi su `fields/{fieldId}/command`:

```json
{ "action": "start", "matchId": "match-001" }
{ "action": "stop" }
```

## Verificare l'inquadratura in campo (`/foto` su Telegram)

Per posizionare le camere serve vedere al volo cosa stanno inquadrando, dal
telefono. Ogni RPi ha il **proprio bot Telegram**; i due bot stanno nello
**stesso gruppo**: scrivi `/foto` nel gruppo e ognuno risponde con uno scatto
della sua camera (`rpicam-jpeg`).

> Serve un token diverso per ogni camera: Telegram non consente a due processi
> di fare polling sullo stesso token (errore `409 Conflict`).

**Setup (una volta):**

1. Crea **2 bot** con [@BotFather](https://t.me/BotFather) (`/newbot`), es.
   `playtrack_campo1_cama_bot` e `playtrack_campo1_camb_bot`. Annota i 2 token.
2. Crea un **gruppo** Telegram e aggiungi entrambi i bot.
3. Trova il **chat id** del gruppo: lascia `TELEGRAM_CHAT_ID` vuoto, avvia il
   daemon, scrivi `/foto` nel gruppo — il bot risponde con il chat id. Poi
   imposta quel valore nel `.env` di **entrambi** i Pi.
4. Nel `.env` di ogni Pi metti il **suo** `TELEGRAM_BOT_TOKEN` e lo **stesso**
   `TELEGRAM_CHAT_ID`, quindi `sudo systemctl restart playtrack`.

`setup.py` chiede questi due valori (opzionali) durante l'installazione.

**Uso:** scrivi `/foto` nel gruppo → ricevi le due inquadrature.
Con `/foto@nome_bot` ne scatti una sola. Se la camera sta registrando, il bot
risponde che non può scattare (la camera è esclusiva).

## Note

- Per passare alla camera reale (libcamera), modifica la riga ffmpeg in `playtrack/agent.py`
