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
    device: str = "auto",
    compute_type: str = "auto",
) -> list[dict[str, Any]]:
    """
    Trascrive il WAV in segmenti temporizzati.

    Args:
        wav_path: file audio WAV.
        model_name: dimensione modello (tiny/base/small/medium/large-v3).
        language: codice lingua (es. 'it', 'en'); None = autodetect.
        device: 'auto' (usa GPU se disponibile), 'cpu' o 'cuda'.
        compute_type: 'auto' (int8 su CPU, float16 su GPU) oppure un valore
            esplicito di CTranslate2 (int8/int8_float16/float16/float32).

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

    resolved_device = _resolve_device(device)
    resolved_compute = _resolve_compute_type(compute_type, resolved_device)

    utils.log(
        f"Carico il modello Whisper '{model_name}' su {resolved_device} "
        f"(compute={resolved_compute}, download al primo uso)..."
    )
    try:
        model = WhisperModel(model_name, device=resolved_device, compute_type=resolved_compute)
    except (ValueError, RuntimeError) as e:
        # GPU non realmente disponibile / driver mancanti: ripiego su CPU.
        if resolved_device != "cpu":
            utils.warn(f"Device '{resolved_device}' non utilizzabile ({e}); ripiego su CPU int8.")
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
        else:
            raise

    # language=None lascia decidere l'autodetect a Whisper.
    lang = None if (language in (None, "", "auto")) else language

    utils.log("Trascrizione in corso (può richiedere alcuni minuti)...")
    segments: list[dict[str, Any]] = []
    try:
        segments_iter, info = model.transcribe(str(wav_path), language=lang, vad_filter=True)

        detected = getattr(info, "language", None)
        if detected:
            utils.log(f"Lingua rilevata/usata: {detected}")

        # L'iterazione è lazy: eventuali errori interni (es. audio senza parlato
        # con il filtro VAD) emergono qui, non alla chiamata sopra.
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
    except Exception as e:
        # Audio senza parlato rilevabile o glitch del VAD: non è un errore fatale.
        # Ritorniamo lista vuota così la pipeline usa il fallback a scene fisse.
        utils.warn(f"Trascrizione senza risultati ({e}); procedo senza parlato.")
        return []

    utils.log(f"Segmenti trascritti: {len(segments)}")
    return segments


# ---------------------------------------------------------------------------
# Risoluzione device / compute_type
# ---------------------------------------------------------------------------
def _cuda_available() -> bool:
    """Rileva una GPU CUDA utilizzabile da CTranslate2, senza dipendere da torch."""
    try:
        from ctranslate2 import get_cuda_device_count  # fornito da faster-whisper
        return get_cuda_device_count() > 0
    except Exception:
        return False


def _resolve_device(device: str) -> str:
    """'auto' -> 'cuda' se disponibile, altrimenti 'cpu'. Passa attraverso cpu/cuda."""
    device = (device or "auto").lower()
    if device == "auto":
        return "cuda" if _cuda_available() else "cpu"
    return device


def _resolve_compute_type(compute_type: str, device: str) -> str:
    """
    'auto' -> int8 su CPU, float16 su GPU. Un valore esplicito viene rispettato.
    """
    if compute_type and compute_type.lower() != "auto":
        return compute_type
    return "float16" if device == "cuda" else "int8"
