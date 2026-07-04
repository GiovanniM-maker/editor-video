"""
Costruzione della timeline di scene approssimative.

Logica MVP (in ordine di priorità):
1. Se scenes.txt ha timestamp -> quelli definiscono i confini scena.
2. Altrimenti raggruppa i segmenti Whisper: nuova scena quando c'è una pausa
   > SILENCE_GAP secondi, oppure quando la scena supera MAX_SCENE_LEN.
3. Se non ci sono segmenti (video muto / niente parlato) -> fallback:
   scene a intervalli fissi FIXED_CHUNK secondi lungo tutta la durata.

Ogni scena: scene_id, start, end, transcript_text, external_description?,
auto_summary, tags. I confini sono resi CONTINUI (end scena = start successiva)
così il montaggio finale non "salta" nei silenzi.
"""

from __future__ import annotations

from typing import Any

from . import utils
from .keywords import TAG_KEYWORDS

# Parametri di segmentazione (sensati per anime/episodi TV).
SILENCE_GAP = 2.5      # pausa oltre la quale ipotizziamo un cambio scena
MAX_SCENE_LEN = 60.0   # una scena non dovrebbe superare i 60s
MIN_SCENE_LEN = 1.0    # scarta/mergia frammenti sotto 1s
FIXED_CHUNK = 45.0     # dimensione blocchi nel fallback senza parlato


def build_scenes(
    segments: list[dict[str, Any]],
    total_duration: float,
    scene_markers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Costruisce la lista di scene.

    Args:
        segments: segmenti {start,end,text} (da Whisper o transcript timestampato).
        total_duration: durata totale del video (da ffprobe).
        scene_markers: eventuali marker {start, description} da scenes.txt.
    """
    if scene_markers:
        scenes = _scenes_from_markers(scene_markers, segments, total_duration)
    elif segments:
        scenes = _scenes_from_segments(segments, total_duration)
    else:
        utils.warn("Nessun parlato rilevato: uso il fallback a intervalli fissi.")
        scenes = _scenes_fixed_chunks(total_duration)

    scenes = _finalize(scenes, total_duration)
    utils.log(f"Scene costruite: {len(scenes)}")
    return scenes


# ---------------------------------------------------------------------------
# Strategie di segmentazione
# ---------------------------------------------------------------------------
def _scenes_from_segments(
    segments: list[dict[str, Any]], total_duration: float
) -> list[dict[str, Any]]:
    """Raggruppa i segmenti per pause e lunghezza massima."""
    scenes: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if current:
            scenes.append(_make_scene(current))

    for seg in segments:
        if not current:
            current.append(seg)
            continue

        prev_end = current[-1]["end"]
        gap = seg["start"] - prev_end
        span = seg["end"] - current[0]["start"]

        if gap > SILENCE_GAP or span > MAX_SCENE_LEN:
            flush()
            current = [seg]
        else:
            current.append(seg)
    flush()
    return scenes


def _scenes_from_markers(
    markers: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    total_duration: float,
) -> list[dict[str, Any]]:
    """Usa i marker di scenes.txt come confini; assegna il testo dei segmenti."""
    scenes: list[dict[str, Any]] = []
    for i, marker in enumerate(markers):
        start = marker["start"]
        end = markers[i + 1]["start"] if i + 1 < len(markers) else total_duration
        # segmenti che cadono (anche parzialmente) nella finestra
        inside = [s for s in segments if s["end"] > start and s["start"] < end]
        text = " ".join(s["text"] for s in inside).strip()
        scenes.append(
            {
                "start": start,
                "end": end,
                "transcript_text": text,
                "external_description": marker.get("description", "") or None,
            }
        )
    return scenes


def _scenes_fixed_chunks(total_duration: float) -> list[dict[str, Any]]:
    """Fallback: blocchi di durata fissa quando non c'è testo."""
    scenes: list[dict[str, Any]] = []
    start = 0.0
    while start < total_duration:
        end = min(start + FIXED_CHUNK, total_duration)
        scenes.append(
            {
                "start": start,
                "end": end,
                "transcript_text": "",
                "external_description": None,
            }
        )
        start = end
    return scenes


# ---------------------------------------------------------------------------
# Costruzione singola scena + finalizzazione
# ---------------------------------------------------------------------------
def _make_scene(segs: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join(s["text"] for s in segs).strip()
    return {
        "start": segs[0]["start"],
        "end": segs[-1]["end"],
        "transcript_text": text,
        "external_description": None,
    }


def _finalize(scenes: list[dict[str, Any]], total_duration: float) -> list[dict[str, Any]]:
    """
    Rende i confini continui, applica durata minima, calcola tags/auto_summary
    e assegna scene_id progressivi.
    """
    if not scenes:
        return []

    # Ordina e rendi continui i confini (end = start della successiva).
    scenes.sort(key=lambda s: s["start"])
    for i in range(len(scenes) - 1):
        scenes[i]["end"] = scenes[i + 1]["start"]
    scenes[-1]["end"] = max(scenes[-1]["end"], min(scenes[-1]["start"] + MIN_SCENE_LEN, total_duration))
    scenes[-1]["end"] = min(scenes[-1]["end"], total_duration)

    # Filtra frammenti troppo corti (li assorbiamo estendendo il precedente).
    cleaned: list[dict[str, Any]] = []
    for scene in scenes:
        if scene["end"] - scene["start"] < MIN_SCENE_LEN and cleaned:
            cleaned[-1]["end"] = scene["end"]
            cleaned[-1]["transcript_text"] = (
                cleaned[-1]["transcript_text"] + " " + scene["transcript_text"]
            ).strip()
        else:
            cleaned.append(scene)

    # Metadati derivati + id.
    for idx, scene in enumerate(cleaned, start=1):
        scene["scene_id"] = idx
        scene["start"] = round(scene["start"], 3)
        scene["end"] = round(scene["end"], 3)
        scene["auto_summary"] = _summarize(scene["transcript_text"], scene.get("external_description"))
        scene["tags"] = _extract_tags(scene["transcript_text"], scene.get("external_description"))
        # riordina le chiavi per output pulito
    return [_ordered(s) for s in cleaned]


def _ordered(scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": scene["scene_id"],
        "start": scene["start"],
        "end": scene["end"],
        "transcript_text": scene["transcript_text"],
        "external_description": scene.get("external_description"),
        "auto_summary": scene["auto_summary"],
        "tags": scene["tags"],
    }


def _summarize(transcript_text: str, external: str | None) -> str:
    """
    auto_summary rule-based: preferisci la descrizione esterna, altrimenti le
    prime ~15 parole del parlato. Nessun LLM richiesto.
    """
    if external:
        return external
    words = transcript_text.split()
    if not words:
        return "(nessun parlato in questa scena)"
    summary = " ".join(words[:15])
    return summary + ("..." if len(words) > 15 else "")


def _extract_tags(transcript_text: str, external: str | None) -> list[str]:
    """Assegna i tag cercando le keyword del vocabolario nel testo normalizzato."""
    haystack = utils.normalize_text(f"{transcript_text} {external or ''}")
    tags: list[str] = []
    for tag, words in TAG_KEYWORDS.items():
        if any(utils.normalize_text(w) in haystack for w in words):
            tags.append(tag)
    return tags
