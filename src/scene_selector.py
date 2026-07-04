"""
Selezione delle scene in base alla richiesta dell'utente.

Due modalità BEN SEPARATE:

  Modalità A (rule based)  -> select_scenes_rule_based(...)
      Euristiche su tag/keyword + logica per recap, dialoghi, durata target.

  Modalità B (LLM-ready)   -> select_scenes_llm(...)
      Stessa firma/contratto di input-output, così in futuro si può collegare
      Claude/OpenAI/LLM locale senza toccare il resto della pipeline.
      Per l'MVP la B delega alla A (euristiche), ma l'interfaccia è pronta.

Contratto di output (identico per A e B):
  [
    {"scene_id": 4, "reason": "Important fight scene", "keep": true, "priority": 9},
    ...
  ]
"""

from __future__ import annotations

from typing import Any

from . import utils
from .keywords import REQUEST_INTENTS


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------
def select_scenes(
    user_request: str,
    scenes: list[dict[str, Any]],
    target_duration: float | None = None,
    use_llm: bool = False,
) -> list[dict[str, Any]]:
    """
    Punto d'ingresso unico. Sceglie la modalità e ritorna le decisioni di
    selezione (una per scena, con keep/priority/reason).
    """
    if use_llm:
        return select_scenes_llm(user_request, scenes, target_duration)
    return select_scenes_rule_based(user_request, scenes, target_duration)


# ---------------------------------------------------------------------------
# Modalità B — LLM-ready (placeholder pronto per integrazione futura)
# ---------------------------------------------------------------------------
def select_scenes_llm(
    user_request: str,
    scenes: list[dict[str, Any]],
    target_duration: float | None = None,
) -> list[dict[str, Any]]:
    """
    Funzione dove in futuro si collega un LLM reale.

    Deve ricevere user_request + scenes e restituire la lista di decisioni.
    Per collegare un LLM: costruire un prompt con le scene (id, summary, tags),
    chiamare l'API, parsare il JSON di risposta nello stesso formato.

    Per l'MVP deleghiamo alle euristiche, così il comportamento è già utile.
    """
    utils.log("scene_selector: modalità LLM-ready (fallback euristico attivo).")
    return select_scenes_rule_based(user_request, scenes, target_duration)


# ---------------------------------------------------------------------------
# Modalità A — rule based
# ---------------------------------------------------------------------------
def select_scenes_rule_based(
    user_request: str,
    scenes: list[dict[str, Any]],
    target_duration: float | None = None,
) -> list[dict[str, Any]]:
    """Assegna keep/priority/reason a ogni scena con euristiche multilingua."""
    if not scenes:
        return []

    req = utils.normalize_text(user_request)
    wanted_tags = _intent_tags(req)
    is_recap = _is_recap(req)
    wants_dialogue = "dialogue" in wanted_tags or _has_any(req, ["dialog", "parla", "talk"])

    # Punteggio grezzo per ogni scena.
    scored: list[dict[str, Any]] = []
    for scene in scenes:
        score, reason = _score_scene(scene, wanted_tags, wants_dialogue)
        scored.append({"scene": scene, "score": score, "reason": reason})

    # Recap: se richiesto (o se nessun tag matcha nulla) distribuiamo le scene.
    total_positive = sum(1 for s in scored if s["score"] > 0)
    if is_recap or (wanted_tags and total_positive == 0):
        if not is_recap:
            utils.warn("Nessuna scena corrisponde alla richiesta: creo un recap distribuito.")
        return _select_recap(scored, target_duration)

    return _select_by_score(scored, target_duration)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _score_scene(
    scene: dict[str, Any], wanted_tags: list[str], wants_dialogue: bool
) -> tuple[int, str]:
    """Punteggio 0-10 + motivazione leggibile."""
    tags = scene.get("tags", [])
    matched = [t for t in wanted_tags if t in tags]

    score = 0
    reasons: list[str] = []

    if matched:
        score += 4 + min(len(matched) * 2, 5)  # più tag combaciano, più priorità
        reasons.append("tag: " + ", ".join(matched))

    if wants_dialogue:
        # Scene "parlate" = molto testo trascritto.
        words = len(scene.get("transcript_text", "").split())
        if words >= 20:
            score += 4
            reasons.append(f"dialogo ricco ({words} parole)")
        elif words >= 8:
            score += 2

    # Se non è stato chiesto nulla di specifico, ogni scena vale un minimo.
    if not wanted_tags and not wants_dialogue:
        score = max(score, 1)

    reason = "; ".join(reasons) if reasons else "scena generica"
    return min(score, 10), reason


# ---------------------------------------------------------------------------
# Strategie di selezione con budget durata
# ---------------------------------------------------------------------------
def _select_by_score(
    scored: list[dict[str, Any]], target_duration: float | None
) -> list[dict[str, Any]]:
    """Greedy per punteggio; rispetta il budget di durata se presente."""
    positives = [s for s in scored if s["score"] > 0]
    positives.sort(key=lambda s: (s["score"], -(s["scene"]["end"] - s["scene"]["start"])), reverse=True)

    decisions_map: dict[int, dict[str, Any]] = {}
    used = 0.0

    for item in positives:
        scene = item["scene"]
        dur = scene["end"] - scene["start"]
        if target_duration is not None and used + dur > target_duration and decisions_map:
            continue  # supererebbe il budget: salta (ma tieni almeno una scena)
        decisions_map[scene["scene_id"]] = _decision(scene, item, keep=True)
        used += dur

    return _finalize_decisions(scored, decisions_map)


def _select_recap(
    scored: list[dict[str, Any]], target_duration: float | None
) -> list[dict[str, Any]]:
    """
    Recap: seleziona scene distribuite lungo tutto l'episodio, in modo
    DETERMINISTICO (nessun random), fino a riempire il budget di durata.
    """
    by_time = sorted(scored, key=lambda s: s["scene"]["start"])
    target = target_duration if target_duration is not None else 60.0

    # Quante scene servono, stimando dalla durata media.
    avg = sum(s["scene"]["end"] - s["scene"]["start"] for s in by_time) / len(by_time)
    n_needed = max(1, min(len(by_time), int(round(target / max(avg, 1.0)))))

    # Campionamento uniforme sugli indici -> copertura lungo tutto l'episodio.
    chosen_idx = _even_indices(len(by_time), n_needed)

    decisions_map: dict[int, dict[str, Any]] = {}
    used = 0.0
    for idx in chosen_idx:
        item = by_time[idx]
        scene = item["scene"]
        dur = scene["end"] - scene["start"]
        if target_duration is not None and used + dur > target and decisions_map:
            continue
        item = {**item, "reason": "recap: scena distribuita nell'episodio"}
        decisions_map[scene["scene_id"]] = _decision(scene, item, keep=True)
        used += dur

    return _finalize_decisions(scored, decisions_map)


def _even_indices(n_total: int, n_pick: int) -> list[int]:
    """Indici distribuiti uniformemente in [0, n_total). Deterministico."""
    if n_pick >= n_total:
        return list(range(n_total))
    if n_pick == 1:
        return [n_total // 2]
    step = (n_total - 1) / (n_pick - 1)
    return sorted({int(round(i * step)) for i in range(n_pick)})


# ---------------------------------------------------------------------------
# Helper decisioni
# ---------------------------------------------------------------------------
def _decision(scene: dict[str, Any], item: dict[str, Any], keep: bool) -> dict[str, Any]:
    return {
        "scene_id": scene["scene_id"],
        "reason": item.get("reason", "scena generica"),
        "keep": keep,
        "priority": int(item.get("score", 0)),
    }


def _finalize_decisions(
    scored: list[dict[str, Any]], keep_map: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Produce una decisione per OGNI scena (keep=False per le scartate)."""
    out: list[dict[str, Any]] = []
    for item in scored:
        sid = item["scene"]["scene_id"]
        if sid in keep_map:
            out.append(keep_map[sid])
        else:
            out.append(
                {
                    "scene_id": sid,
                    "reason": "non corrisponde alla richiesta",
                    "keep": False,
                    "priority": int(item["score"]),
                }
            )
    out.sort(key=lambda d: d["scene_id"])
    return out


def _intent_tags(req_norm: str) -> list[str]:
    """Dai token della richiesta ricava i tag rilevanti da cercare."""
    tags: list[str] = []
    for key, mapped in REQUEST_INTENTS.items():
        if key in req_norm:
            for t in mapped:
                if t not in tags:
                    tags.append(t)
    return tags


def _is_recap(req_norm: str) -> bool:
    return _has_any(req_norm, ["recap", "riassunto", "highlight", "sunto", "summary"])


def _has_any(haystack: str, needles: list[str]) -> bool:
    return any(n in haystack for n in needles)
