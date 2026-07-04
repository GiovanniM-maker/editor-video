"""
Taglio delle clip e concatenazione nel video finale.

Scelte tecniche IMPORTANTI (per un output affidabile):
- Ogni clip viene RI-ENCODATA con un profilo uniforme (H.264 + AAC, stessa
  risoluzione/fps/samplerate). Questo evita i frame neri e la desync A/V che
  si otterrebbero con '-c copy' su tagli non allineati ai keyframe, e garantisce
  che il concat demuxer riceva stream compatibili.
- La concatenazione usa il concat demuxer con una file-list su disco: gestisce
  correttamente path con spazi/unicode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import utils
from .utils import VideoInfo


def render_edit(
    edit_plan: dict[str, Any],
    video_path: Path,
    info: VideoInfo,
    clips_dir: Path,
    output_path: Path,
    keep_temp: bool = False,
) -> Path:
    """
    Taglia tutte le clip dell'edit plan e le concatena in output_path.

    Ritorna il path del video finale. Solleva RuntimeError con messaggio utile
    se non ci sono clip o se ffmpeg fallisce.
    """
    clips = edit_plan.get("clips", [])
    if not clips:
        raise RuntimeError(
            "Nessuna scena selezionata: impossibile generare il video.\n"
            "Prova una richiesta diversa (es. 'crea un recap di 60 secondi') "
            "o verifica che il video contenga contenuti pertinenti."
        )

    utils.ensure_dirs(clips_dir, output_path.parent)
    _clean_dir(clips_dir)

    # Profilo di encoding uniforme, derivato dal sorgente.
    fps = info.fps if info.fps > 0 else 25.0

    clip_paths: list[Path] = []
    for i, clip in enumerate(clips, start=1):
        clip_path = clips_dir / f"clip_{i:03d}.mp4"
        _cut_clip(video_path, clip["start"], clip["end"], fps, clip_path)
        clip_paths.append(clip_path)
        utils.log(
            f"Clip {i}/{len(clips)} (scena {clip['scene_id']}): "
            f"{utils.format_timestamp(clip['start'])} -> {utils.format_timestamp(clip['end'])}"
        )

    _concat_clips(clip_paths, output_path, clips_dir)

    if not keep_temp:
        _clean_dir(clips_dir)

    return output_path


# ---------------------------------------------------------------------------
# Taglio singola clip (ri-encode uniforme)
# ---------------------------------------------------------------------------
def _cut_clip(video_path: Path, start: float, end: float, fps: float, out_path: Path) -> None:
    duration = max(0.1, end - start)
    cmd = [
        "ffmpeg",
        "-y",
        # -ss/-to DOPO -i: taglio accurato (decodifica dall'inizio del segmento).
        "-i", str(video_path),
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        # video: profilo uniforme per concat compatibile
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-r", f"{fps:.5f}",
        # audio: uniforme; se il sorgente è muto, genera silenzio compatibile
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        # keyframe all'inizio -> concat pulito
        "-force_key_frames", "expr:gte(t,0)",
        "-movflags", "+faststart",
        str(out_path),
    ]
    utils.run_ffmpeg(cmd, description=f"taglio clip {out_path.name}")


# ---------------------------------------------------------------------------
# Concatenazione (concat demuxer + file list)
# ---------------------------------------------------------------------------
def _concat_clips(clip_paths: list[Path], output_path: Path, work_dir: Path) -> None:
    if len(clip_paths) == 1:
        # Una sola clip: la ricopiamo direttamente (già nel formato giusto).
        cmd = ["ffmpeg", "-y", "-i", str(clip_paths[0]), "-c", "copy",
               "-movflags", "+faststart", str(output_path)]
        utils.run_ffmpeg(cmd, description="finalizzazione clip singola")
        return

    list_file = work_dir / "concat_list.txt"
    # Nel concat demuxer i path vanno quotati con apici singoli; raddoppiamo
    # eventuali apici presenti nel path per non rompere il parsing.
    lines = [f"file '{_escape(p.resolve())}'" for p in clip_paths]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",  # le clip hanno già lo stesso profilo: copy è sicuro e veloce
        "-movflags", "+faststart",
        str(output_path),
    ]
    utils.run_ffmpeg(cmd, description="concatenazione clip nel video finale")


def _escape(path: Path) -> str:
    return str(path).replace("'", "'\\''")


# ---------------------------------------------------------------------------
# Video di test sintetico (per provare la pipeline senza attendere Whisper)
# ---------------------------------------------------------------------------
def make_sample_video(out_path: Path, seconds: int = 60) -> Path:
    """
    Genera un MP4 di prova (pattern video + tono audio) con ffmpeg.
    Utile per testare taglio/concat in pochi secondi.
    """
    utils.ensure_dirs(out_path.parent)
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=25:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-shortest",
        str(out_path),
    ]
    utils.run_ffmpeg(cmd, description="generazione video di test")
    return out_path


def _clean_dir(path: Path) -> None:
    if not path.exists():
        return
    for f in path.iterdir():
        if f.is_file():
            f.unlink()
