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

from pathlib import Path
from typing import Any

from . import utils
from .keywords import REQUEST_INTENTS


class LLMInputRequired(Exception):
    """
    Sollevata dalla modalità LLM quando serve l'input di un LLM esterno.

    Il tool ha esportato la richiesta di selezione (data/llm_request.json); un
    LLM (per ora Claude "in the loop", in futuro un'API) deve produrre le
    decisioni e ripassarle con --llm-decisions.
    """

    def __init__(self, request_path: Path):
        self.request_path = request_path
        super().__init__(str(request_path))


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------
def select_scenes(
    user_request: str,
    scenes: list[dict[str, Any]],
    target_duration: float | None = None,
    use_llm: bool = False,
    llm_decisions_path: Path | None = None,
    llm_request_path: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Punto d'ingresso unico. Sceglie la modalità e ritorna le decisioni di
    selezione (una per scena, con keep/priority/reason).
    """
    if use_llm:
        return select_scenes_llm(
            user_request, scenes, target_duration,
            decisions_path=llm_decisions_path,
            request_path=llm_request_path,
        )
    return select_scenes_rule_based(user_request, scenes, target_duration)


# ---------------------------------------------------------------------------
# Modalità B — LLM (Claude-in-the-loop ora, API in futuro)
# ---------------------------------------------------------------------------
def select_scenes_llm(
    user_request: str,
    scenes: list[dict[str, Any]],
    target_duration: float | None = None,
    decisions_path: Path | None = None,
    request_path: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Selezione delegata a un LLM.

    Flusso "Claude-in-the-loop" (nessuna API key richiesta):
      1. se NON esistono decisioni pronte -> esporta la richiesta in
         `request_path` (data/llm_request.json) e solleva LLMInputRequired.
         Un LLM legge quel file e scrive le decisioni in JSON.
      2. si rilancia con --llm-decisions <file>: le decisioni vengono caricate,
         validate e usate.

    In futuro il passo 1 può chiamare direttamente un'API (Claude/OpenAI/local):
    basta sostituire l'export+raise con una chiamata che ritorna lo stesso JSON.
    """
    # Passo 2: decisioni già disponibili -> caricale e validale.
    if decisions_path and Path(decisions_path).exists():
        utils.log(f"scene_selector[LLM]: carico le decisioni da {decisions_path}.")
        raw = utils.read_json(Path(decisions_path))
        return _validate_llm_decisions(raw, scenes)

    # Passo 1: esporta la richiesta per l'LLM e fermati.
    req_path = Path(request_path) if request_path else Path("data/llm_request.json")
    _export_llm_request(req_path, user_request, scenes, target_duration)
    raise LLMInputRequired(req_path)


def build_llm_prompt(request_payload: dict[str, Any]) -> str:
    """
    Costruisce il prompt testuale da dare a un LLM reale (helper per l'integrazione
    futura via API). Non usato nel flusso Claude-in-the-loop, ma pronto all'uso.
    """
    return (
        "Sei un montatore video. Data la richiesta dell'utente e l'elenco di scene, "
        "decidi quali tenere.\n"
        f"RICHIESTA: {request_payload['user_request']}\n"
        f"DURATA TARGET (s): {request_payload['target_duration']}\n"
        "SCENE (JSON):\n"
        f"{request_payload['scenes']}\n\n"
        "Rispondi SOLO con un array JSON di oggetti "
        '{\"scene_id\": int, \"reason\": str, \"keep\": bool, \"priority\": int(0-10)}.'
    )


def _export_llm_request(
    path: Path,
    user_request: str,
    scenes: list[dict[str, Any]],
    target_duration: float | None,
) -> None:
    """Scrive un pacchetto compatto e leggibile per l'LLM."""
    compact = [
        {
            "scene_id": s["scene_id"],
            "start": s["start"],
            "end": s["end"],
            "duration": round(s["end"] - s["start"], 2),
            "auto_summary": s.get("auto_summary", ""),
            "tags": s.get("tags", []),
            "transcript_preview": (s.get("transcript_text", "") or "")[:200],
        }
        for s in scenes
    ]
    payload = {
        "user_request": user_request,
        "target_duration": target_duration,
        "instructions": (
            "Restituisci un array JSON di decisioni, una per scena da tenere "
            "(o tutte con keep true/false). Formato di ogni elemento: "
            '{"scene_id": int, "reason": str, "keep": bool, "priority": int 0-10}. '
            "Rispetta la durata target sommando le durate delle scene con keep=true."
        ),
        "scenes": compact,
    }
    utils.write_json(path, payload)


def _validate_llm_decisions(
    raw: Any, scenes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    Valida/normalizza le decisioni prodotte dall'LLM.

    - accetta sia una lista diretta sia {"decisions": [...]}
    - tiene solo scene_id esistenti
    - completa le scene mancanti con keep=False (contratto: una per scena)
    """
    if isinstance(raw, dict) and "decisions" in raw:
        raw = raw["decisions"]
    if not isinstance(raw, list):
        utils.die("Il file di decisioni LLM deve contenere un array JSON di decisioni.")

    valid_ids = {s["scene_id"] for s in scenes}
    by_id: dict[int, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or "scene_id" not in item:
            continue
        sid = item["scene_id"]
        if sid not in valid_ids:
            continue
        by_id[sid] = {
            "scene_id": sid,
            "reason": str(item.get("reason", "")).strip() or "selezionata dall'LLM",
            "keep": bool(item.get("keep", True)),
            "priority": int(item.get("priority", 5)),
        }

    # Contratto: una decisione per OGNI scena.
    decisions = []
    for s in scenes:
        sid = s["scene_id"]
        decisions.append(
            by_id.get(sid, {
                "scene_id": sid,
                "reason": "non indicata dall'LLM",
                "keep": False,
                "priority": 0,
            })
        )
    kept = sum(1 for d in decisions if d["keep"])
    utils.log(f"scene_selector[LLM]: {kept} scene tenute su {len(scenes)}.")
    return decisions


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
