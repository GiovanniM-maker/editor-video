"""
Utility comuni: logging, controllo dipendenze, ffprobe, parsing tempo, path.

Tutto ciò che è "infrastruttura" trasversale sta qui, così gli altri moduli
restano piccoli e leggibili.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Logging semplice (niente librerie esterne per un MVP)
# ---------------------------------------------------------------------------
def log(message: str) -> None:
    """Messaggio informativo su stdout."""
    print(f"[info] {message}")


def warn(message: str) -> None:
    """Avviso su stderr (non blocca l'esecuzione)."""
    print(f"[warn] {message}", file=sys.stderr)


def die(message: str, code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    """Stampa un errore leggibile ed esce. Usato per errori 'utente'."""
    print(f"[error] {message}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------------------
# Preflight: ffmpeg/ffprobe devono esistere prima di iniziare
# ---------------------------------------------------------------------------
def require_binaries() -> None:
    """Verifica che ffmpeg e ffprobe siano nel PATH, altrimenti esce."""
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        die(
            f"Binari mancanti nel PATH: {', '.join(missing)}.\n"
            "Installa ffmpeg (include ffprobe):\n"
            "  - macOS:         brew install ffmpeg\n"
            "  - Ubuntu/Debian: sudo apt install ffmpeg\n"
            "  - Windows:       https://www.gyan.dev/ffmpeg/builds/ (aggiungi al PATH)"
        )


# ---------------------------------------------------------------------------
# ffprobe: metadati del video
# ---------------------------------------------------------------------------
class VideoInfo:
    """Contenitore semplice per i metadati del video sorgente."""

    def __init__(self, duration: float, width: int, height: int, fps: float, has_audio: bool):
        self.duration = duration
        self.width = width
        self.height = height
        self.fps = fps
        self.has_audio = has_audio

    def __repr__(self) -> str:
        return (
            f"VideoInfo(duration={self.duration:.2f}s, {self.width}x{self.height}, "
            f"{self.fps:.3f}fps, audio={self.has_audio})"
        )


def probe_video(video_path: Path) -> VideoInfo:
    """
    Ricava durata, risoluzione, fps e presenza audio tramite ffprobe.
    Solleva RuntimeError se il file non è leggibile.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe non riesce a leggere '{video_path}':\n{result.stderr.strip()}")

    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise RuntimeError(f"Nessuno stream video trovato in '{video_path}'.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    # Durata: preferisci quella del container, con fallback sullo stream.
    duration = _to_float(fmt.get("duration")) or _to_float(video_stream.get("duration")) or 0.0

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    fps = _parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))

    if duration <= 0:
        raise RuntimeError(f"Durata non valida per '{video_path}' (0s?).")

    return VideoInfo(duration=duration, width=width, height=height, fps=fps, has_audio=has_audio)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_fps(rate: str | None) -> float:
    """Converte 'num/den' (es. '30000/1001') in float; default 25 se assente."""
    if not rate:
        return 25.0
    try:
        if "/" in rate:
            num, den = rate.split("/")
            den_f = float(den)
            return float(num) / den_f if den_f else 25.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 25.0


# ---------------------------------------------------------------------------
# Tempo: parsing/formatting timestamp HH:MM:SS(.ms)
# ---------------------------------------------------------------------------
def parse_timestamp(text: str) -> float | None:
    """
    Converte una stringa tempo in secondi (float).
    Accetta: 'SS', 'MM:SS', 'HH:MM:SS', con eventuali decimali (virgola o punto).
    Ritorna None se non è un timestamp valido.
    """
    text = text.strip().replace(",", ".")
    if not text:
        return None
    parts = text.split(":")
    try:
        parts_f = [float(p) for p in parts]
    except ValueError:
        return None
    if len(parts_f) == 1:
        return parts_f[0]
    if len(parts_f) == 2:
        return parts_f[0] * 60 + parts_f[1]
    if len(parts_f) == 3:
        return parts_f[0] * 3600 + parts_f[1] * 60 + parts_f[2]
    return None


def format_timestamp(seconds: float) -> str:
    """Formatta secondi come HH:MM:SS (per output leggibile)."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Testo: normalizzazione per matching accent/case-insensitive
# ---------------------------------------------------------------------------
def normalize_text(text: str) -> str:
    """
    Minuscolo + rimozione accenti, per un matching keyword robusto in IT/EN.
    Es: 'Combattimento È Iniziato' -> 'combattimento e iniziato'.
    """
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text


# ---------------------------------------------------------------------------
# JSON e cartelle
# ---------------------------------------------------------------------------
def write_json(path: Path, data: Any) -> None:
    """Scrive JSON indentato UTF-8, creando la cartella se serve."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dirs(*dirs: Path) -> None:
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def run_ffmpeg(cmd: list[str], description: str) -> None:
    """
    Esegue un comando ffmpeg catturando lo stderr; solleva RuntimeError con
    messaggio leggibile in caso di fallimento.
    """
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # ffmpeg è verboso: mostriamo solo le ultime righe utili.
        tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"ffmpeg fallito durante: {description}\n{tail}")
