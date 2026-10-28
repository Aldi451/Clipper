"""Orkestrasi job: analisis (transkrip + cari momen), render clip, upload YouTube.

Semua berjalan di background thread dengan antrean terpisah supaya 1 pekerjaan berat
tidak mengunci request HTTP dan tidak berebut CPU.
"""
from __future__ import annotations

import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import analyze, db, editor, ingest, media, subs, transcribe, youtube
from .config import WORK_DIR, load_settings


analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analyze")
render_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")
upload_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="upload")

_busy: dict[str, bool] = {}
_busy_lock = threading.Lock()


def _claim(key: str) -> bool:
    with _busy_lock:
        if _busy.get(key):
            return False
        _busy[key] = True
        return True


def _release(key: str) -> None:
    with _busy_lock:
        _busy.pop(key, None)


# --------------------------------------------------------------------------- #
# job helper
# --------------------------------------------------------------------------- #
def _job_set(job_id: str, **fields: Any) -> None:
    db.update_job(job_id, **fields)


def _progress_fn(job_id: str, floor: float, ceil: float) -> Callable[[float, str], None]:
    def inner(fraction: float, message: str = "") -> None:
        value = floor + max(0.0, min(1.0, fraction)) * (ceil - floor)
        fields: dict[str, Any] = {"progress": round(value, 4)}
        if message:
            fields["message"] = message
        _job_set(job_id, **fields)
    return inner


# --------------------------------------------------------------------------- #
# analisis
# --------------------------------------------------------------------------- #
def start_analysis(job_id: str) -> None:
    if not _claim(f"analyze:{job_id}"):
        return
    analysis_pool.submit(_run_analysis, job_id)


def _run_analysis(job_id: str) -> None:
    settings = load_settings()
    job = db.get_job(job_id)
    if not job:
        _release(f"analyze:{job_id}")
        return
    workdir = WORK_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        _job_set(job_id, status="running", stage="memuat", progress=0.01, message="Menyiapkan…", error="")

        source_path = Path(job["source_path"]) if job.get("source_path") else None

        # 1) download kalau sumbernya link
        if (not source_path or not source_path.exists()) and ingest.is_url(job.get("source") or ""):
            _job_set(job_id, stage="download", progress=0.03, message="Mengunduh video dari link…")
            source_path = ingest.download(
                job["source"], job_label=job_id,
                on_progress=_progress_fn(job_id, 0.03, 0.22),
            )
            _job_set(job_id, source_path=str(source_path))
        if not source_path or not source_path.exists():
            raise RuntimeError("Sumber video tidak ditemukan")

        # 2) probe
        _job_set(job_id, stage="probe", progress=0.24, message="Membaca metadata video…")
        info = media.probe(source_path)
        _job_set(job_id,
                 duration=info.duration, width=info.width, height=info.height,
                 meta={**(job.get("meta") or {}), "info": {
                     "duration": info.duration, "width": info.width, "height": info.height,
                     "fps": info.fps, "has_audio": info.has_audio,
                     "has_video": info.has_video, "audio_codec": info.audio_codec,
                 }})
        if info.duration <= 3:
            raise RuntimeError("Video terlalu pendek (minimal 3 detik)")
        if not job.get("title"):
            _job_set(job_id, title=source_path.stem[:80])

        # 3) audio + energi
        audio_path = workdir / "audio.wav"
        _job_set(job_id, stage="audio", progress=0.26, message="Ekstrak audio…")
        energy: list[tuple[float, float]] = []
        if info.has_audio:
            try:
                media.extract_audio(source_path, audio_path)
                _job_set(job_id, stage="audio", progress=0.3, message="Analisa energi audio…")
                energy = media.audio_energy(audio_path, window=0.5)
            except media.FFmpegError:
                energy = []

        # 4) transkrip
        #    - subtitle video (kalau ada) dipakai dulu: instan & tanpa model
        #    - Whisper lokal dipakai untuk timing kata yang presisi (butuh model terunduh)
        transcript = None
        transcript_error = ""
        subtitle = subs.pick_subtitle_file(workdir) or subs.find_sidecar_subtitle(source_path)
        if subtitle:
            _job_set(job_id, stage="subtitle", progress=0.31, message="Membaca subtitle video…")
            transcript = subs.load_subtitle_transcript(subtitle[0], subtitle[1])
            if transcript:
                transcribe.save_transcript(job_id, transcript)

        use_whisper = info.has_audio and transcribe.available() and (
            settings.get("prefer_whisper") or not transcript
        )
        if use_whisper:
            _job_set(job_id, stage="transkrip", progress=0.33,
                     message=f"Transkripsi lokal (Whisper {settings.get('whisper_model')})…")
            try:
                whisper = transcribe.transcribe(
                    audio_path if audio_path.exists() else source_path,
                    on_progress=_progress_fn(job_id, 0.33, 0.78),
                )
                if whisper.get("segments"):
                    transcript = whisper
                    transcribe.save_transcript(job_id, transcript)
                    transcript_error = ""
            except Exception as exc:  # model belum ke-download / OOM
                transcript_error = _friendly_transcribe_error(exc)
                traceback.print_exc()
        elif not transcribe.available() and not transcript:
            transcript_error = "faster-whisper belum terinstall (pip install faster-whisper)"

        # 5) kandidat clip
        _job_set(job_id, stage="analisa", progress=0.8, message="Mencari momen terbaik…")
        if transcript and transcript.get("segments"):
            candidates = analyze.find_candidates(
                transcript, energy, settings, on_progress=_progress_fn(job_id, 0.8, 0.94)
            )
        else:
            candidates = []
        if not candidates:
            candidates = analyze.fixed_windows(info.duration, settings)

        if settings.get("llm_enabled") and analyze.ollama_available(settings) and candidates:
            _job_set(job_id, message="Minta judul ke LLM lokal…")
            analyze.refine_with_llm(candidates, settings)

        # 6) simpan clip
        existing = db.list_clips(job_id)
        for clip in existing:
            if clip["file"]:
                try:
                    Path(clip["file"]).unlink(missing_ok=True)
                except OSError:
                    pass
            db.delete_clip(clip["id"])

        for idx, cand in enumerate(candidates):
            db.create_clip(
                job_id,
                idx=idx,
                start=round(cand.start, 3),
                duration=round(cand.duration, 3),
                score=cand.score,
                title=cand.title,
                hook=cand.hook,
                description=cand.description,
                hashtags=cand.hashtags,
                status="suggested",
                meta={
                    "features": cand.features,
                    "transcript": cand.text[:4000],
                    "language": (transcript or {}).get("language", ""),
                    "caption": bool(transcript),
                },
            )

        message = f"{len(candidates)} clip siap dirender"
        if transcript_error:
            message += f" · transkrip dilewati: {transcript_error[:120]}"
        _job_set(job_id, status="done", stage="selesai", progress=1.0, message=message,
                 error="", meta={**(db.get_job(job_id) or {}).get("meta", {}),
                                 "transcript_error": transcript_error,
                                 "language": (transcript or {}).get("language", "")})

        if settings.get("auto_render") and candidates:
            render_job_all(job_id)

    except Exception as exc:
        traceback.print_exc()
        _job_set(job_id, status="error", stage="gagal", progress=0.0,
                 message="Gagal", error=str(exc)[:600])
    finally:
        _release(f"analyze:{job_id}")


def _friendly_transcribe_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if any(k in low for k in ("connect", "ssl", "download", "locate the file", "certificate", "timed out")):
        return ("model Whisper belum terunduh (butuh internet sekali — jalankan sekali dengan "
                "koneksi, atau set CLIPPER_MODEL_DIR ke folder model offline)")
    if "out of memory" in low or "memory" in low:
        return "RAM/VRAM tidak cukup untuk model ini — coba model 'base' atau 'tiny' di Pengaturan"
    return msg[:300]


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def start_render(clip_id: str) -> None:
    if not _claim(f"render:{clip_id}"):
        return
    render_pool.submit(_run_render, clip_id)


def render_job_all(job_id: str) -> None:
    for clip in db.list_clips(job_id):
        if clip["status"] != "uploaded":
            start_render(clip["id"])


def _run_render(clip_id: str) -> None:
    clip = db.get_clip(clip_id)
    if not clip:
        _release(f"render:{clip_id}")
        return
    job = db.get_job(clip["job_id"])
    try:
        if not job:
            raise RuntimeError("Project tidak ditemukan")
        settings = load_settings()
        source_path = Path(job["source_path"])
        if not source_path.exists():
            raise RuntimeError("File sumber sudah tidak ada (mungkin dihapus)")
        info = media.probe(source_path)
        transcript = transcribe.load_transcript(job["id"])

        db.update_clip(clip_id, status="rendering", progress=0.01, message="Mulai render…", error="")

        def on_progress(fraction: float, message: str) -> None:
            db.update_clip(clip_id, progress=round(fraction, 4), message=message)

        spec = editor.load_or_create_spec(clip, transcript, float(info.duration or 0), settings)
        result = editor.render_spec(clip, spec, source_path, info, on_progress, settings)
        db.update_clip(
            clip_id, status="rendered", progress=1.0, message="Clip siap",
            file=result["file"], thumb=result["thumb"], spec=json.dumps(spec, ensure_ascii=False),
            duration=round(result["duration"], 3),
            meta={**(clip.get("meta") or {}), "srt": result.get("srt", ""), "dirty": False,
                  "resolution": f"{result.get('width')}x{result.get('height')}"},
        )
        editor.cleanup_workdir(job["id"], clip_id)

        if settings.get("auto_pipeline") and settings.get("yt_auto_upload") and youtube.is_connected():
            start_upload(clip_id)
    except Exception as exc:
        traceback.print_exc()
        db.update_clip(clip_id, status="error", message="Render gagal", error=str(exc)[:700])
    finally:
        _release(f"render:{clip_id}")


# --------------------------------------------------------------------------- #
# upload
# --------------------------------------------------------------------------- #
def start_upload(clip_id: str) -> None:
    if not _claim(f"upload:{clip_id}"):
        return
    upload_pool.submit(_run_upload, clip_id)


def _run_upload(clip_id: str) -> None:
    clip = db.get_clip(clip_id)
    if not clip:
        _release(f"upload:{clip_id}")
        return
    try:
        settings = load_settings()
        if not clip.get("file") or not Path(clip["file"]).exists():
            raise RuntimeError("Render clip dulu sebelum upload")
        if not youtube.is_connected():
            raise RuntimeError("Belum login YouTube")
        db.update_clip(clip_id, status="uploading", progress=0.01, message="Mulai upload…", error="")

        def on_progress(fraction: float, message: str) -> None:
            db.update_clip(clip_id, progress=round(fraction, 4), message=message)

        srt = (clip.get("meta") or {}).get("srt") or ""
        result = youtube.upload_video(
            clip["file"],
            title=clip.get("title") or "Clip",
            description=clip.get("description") or "",
            hashtags=clip.get("hashtags") or settings.get("yt_hashtags") or "#Shorts",
            privacy=settings.get("yt_privacy") or "public",
            on_progress=on_progress,
            srt_path=srt if srt and Path(srt).exists() else None,
            category_id=str(settings.get("yt_category") or "24"),
            notify_subscribers=bool(settings.get("yt_notify_subscribers")),
        )
        db.update_clip(
            clip_id, status="uploaded", progress=1.0, message="Terkirim ke YouTube",
            yt_id=result["id"], yt_url=result["url"],
            yt_privacy=result["privacy"], error="",
        )
    except Exception as exc:
        traceback.print_exc()
        db.update_clip(clip_id, status="rendered" if clip.get("file") else "suggested",
                       message="Upload gagal", error=str(exc)[:700])
    finally:
        _release(f"upload:{clip_id}")


# --------------------------------------------------------------------------- #
# util
# --------------------------------------------------------------------------- #
def busy_state() -> dict[str, bool]:
    with _busy_lock:
        return dict(_busy)


def shutdown() -> None:
    for pool in (analysis_pool, render_pool, upload_pool):
        pool.shutdown(wait=False, cancel_futures=True)
