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

- Per passare alla camera reale (libcamera), modifica la riga ffmpeg in `playtrack/agent.py`
