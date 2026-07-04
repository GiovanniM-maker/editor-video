"""
Import di trascrizioni esterne (transcript.txt) e descrizioni scene (scenes.txt).

Regole (come da specifica MVP):
- transcript.txt: se ha timestamp riga per riga li usiamo come segmenti;
  altrimenti è solo contesto semantico (la timeline resta quella di Whisper).
- scenes.txt: righe tipo 'HH:MM:SS - descrizione'. Se hanno timestamp li usiamo
  per definire i confini scena; altrimenti sono solo contesto.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import utils

# Riga con timestamp iniziale, es:
#   "00:03:10 - Il protagonista entra nel dungeon."
#   "12.4 -> 18.9  qualcosa"   (anche range)
#   "[00:01:02] testo"
_TS_LINE = re.compile(
    r"^\s*[\[\(]?\s*"
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?:\s*(?:-|->|—|→|>)\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?|\d+(?:[.,]\d+)?))?"
    r"\s*[\]\)]?\s*[-–—:]*\s*"
    r"(?P<text>.*)$"
)


def load_transcript(path: Path) -> dict[str, Any]:
    """
    Carica transcript.txt.

    Returns un dict:
      {
        'has_timestamps': bool,
        'segments': [{'start','end','text'}, ...],  # popolato solo se has_timestamps
        'context_text': str                          # testo intero come contesto
      }
    """
    lines = _read_lines(path)
    segments = _parse_timestamped_lines(lines)
    context_text = "\n".join(lines).strip()

    if segments:
        utils.log(f"transcript.txt: trovate {len(segments)} righe con timestamp.")
        return {"has_timestamps": True, "segments": segments, "context_text": context_text}

    utils.log("transcript.txt: nessun timestamp, uso il testo solo come contesto semantico.")
    return {"has_timestamps": False, "segments": [], "context_text": context_text}


def load_scenes(path: Path) -> dict[str, Any]:
    """
    Carica scenes.txt.

    Returns un dict:
      {
        'has_timestamps': bool,
        'markers': [{'start': float, 'description': str}, ...],  # ordinati per start
        'context_text': str
      }
    """
    lines = _read_lines(path)
    markers: list[dict[str, Any]] = []

    for line in lines:
        m = _TS_LINE.match(line)
        if not m:
            continue
        start = utils.parse_timestamp(m.group("start"))
        if start is None:
            continue
        description = (m.group("text") or "").strip()
        markers.append({"start": start, "description": description})

    markers.sort(key=lambda x: x["start"])
    context_text = "\n".join(lines).strip()

    if markers:
        utils.log(f"scenes.txt: trovati {len(markers)} marker temporali.")
        return {"has_timestamps": True, "markers": markers, "context_text": context_text}

    utils.log("scenes.txt: nessun timestamp, uso il testo solo come contesto.")
    return {"has_timestamps": False, "markers": [], "context_text": context_text}


# ---------------------------------------------------------------------------
# Helper interni
# ---------------------------------------------------------------------------
def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        utils.die(f"File non trovato: {path}")
    raw = path.read_text(encoding="utf-8", errors="replace")
    return [ln.rstrip() for ln in raw.splitlines() if ln.strip()]


def _parse_timestamped_lines(lines: list[str]) -> list[dict[str, Any]]:
    """Estrae segmenti {start,end,text} dalle righe che iniziano con timestamp."""
    segments: list[dict[str, Any]] = []
    for line in lines:
        m = _TS_LINE.match(line)
        if not m:
            continue
        start = utils.parse_timestamp(m.group("start"))
        if start is None:
            continue
        end = utils.parse_timestamp(m.group("end")) if m.group("end") else None
        text = (m.group("text") or "").strip()
        segments.append({"start": start, "end": end, "text": text})

    # Se mancano gli 'end', li deriviamo dallo start della riga successiva.
    for i, seg in enumerate(segments):
        if seg["end"] is None:
            if i + 1 < len(segments):
                seg["end"] = segments[i + 1]["start"]
            else:
                seg["end"] = seg["start"] + 5.0  # ultima riga: durata di cortesia
    return segments
