# playtrack-daemon

Daemon edge per Raspberry Pi — progetto PlayTrack.

Ogni campo ha 2 RPi (cam-a e cam-b), uno per lato lungo.
I RPi registrano video, li caricano su Firebase Storage, e ascoltano comandi da Firestore.

## Setup su un nuovo RPi

```bash
# 1. Clona il repo
git clone https://github.com/infantone/playtrack-daemon
cd playtrack-daemon

# 2. (Opzionale) Copia il service account Firebase nella directory del repo
#    Altrimenti lo puoi copiare manualmente dopo il setup
cp /path/to/firebase-service-account.json .

# 3. Esegui il setup (chiederà CAMERA_ID, DEVICE_NAME, FIELD_ID, FIREBASE_PROJECT_ID)
sudo ./setup.sh
```

## Aggiornare il codice su un RPi già configurato

```bash
git pull
sudo ./update.sh
```

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

## Struttura su disco dopo il setup

```
/opt/playtrack/
├── agent.py
├── uploader.py
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

## Note

- Il `firebase-service-account.json` **non va mai committato** (nel .gitignore)
- Lo stesso service account funziona su tutti i RPi del progetto
- Per passare alla camera reale (libcamera), modifica la riga ffmpeg in `playtrack/agent.py`
