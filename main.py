#!/usr/bin/env python3
"""
Anime Scene Cutter MVP — CLI.

Pipeline end-to-end:
  1. estrae l'audio dal video          (src/audio.py)
  2. trascrive con timestamp           (src/transcriber.py)  [o transcript.txt]
  3. costruisce scene approssimative   (src/scene_builder.py)
  4. seleziona le scene rilevanti      (src/scene_selector.py)
  5. genera l'edit plan JSON           (src/edit_plan.py)
  6. taglia e concatena il video       (src/video_editor.py)

Esempio:
  python main.py --video input/episode.mp4 \
      --request "crea un edit con solo scene di combattimento" \
      --target-duration 60

Test rapido senza avere un video:
  python main.py --make-sample-video input/sample.mp4
  python main.py --video input/sample.mp4 --request "create a 60 second recap"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src import (
    audio,
    edit_plan as edit_plan_mod,
    scene_builder,
    scene_selector,
    transcriber,
    transcript_loader,
    utils,
    video_editor,
)

# Cartelle di lavoro (relative alla root del progetto).
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
CLIPS_DIR = OUTPUT_DIR / "clips"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="anime-scene-cutter",
        description="Taglia automaticamente un episodio in base a una richiesta in linguaggio naturale.",
    )
    p.add_argument("--video", help="Path del video MP4 di input (obbligatorio, salvo --make-sample-video).")
    p.add_argument("--transcript", help="transcript.txt opzionale (contesto o timeline se ha timestamp).")
    p.add_argument("--scenes", help="scenes.txt opzionale (descrizioni scene, con o senza timestamp).")
    p.add_argument("--request", help="Richiesta utente, es: 'crea un edit con solo scene di combattimento'.")
    p.add_argument("--target-duration", type=float, default=None,
                   help="Durata target dell'edit in secondi (opzionale).")
    p.add_argument("--language", default="auto",
                   help="Lingua per Whisper (es. it, en). Default: auto.")
    p.add_argument("--whisper-model", default="small",
                   help="Modello Whisper (tiny/base/small/medium/large-v3). Default: small.")
    p.add_argument("--use-llm", action="store_true",
                   help="Usa la modalità LLM-ready del selector (per l'MVP delega alle euristiche).")
    p.add_argument("--dry-run", action="store_true",
                   help="Genera solo data/edit_plan.json senza tagliare il video.")
    p.add_argument("--keep-temp", action="store_true",
                   help="Non cancellare le clip temporanee e il WAV.")
    p.add_argument("--make-sample-video", metavar="PATH",
                   help="Genera un MP4 di test in PATH ed esci (utile per provare la pipeline).")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    utils.require_binaries()
    utils.ensure_dirs(DATA_DIR, OUTPUT_DIR, CLIPS_DIR)

    # --- modalità utility: genera un video di test ed esci -------------------
    if args.make_sample_video:
        out = Path(args.make_sample_video)
        video_editor.make_sample_video(out)
        utils.log(f"Video di test creato: {out}")
        return 0

    # --- validazione argomenti obbligatori -----------------------------------
    if not args.video:
        utils.die("--video è obbligatorio (oppure usa --make-sample-video per un test).")
    if not args.request:
        utils.die("--request è obbligatorio (es: --request \"create a 60 second recap\").")

    video_path = Path(args.video)
    if not video_path.exists():
        utils.die(f"Video non trovato: {video_path}")

    # --- metadati video ------------------------------------------------------
    try:
        info = utils.probe_video(video_path)
    except RuntimeError as e:
        utils.die(str(e))
    utils.log(f"Video: {info}")

    # --- 1-2. segmenti temporizzati (transcript esterno o Whisper) -----------
    segments = _obtain_segments(args, video_path, info)
    utils.write_json(DATA_DIR / "transcript_segments.json", segments)

    # --- descrizioni scene esterne (opzionali) -------------------------------
    scene_markers = None
    if args.scenes:
        loaded = transcript_loader.load_scenes(Path(args.scenes))
        if loaded["has_timestamps"]:
            scene_markers = loaded["markers"]

    # --- 3. costruzione scene ------------------------------------------------
    scenes = scene_builder.build_scenes(segments, info.duration, scene_markers)
    if not scenes:
        utils.die("Impossibile costruire scene dal video fornito.")
    utils.write_json(DATA_DIR / "scenes.json", scenes)

    # --- 4. selezione --------------------------------------------------------
    decisions = scene_selector.select_scenes(
        user_request=args.request,
        scenes=scenes,
        target_duration=args.target_duration,
        use_llm=args.use_llm,
    )

    # --- 5. edit plan --------------------------------------------------------
    plan = edit_plan_mod.build_edit_plan(
        source_video=str(video_path),
        user_request=args.request,
        scenes=scenes,
        decisions=decisions,
        target_duration=args.target_duration,
    )
    utils.write_json(DATA_DIR / "edit_plan.json", plan)

    n_selected = len(plan["clips"])
    est = edit_plan_mod.estimated_duration(plan)

    if n_selected == 0:
        utils.die(
            "Nessuna scena selezionata per questa richiesta.\n"
            "Suggerimento: prova 'crea un recap di 60 secondi' o cambia i termini della richiesta."
        )

    # --- 6. render (salvo dry-run) -------------------------------------------
    final_path = OUTPUT_DIR / "final_edit.mp4"
    if args.dry_run:
        utils.log("Modalità --dry-run: generato solo l'edit plan (nessun taglio video).")
        final_path = None
    else:
        try:
            video_editor.render_edit(
                edit_plan=plan,
                video_path=video_path,
                info=info,
                clips_dir=CLIPS_DIR,
                output_path=final_path,
                keep_temp=args.keep_temp,
            )
        except RuntimeError as e:
            utils.die(str(e))

    _print_summary(len(scenes), n_selected, est, final_path)
    return 0


def _obtain_segments(args, video_path: Path, info) -> list[dict]:
    """
    Ricava i segmenti temporizzati.
    - Se transcript.txt ha timestamp -> usali direttamente (niente Whisper).
    - Altrimenti estrai audio e trascrivi con Whisper.
    - Un transcript SENZA timestamp resta solo contesto (non altera la timeline).
    """
    if args.transcript:
        loaded = transcript_loader.load_transcript(Path(args.transcript))
        if loaded["has_timestamps"]:
            utils.log("Uso i timestamp dal transcript fornito (Whisper saltato).")
            return loaded["segments"]

    # Serve Whisper: estrai audio.
    wav_path = DATA_DIR / "audio.wav"
    try:
        audio.extract_audio(video_path, wav_path)
    except RuntimeError as e:
        utils.die(str(e))

    segments = transcriber.transcribe(
        wav_path,
        model_name=args.whisper_model,
        language=args.language,
    )

    if not args.keep_temp and wav_path.exists():
        wav_path.unlink()

    return segments


def _print_summary(n_scenes: int, n_selected: int, est_duration: float, final_path: Path | None) -> None:
    print("\n" + "=" * 48)
    print("  ANIME SCENE CUTTER — RIEPILOGO")
    print("=" * 48)
    print(f"  Scene individuate : {n_scenes}")
    print(f"  Scene selezionate : {n_selected}")
    print(f"  Durata stimata    : {est_duration:.1f}s ({utils.format_timestamp(est_duration)})")
    print(f"  Edit plan         : {DATA_DIR / 'edit_plan.json'}")
    if final_path:
        print(f"  Video finale      : {final_path}")
    else:
        print("  Video finale      : (dry-run, non generato)")
    print("=" * 48 + "\n")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        utils.die("Interrotto dall'utente.", code=130)
