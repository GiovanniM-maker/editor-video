# Anime Scene Cutter MVP

Taglia automaticamente un episodio anime (o qualsiasi video MP4) in base a una
**richiesta in linguaggio naturale**, e produce un nuovo MP4 montato.

Esempi di richiesta:
- `"crea un edit con solo scene di combattimento"`
- `"taglia tutte le scene lente"`
- `"crea un recap di 60 secondi"`
- `"estraimi solo le scene in cui il protagonista parla"`
- `"crea un highlight emozionale"`

È un **MVP locale, da terminale**: nessun database, nessun frontend, nessun
servizio cloud obbligatorio. Lavora **solo su file locali** forniti da te.

---

## Come funziona (pipeline)

```
video.mp4
   │
   ├─ 1. estrazione audio (ffmpeg → WAV 16kHz mono)
   ├─ 2. trascrizione con timestamp (faster-whisper)      → data/transcript_segments.json
   ├─ 3. costruzione scene approssimative                 → data/scenes.json
   ├─ 4. selezione scene in base alla richiesta (rule-based / LLM-ready)
   ├─ 5. generazione edit plan                            → data/edit_plan.json
   └─ 6. taglio + concatenazione (ffmpeg)                 → output/final_edit.mp4
```

---

## Installazione

### 1. ffmpeg (obbligatorio, include `ffprobe`)

- **macOS:** `brew install ffmpeg`
- **Ubuntu/Debian:** `sudo apt install ffmpeg`
- **Windows:** scarica una build da <https://www.gyan.dev/ffmpeg/builds/> e aggiungi
  la cartella `bin` al `PATH`.

Verifica: `ffmpeg -version` e `ffprobe -version` devono rispondere.

### 2. Dipendenze Python (Python 3.11+)

```bash
python -m venv .venv
source .venv/bin/activate      # su Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> ⚠️ **Nota Whisper:** al primo utilizzo il modello viene **scaricato da internet**
> (~qualche centinaio di MB per `small`) e poi messo in **cache locale**. Serve
> connessione **solo la prima volta**. Le esecuzioni successive sono offline.

---

## Uso

Comando completo:

```bash
python main.py \
  --video input/episode.mp4 \
  --transcript input/transcript.txt \
  --scenes input/scenes.txt \
  --request "crea un edit con solo scene di combattimento" \
  --target-duration 60
```

### Parametri

| Parametro             | Obblig. | Descrizione |
|-----------------------|:------:|-------------|
| `--video`             | ✅*    | Video MP4 di input. |
| `--request`           | ✅     | Richiesta in linguaggio naturale. |
| `--transcript`        | ❌     | `transcript.txt`: se ha timestamp è usato come timeline, altrimenti come contesto. |
| `--scenes`            | ❌     | `scenes.txt`: se ha timestamp definisce i confini scena, altrimenti è contesto. |
| `--target-duration`   | ❌     | Durata target dell'edit, in secondi. |
| `--language`          | ❌     | Lingua Whisper (`it`, `en`, …). Default `auto`. |
| `--whisper-model`     | ❌     | `tiny`/`base`/`small`/`medium`/`large-v3`. Default `small`. |
| `--device`            | ❌     | `auto` (GPU se presente) / `cpu` / `cuda`. Default `auto`. |
| `--compute-type`      | ❌     | Precisione CTranslate2 (`auto`/`int8`/`float16`/…). Default `auto`. |
| `--use-llm`           | ❌     | Attiva la modalità LLM-ready del selector (per l'MVP delega alle euristiche). |
| `--dry-run`           | ❌     | Genera solo `data/edit_plan.json`, senza tagliare il video. |
| `--keep-temp`         | ❌     | Non cancella le clip temporanee e il WAV. |
| `--make-sample-video` | —      | Genera un MP4 di test ed esce (per provare la pipeline). |

\* `--video` non serve solo quando usi `--make-sample-video`.

### Quickstart minimo (anche senza transcript né scenes)

```bash
python main.py --video input/episode.mp4 --request "create a 60 second recap"
```

In questo caso il tool: estrae l'audio → trascrive → crea scene approssimative →
seleziona clip distribuite → genera `output/final_edit.mp4`.

### Provare subito senza un video vero

```bash
python main.py --make-sample-video input/sample.mp4
python main.py --video input/sample.mp4 --request "create a 60 second recap"
```

Il primo comando genera un video di test (pattern + tono) in pochi secondi, così
puoi verificare taglio e concatenazione senza attendere Whisper.

---

## Input attesi

- **Video:** un MP4 locale qualsiasi.
- **`transcript.txt`** (opzionale): testo dell'episodio. Con righe tipo
  `00:00:12 testo` viene usato come timeline; senza timestamp è solo contesto.
  Vedi `examples/transcript_example.txt`.
- **`scenes.txt`** (opzionale): righe tipo `HH:MM:SS - descrizione`.
  Vedi `examples/scenes_example.txt`.

## Output generati

- `data/transcript_segments.json` — segmenti `{start, end, text}`.
- `data/scenes.json` — scene con `scene_id, start, end, transcript_text,
  external_description, auto_summary, tags`.
- `data/edit_plan.json` — piano di montaggio (`source_video`, `user_request`,
  `target_duration`, `clips[]`).
- `output/final_edit.mp4` — il video montato finale.

Al termine la CLI stampa: numero scene individuate, numero selezionate, durata
finale stimata, path dell'edit plan e del video finale.

---

## Selezione scene: come decide

Il modulo `src/scene_selector.py` ha **due modalità ben separate**:

- **Rule-based (default):** interpreta la richiesta con un vocabolario keyword
  **multilingua IT/EN** (es. *combattimento ↔ fight ↔ battle ↔ azione*), assegna
  a ogni scena `keep`/`priority`/`reason` e rispetta `--target-duration`.
  Per i recap sceglie scene **distribuite** lungo tutto l'episodio (deterministico).
- **LLM (`--use-llm`):** modalità "Claude-in-the-loop", senza API key. Il tool
  esporta la richiesta e un LLM produce le decisioni; poi le reimporti. In futuro
  basta sostituire l'export con una chiamata API (l'helper `build_llm_prompt` è già
  pronto) mantenendo lo stesso contratto di input/output.

### Modalità LLM "Claude-in-the-loop"

Utile in fase di test per far selezionare le scene a un LLM reale senza scrivere
codice di integrazione né configurare API key:

```bash
# 1) Esporta la richiesta di selezione (scrive data/llm_request.json)
python main.py --video input/episode.mp4 --scenes input/scenes.txt \
    --request "combattimenti + finale emozionale" --target-duration 45 --use-llm

# 2) Dai data/llm_request.json a un LLM (o a Claude in chat). L'LLM risponde con
#    un array JSON [{"scene_id", "reason", "keep", "priority"}] che salvi in
#    data/llm_decisions.json

# 3) Rilancia lo stesso comando con le decisioni: il tool le valida e monta l'edit
python main.py --video input/episode.mp4 --scenes input/scenes.txt \
    --request "combattimenti + finale emozionale" --target-duration 45 \
    --use-llm --llm-decisions data/llm_decisions.json
```

Le decisioni vengono validate (scene_id inesistenti scartati, scene non citate
trattate come `keep=false`), così un JSON imperfetto non rompe la pipeline.

---

## Limiti dell'MVP

- I tagli sono accurati a ~0.1s; le scene sono **approssimative** (derivate da
  pause nel parlato o da blocchi temporali), non da analisi visiva dei tagli.
- La comprensione della richiesta è **euristica** (keyword), non semantica reale.
- Ogni clip viene **ri-encodata** (H.264/AAC) per un output affidabile: è più
  lento del semplice copy, ma evita frame neri e desync audio/video.
- Whisper su episodi lunghi può richiedere **diversi minuti** su CPU.

## Accelerazione GPU (Whisper più veloce)

Di default `--device auto` usa la GPU se disponibile, altrimenti la CPU. Su una
macchina con GPU NVIDIA la trascrizione è molto più rapida:

```bash
python main.py --video input/episode.mp4 --request "recap 60s" \
    --device cuda --compute-type float16
```

Se la GPU non è realmente utilizzabile (driver/CUDA mancanti), il tool ripiega
automaticamente su CPU (`int8`) senza interrompersi. Su CPU l'MVP resta
utilizzabile ma più lento sui video lunghi.

> Nota: l'accelerazione CUDA richiede driver NVIDIA e le librerie cuDNN/cuBLAS
> compatibili con CTranslate2 installate sul sistema.

## Possibili evoluzioni

- Integrazione di un LLM reale nel selector (già predisposto).
- Scene detection **visiva** (es. PySceneDetect) per confini più precisi.
- Transizioni/dissolvenze tra le clip.
- Accelerazione GPU per Whisper (`device="cuda"`, `compute_type="float16"`).
- Esportazione anche in altri formati/risoluzioni.

---

## Vincoli / uso responsabile

Il software lavora **solo su file locali** forniti dall'utente. Non scarica video
online, non aggira DRM, non include contenuti protetti da copyright. Usalo solo su
materiale che hai il diritto di modificare.
