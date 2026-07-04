"""
Trascrizione audio -> segmenti con timestamp usando faster-whisper.

Nota MVP:
- Il modello Whisper viene SCARICATO da internet al primo utilizzo e poi
  messo in cache locale (~/.cache). Serve rete solo la prima volta.
- Se faster-whisper non è installato, diamo un errore chiaro invece di crashare.

Output: lista di dict {start, end, text}.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import utils


def transcribe(
    wav_path: Path,
    model_name: str = "small",
    language: str | None = None,
) -> list[dict[str, Any]]:
    """
    Trascrive il WAV in segmenti temporizzati.

    Args:
        wav_path: file audio WAV.
        model_name: dimensione modello (tiny/base/small/medium/large-v3).
        language: codice lingua (es. 'it', 'en'); None = autodetect.

    Returns:
        Lista di segmenti [{'start': float, 'end': float, 'text': str}, ...].
        Può essere vuota se l'audio non contiene parlato.
    """
    try:
        from faster_whisper import WhisperModel  # import lazy: pesante
    except ImportError:
        utils.die(
            "faster-whisper non è installato.\n"
            "Installa le dipendenze con:  pip install -r requirements.txt\n"
            "Oppure salta la trascrizione fornendo un transcript con timestamp "
            "(--transcript) o usa un video muto (verrà usato il fallback a intervalli)."
        )

    utils.log(f"Carico il modello Whisper '{model_name}' (download al primo uso)...")
    # compute_type='int8' -> veloce e leggero su CPU, scelta sensata per un MVP.
    model = WhisperModel(model_name, device="cpu", compute_type="int8")

    # language=None lascia decidere l'autodetect a Whisper.
    lang = None if (language in (None, "", "auto")) else language

    utils.log("Trascrizione in corso (può richiedere alcuni minuti)...")
    segments_iter, info = model.transcribe(str(wav_path), language=lang, vad_filter=True)

    detected = getattr(info, "language", None)
    if detected:
        utils.log(f"Lingua rilevata/usata: {detected}")

    segments: list[dict[str, Any]] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append(
            {
                "start": round(float(seg.start), 3),
                "end": round(float(seg.end), 3),
                "text": text,
            }
        )

    utils.log(f"Segmenti trascritti: {len(segments)}")
    return segments
