"""
Estrazione audio dal video sorgente.

Whisper lavora bene su WAV mono a 16 kHz: estraiamo esattamente quel formato
per massimizzare qualità/velocità della trascrizione.
"""

from __future__ import annotations

from pathlib import Path

from . import utils


def extract_audio(video_path: Path, out_wav: Path) -> Path:
    """
    Estrae la traccia audio in WAV mono 16 kHz PCM.
    Ritorna il path del WAV creato. Solleva RuntimeError su errore ffmpeg.
    """
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",                 # sovrascrivi senza chiedere
        "-i", str(video_path),
        "-vn",                # niente video
        "-ac", "1",           # mono
        "-ar", "16000",       # 16 kHz (formato atteso da Whisper)
        "-c:a", "pcm_s16le",  # WAV standard
        str(out_wav),
    ]
    utils.run_ffmpeg(cmd, description=f"estrazione audio da '{video_path.name}'")
    return out_wav
