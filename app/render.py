"""Render clip 9:16 (1080x1920) dengan blur background / crop, caption, dan hook."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from . import captions, media
from .config import CLIP_DIR, WORK_DIR, ffmpeg_exe, load_settings

ProgressFn = Callable[[float, str], None]


class RenderError(RuntimeError):
    pass


def _words_from_segments(transcript: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Kalau whisper tidak memberi word timing, bikin perkiraan dari durasi segmen."""
    if not transcript:
        return []
    words = captions.collect_words(transcript)
    if words:
        return words
    approx: list[dict[str, Any]] = []
    for seg in transcript.get("segments") or []:
        tokens = (seg.get("text") or "").split()
        if not tokens:
            continue
        span = max(0.2, float(seg["end"]) - float(seg["start"]))
        step = span / len(tokens)
        for i, token in enumerate(tokens):
            approx.append({
                "start": float(seg["start"]) + i * step,
                "end": float(seg["start"]) + (i + 1) * step,
                "text": token,
            })
    return approx


def _video_chain(has_video: bool, layout: str, focus: float, fps: int) -> str:
    if not has_video:
        return f"color=c=0x0A0A12:s={captions.CANVAS_W}x{captions.CANVAS_H}:r={fps}[vbase]"
    focus = max(0.0, min(1.0, float(focus)))
    if layout == "crop":
        return (
            f"[0:v]fps={fps},scale={captions.CANVAS_W}:{captions.CANVAS_H}"
            f":force_original_aspect_ratio=increase,"
            f"crop={captions.CANVAS_W}:{captions.CANVAS_H}"
            f":x='(iw-{captions.CANVAS_W})*{focus:.3f}':y='(ih-{captions.CANVAS_H})/2',"
            f"setsar=1[vbase]"
        )
    return (
        f"[0:v]fps={fps},split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={captions.CANVAS_W}:{captions.CANVAS_H}"
        f":force_original_aspect_ratio=increase,crop={captions.CANVAS_W}:{captions.CANVAS_H},"
        f"gblur=sigma=30,eq=brightness=-0.12:saturation=1.15,setsar=1[bgb];"
        f"[fgsrc]scale={captions.CANVAS_W}:{captions.CANVAS_H}"
        f":force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[vbase]"
    )


def render_clip(
    clip: dict[str, Any],
    source_path: str | Path,
    source_info: media.MediaInfo,
    transcript: dict[str, Any] | None,
    on_progress: ProgressFn | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    clip_id = clip["id"]
    duration_total = max(0.5, float(source_info.duration or 0))
    start = max(0.0, min(float(clip.get("start") or 0), max(0.0, duration_total - 1.0)))
    duration = float(clip.get("duration") or 30)
    duration = max(2.0, min(duration, max(2.0, duration_total - start)))

    workdir = WORK_DIR / clip["job_id"] / "render" / clip_id
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = CLIP_DIR / f"{clip_id}.mp4"
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    thumb_path = CLIP_DIR / f"{clip_id}.jpg"

    def report(fraction: float, message: str) -> None:
        if on_progress:
            on_progress(max(0.0, min(1.0, fraction)), message)

    report(0.01, "Menyiapkan caption…")

    clip_meta = clip.get("meta") or {}
    use_captions = settings.get("captions")
    if clip_meta.get("captions") is False:
        use_captions = False

    words = _words_from_segments(transcript)
    caption_file = workdir / "captions.ass"
    hook_file = workdir / "hook.ass"
    has_caption = False
    has_hook = False
    if use_captions and words:
        has_caption = captions.build_caption_ass(words, start, start + duration, caption_file, settings)
    if settings.get("hook_overlay") and (clip.get("hook") or clip.get("title")):
        has_hook = captions.build_hook_ass(
            clip.get("hook") or clip.get("title") or "", hook_file, duration, settings
        )

    # --- input & filter ---
    fps = int(settings.get("fps") or 30)
    if fps not in (24, 25, 30, 60):
        fps = 30
    has_video = bool(source_info.has_video)
    focus = clip_meta.get("focus")
    if not isinstance(focus, (int, float)):
        focus = float(settings.get("crop_focus") or 0.5)
    filters = [_video_chain(has_video, str(settings.get("layout") or "fit"), float(focus), fps)]
    last = "vbase"
    if has_caption:
        filters.append(f"[{last}]ass=captions.ass[vcap]")
        last = "vcap"
    if has_hook:
        filters.append(f"[{last}]ass=hook.ass[vout]")
        last = "vout"
    if last != "vout":
        filters.append(f"[{last}]null[vout]")
    filter_complex = ";".join(filters)

    cmd: list[str] = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-nostats"]
    if has_video:
        cmd += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source_path)]
        a_index = "0:a"
    else:
        cmd += [
            "-f", "lavfi", "-t", f"{duration:.3f}",
            "-i", f"color=c=0x0A0A12:s={captions.CANVAS_W}x{captions.CANVAS_H}:r={fps}",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source_path),
        ]
        a_index = "1:a"

    cmd += ["-filter_complex", filter_complex, "-map", "[vout]"]
    audio_filters: list[str] = []
    if settings.get("normalize_audio"):
        audio_filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    audio_filters.append("aresample=48000")
    if source_info.has_audio:
        cmd += ["-map", f"{a_index}?", "-af", ",".join(audio_filters), "-c:a", "aac", "-b:a", "160k", "-ac", "2"]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(int(settings.get("crf") or 20)),
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-r", str(fps), "-g", str(fps * 2),
        "-movflags", "+faststart", "-t", f"{duration:.3f}",
        "-progress", "pipe:1", str(out_path),
    ]

    report(0.05, "Render video 9:16…")

    def ffprog(fraction: float) -> None:
        report(0.05 + 0.88 * fraction, f"Render {fraction * 100:.0f}%")

    try:
        media.run_with_progress(cmd, duration, ffprog, cwd=workdir)
    except media.FFmpegError as exc:
        raise RenderError(str(exc)) from exc
    if not out_path.exists() or out_path.stat().st_size < 1024:
        raise RenderError("Hasil render kosong — coba lagi dengan pengaturan berbeda")

    report(0.95, "Bikin thumbnail…")
    try:
        media.thumb(out_path, min(1.5, duration / 3), thumb_path)
    except Exception:
        thumb_path = None  # type: ignore[assignment]

    srt_path = workdir / "captions.srt"
    try:
        srt_path.write_text(_build_srt(transcript, start, start + duration), encoding="utf-8")
    except Exception:
        srt_path = None  # type: ignore[assignment]

    report(1.0, "Selesai")
    return {
        "file": str(out_path),
        "thumb": str(thumb_path) if thumb_path else "",
        "srt": str(srt_path) if srt_path and srt_path.exists() else "",
        "duration": duration,
    }


def _build_srt(transcript: dict[str, Any] | None, start: float, end: float) -> str:
    if not transcript:
        return ""
    rows: list[str] = []
    index = 1
    for seg in transcript.get("segments") or []:
        seg_start = float(seg["start"])
        seg_end = float(seg["end"])
        if seg_end <= start or seg_start >= end:
            continue
        local_start = max(0.0, seg_start - start)
        local_end = min(end - start, seg_end - start)
        if local_end <= local_start:
            continue
        rows.append(
            f"{index}\n{_srt_ts(local_start)} --> {_srt_ts(local_end)}\n{seg['text'].strip()}\n"
        )
        index += 1
    return "\n".join(rows)


def _srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d},{ms:03d}"


def cleanup_render_workdir(job_id: str, clip_id: str | None = None) -> None:
    base = WORK_DIR / job_id / "render"
    target = base / clip_id if clip_id else base
    shutil.rmtree(target, ignore_errors=True)
