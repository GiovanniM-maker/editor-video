"""
Generazione dell'edit plan a partire dalle scene e dalle decisioni di selezione.

L'edit plan è il "contratto" tra la parte di analisi (scene/selezione) e la
parte di rendering (video_editor): un JSON che descrive esattamente quali clip
tagliare, in che ordine, da dove a dove e perché.
"""

from __future__ import annotations

from typing import Any


def build_edit_plan(
    source_video: str,
    user_request: str,
    scenes: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    target_duration: float | None,
) -> dict[str, Any]:
    """
    Combina scene + decisioni in un piano di montaggio.

    Le clip sono ordinate per tempo (start crescente) così il risultato
    rispetta la cronologia dell'episodio. Gli overlap eventuali vengono uniti.
    """
    scene_by_id = {s["scene_id"]: s for s in scenes}

    clips: list[dict[str, Any]] = []
    for dec in decisions:
        if not dec.get("keep"):
            continue
        scene = scene_by_id.get(dec["scene_id"])
        if scene is None:
            continue
        clips.append(
            {
                "scene_id": scene["scene_id"],
                "start": scene["start"],
                "end": scene["end"],
                "reason": dec.get("reason", ""),
                "priority": dec.get("priority", 0),
            }
        )

    clips.sort(key=lambda c: c["start"])
    clips = _merge_overlaps(clips)

    return {
        "source_video": source_video,
        "user_request": user_request,
        "target_duration": target_duration,
        "clips": clips,
    }


def estimated_duration(edit_plan: dict[str, Any]) -> float:
    """Durata totale stimata dell'edit (somma delle clip)."""
    return sum(c["end"] - c["start"] for c in edit_plan.get("clips", []))


def _merge_overlaps(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unisce clip che si sovrappongono o si toccano, per evitare doppioni."""
    if not clips:
        return []
    merged = [dict(clips[0])]
    for clip in clips[1:]:
        last = merged[-1]
        if clip["start"] <= last["end"]:
            # overlap: estendi la clip precedente
            last["end"] = max(last["end"], clip["end"])
            if clip.get("reason") and clip["reason"] not in last.get("reason", ""):
                last["reason"] = f'{last["reason"]}; {clip["reason"]}'.strip("; ")
        else:
            merged.append(dict(clip))
    return merged
