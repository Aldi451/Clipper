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

from . import analyze, db, editor, ingest, media, speech, subs, tiktok, transcribe, youtube
from .cancellation import OperationCancelled
from .config import WORK_DIR, load_settings
from .fsutil import safe_unlink
from .paths import resolve_media_path


analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analyze")
render_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")
upload_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="upload")

_busy: dict[str, bool] = {}
_cancel_events: dict[str, threading.Event] = {}
_busy_lock = threading.Lock()


def _claim(key: str) -> bool:
    with _busy_lock:
        if _busy.get(key):
            return False
        _busy[key] = True
        _cancel_events[key] = threading.Event()
        return True


def _release(key: str) -> None:
    with _busy_lock:
        _busy.pop(key, None)
        _cancel_events.pop(key, None)


def _cancel_requested(key: str) -> bool:
    with _busy_lock:
        event = _cancel_events.get(key)
        return bool(event and event.is_set())


def _check_cancelled(key: str) -> None:
    if _cancel_requested(key):
        raise OperationCancelled("Proses dibatalkan")


def _seal_cancellation(key: str) -> None:
    """Tutup jendela cancel secara atomik sebelum hasil final disimpan."""
    with _busy_lock:
        event = _cancel_events.get(key)
        if event and event.is_set():
            raise OperationCancelled("Proses dibatalkan")
        _cancel_events.pop(key, None)


def _request_cancel(key: str, message_update: Callable[[], None] | None = None) -> str:
    """Hasil: requested, finishing, atau not_running."""
    with _busy_lock:
        if not _busy.get(key):
            return "not_running"
        event = _cancel_events.get(key)
        if event is None:
            return "finishing"
        if not event.is_set():
            if message_update:
                message_update()
            event.set()
        return "requested"


def cancel_job(job_id: str) -> str:
    return _request_cancel(
        f"analyze:{job_id}",
        lambda: _job_set(job_id, message="Membatalkan persiapan video…"),
    )


def cancel_render(clip_id: str) -> str:
    return _request_cancel(
        f"render:{clip_id}",
        lambda: db.update_clip(clip_id, message="Membatalkan render…"),
    )


# --------------------------------------------------------------------------- #
# job helper
# --------------------------------------------------------------------------- #
def _job_set(job_id: str, **fields: Any) -> None:
    db.update_job(job_id, **fields)


def _progress_fn(job_id: str, floor: float, ceil: float) -> Callable[[float, str], None]:
    key = f"analyze:{job_id}"

    def inner(fraction: float, message: str = "") -> None:
        _check_cancelled(key)
        value = floor + max(0.0, min(1.0, fraction)) * (ceil - floor)
        fields: dict[str, Any] = {"progress": round(value, 4)}
        if message:
            fields["message"] = message
        _job_set(job_id, **fields)
    return inner


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# persiapan video full & analisis AI
# --------------------------------------------------------------------------- #
def is_manual_clip(clip: dict[str, Any]) -> bool:
    meta = clip.get("meta") or {}
    return bool(meta.get("manual") or meta.get("origin") == "manual")


def start_prepare(job_id: str) -> None:
    """Unduh/simpan video sumber full & baca metadata tanpa langsung menjalankan AI."""
    if not _claim(f"analyze:{job_id}"):
        return
    analysis_pool.submit(_run_prepare, job_id)


def _run_prepare(job_id: str) -> None:
    job = db.get_job(job_id)
    if not job:
        _release(f"analyze:{job_id}")
        return
    workdir = WORK_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    key = f"analyze:{job_id}"
    try:
        _check_cancelled(key)
        _job_set(job_id, status="running", stage="memuat", progress=0.02, message="Menyiapkan video full…", error="")

        source_path = resolve_media_path(job.get("source_path"), (WORK_DIR / job_id,))
        if source_path is not None and str(source_path) != (job.get("source_path") or ""):
            _job_set(job_id, source_path=str(source_path))

        # 1) download kalau sumbernya link
        if (not source_path or not source_path.exists()) and ingest.is_url(job.get("source") or ""):
            _job_set(job_id, stage="download", progress=0.05, message="Mengunduh video full dari link…")
            source_path = ingest.download(
                job["source"], job_label=job_id,
                on_progress=_progress_fn(job_id, 0.05, 0.90),
                cancel_check=lambda: _cancel_requested(key),
            )
            _check_cancelled(key)
            _job_set(job_id, source_path=str(source_path))
        if not source_path or not source_path.exists():
            raise RuntimeError("Sumber video tidak ditemukan")

        # 2) probe metadata video full
        _check_cancelled(key)
        _job_set(job_id, stage="probe", progress=0.94, message="Membaca metadata video full…")
        info = media.probe(source_path)
        _check_cancelled(key)
        source_meta = ingest.load_source_meta(workdir) or (job.get("meta") or {}).get("source_meta") or {}
        if info.duration <= 3:
            raise RuntimeError("Video terlalu pendek (minimal 3 detik)")
        current_title = (job.get("title") or "").strip()
        best_title = current_title
        if not current_title or current_title.lower() == "source":
            best_title = str(source_meta.get("title") or source_path.stem)[:80]

        _seal_cancellation(key)
        _job_set(
            job_id,
            title=best_title,
            duration=info.duration,
            width=info.width,
            height=info.height,
            status="done",
            stage="siap_pilih",
            progress=1.0,
            message="Video full siap — pilih Render Hook AI atau Edit Manual di project",
            error="",
            meta={
                **(job.get("meta") or {}),
                "prepared": True,
                "source_meta": source_meta,
                "info": {
                    "duration": info.duration,
                    "width": info.width,
                    "height": info.height,
                    "fps": info.fps,
                    "has_audio": info.has_audio,
                    "has_video": info.has_video,
                    "audio_codec": info.audio_codec,
                },
            },
        )
    except OperationCancelled:
        _job_set(job_id, status="cancelled", stage="dibatalkan", progress=0.0,
                 message="Persiapan video dibatalkan", error="")
    except Exception as exc:
        traceback.print_exc()
        _job_set(job_id, status="error", stage="gagal", progress=0.0,
                 message="Gagal menyiapkan video", error=friendly_error(exc))
    finally:
        _release(f"analyze:{job_id}")


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
    key = f"analyze:{job_id}"
    try:
        _check_cancelled(key)
        _job_set(job_id, status="running", stage="memuat", progress=0.01, message="Menyiapkan…", error="")

        source_path = resolve_media_path(job.get("source_path"), (WORK_DIR / job_id,))
        if source_path is None and job.get("source_path"):
            _job_set(job_id, message="File sumber tidak ada di path lama, mencari di folder data…")
        if source_path is not None and str(source_path) != (job.get("source_path") or ""):
            # path tersimpan masih menunjuk folder lama → perbarui agar konsisten
            _job_set(job_id, source_path=str(source_path))

        # 1) download kalau sumbernya link
        if (not source_path or not source_path.exists()) and ingest.is_url(job.get("source") or ""):
            _job_set(job_id, stage="download", progress=0.03, message="Mengunduh video dari link…")
            source_path = ingest.download(
                job["source"], job_label=job_id,
                on_progress=_progress_fn(job_id, 0.03, 0.22),
                cancel_check=lambda: _cancel_requested(key),
            )
            _check_cancelled(key)
            _job_set(job_id, source_path=str(source_path))
        if not source_path or not source_path.exists():
            raise RuntimeError("Sumber video tidak ditemukan")

        # 2) probe
        _check_cancelled(key)
        _job_set(job_id, stage="probe", progress=0.24, message="Membaca metadata video…")
        info = media.probe(source_path)
        _check_cancelled(key)
        source_meta = ingest.load_source_meta(workdir) or (job.get("meta") or {}).get("source_meta") or {}
        _job_set(job_id,
                 duration=info.duration, width=info.width, height=info.height,
                 meta={**(job.get("meta") or {}), "source_meta": source_meta, "info": {
                     "duration": info.duration, "width": info.width, "height": info.height,
                     "fps": info.fps, "has_audio": info.has_audio,
                     "has_video": info.has_video, "audio_codec": info.audio_codec,
                 }})
        if info.duration <= 3:
            raise RuntimeError("Video terlalu pendek (minimal 3 detik)")
        current_title = (job.get("title") or "").strip()
        if not current_title or current_title.lower() == "source":
            best_title = str(source_meta.get("title") or source_path.stem)[:80]
            _job_set(job_id, title=best_title)
            job["title"] = best_title

        # 3) audio + energi
        audio_path = workdir / "audio.wav"
        _job_set(job_id, stage="audio", progress=0.26, message="Ekstrak audio…")
        energy: list[tuple[float, float]] = []
        if info.has_audio:
            try:
                _check_cancelled(key)
                media.extract_audio(source_path, audio_path)
                _check_cancelled(key)
                _job_set(job_id, stage="audio", progress=0.3, message="Analisa energi audio…")
                energy = media.audio_energy(audio_path, window=0.5)
                _check_cancelled(key)
            except OperationCancelled:
                raise
            except Exception as exc:  # analisa energi tidak boleh menggagalkan job
                print(f"[clipper] analisa energi audio dilewati: {exc}", flush=True)
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
            except OperationCancelled:
                raise
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
            # Bila transkrip tidak tersedia, tetap cari jendela audio di seluruh video;
            # jangan kembali ke potongan berurutan yang selalu dimulai dari 00:00.
            candidates = analyze.fixed_windows(info.duration, settings, energy=energy)

        segments_for_target = (transcript or {}).get("segments") or []
        if segments_for_target:
            span_start = float(segments_for_target[0].get("start") or 0.0)
            span_end = float(segments_for_target[-1].get("end") or info.duration)
            target_span = max(0.0, span_end - span_start)
        else:
            target_span = float(info.duration or 0.0)
        final_target = analyze.target_clip_count(settings, target_span)

        # AI membandingkan kandidat lokal yang lebih luas, lalu kita render-framing
        # hanya kandidat akhir supaya kualitas naik tanpa menggandakan proses CV/CPU.
        ai_refinement = bool(
            candidates
            and any(cand.text.strip() for cand in candidates)
            and (
                analyze.gemini_available(settings)
                or (settings.get("llm_enabled") and settings.get("llm_url"))
            )
        )
        if ai_refinement:
            def _refine_progress(fraction: float, message: str = "") -> None:
                _check_cancelled(key)
                progress = 0.94 + 0.02 * max(0.0, min(1.0, float(fraction)))
                _job_set(job_id, stage="ai_rank", progress=progress,
                         message=message or "Membandingkan momen kandidat dengan AI…")

            _job_set(
                job_id,
                stage="ai_rank",
                progress=0.94,
                message="Membandingkan momen, hook, dan konteks dengan Gemini AI…"
                if analyze.gemini_available(settings)
                else "Membandingkan kandidat dengan LLM lokal…",
            )
            analyze.refine_with_llm(candidates, settings, on_progress=_refine_progress)

        # Skor AI sudah dibaurkan dengan skor lokal jika ranker berhasil. Terapkan
        # quality gate setelah tahap itu agar kandidat lemah tidak mengisi kuota.
        # Fallback tanpa transkrip dilewatkan oleh helper karena skalanya berbeda.
        quality_threshold = int(settings.get("min_clip_score", 55))
        has_transcript_candidates = any(str(cand.text or "").strip() for cand in candidates)
        candidates, quality_rejected = analyze.apply_min_clip_score(candidates, quality_threshold)

        # Jumlah clip adalah batas maksimum; setelah gate, boleh tersisa lebih sedikit.
        if len(candidates) > final_target:
            candidates = analyze.select_candidate_subset(candidates, final_target)

        # 5b) auto-framing hanya untuk momen yang terpilih akhir.
        framing_results: dict[int, dict[str, Any]] = {}
        tpl = editor.template_by_id(str(settings.get("default_template") or "")) or {}
        effective_layout = str(tpl.get("layout") or settings.get("layout") or "")
        if (settings.get("auto_framing", True) or effective_layout in ("crop", "gameplay")) and candidates:
            try:
                from . import framing as framing_module

                if framing_module.available() and source_path:
                    gameplay_count = 0
                    frame_start = 0.96 if ai_refinement else 0.94
                    frame_span = 0.025
                    for index, cand in enumerate(candidates):
                        _check_cancelled(key)
                        _job_set(job_id, stage="framing",
                                 progress=frame_start + frame_span * index / max(1, len(candidates)),
                                 message=f"Mendeteksi pembicara / gameplay clip {index + 1}/{len(candidates)}…")
                        ctx = {
                            **(source_meta if isinstance(source_meta, dict) else {}),
                            "job_title": str(job.get("title") or ""),
                            "transcript": cand.text[:1500],
                        }
                        result = framing_module.analyze(
                            source_path, cand.start, cand.duration, context=ctx,
                        )
                        framing_results[index] = result
                        if result.get("kind") == "gameplay":
                            gameplay_count += 1
                            cand.features["gameplay"] = 1.0
                        elif result.get("ok"):
                            cand.features["framing_moves"] = float(result.get("moves") or 0)
                    if gameplay_count:
                        _job_set(job_id, message="Video gameplay terdeteksi: atas gameplay, bawah host…")
                    else:
                        _job_set(job_id, message="Pembicara terdeteksi, kamera akan mengikuti…")
            except OperationCancelled:
                raise
            except Exception:  # noqa: BLE001 - framing opsional, jangan gagalkan job
                traceback.print_exc()

        _check_cancelled(key)
        _job_set(job_id, stage="finalisasi", progress=0.99,
                 message="Menyusun hasil kandidat klip…")

        # Batalkan selama analisa masih berlangsung; finalisasi hasil DB tidak lagi dibatalkan.
        _seal_cancellation(key)

        # 6) simpan clip AI (JANGAN hapus clip manual milik user!)
        existing = db.list_clips(job_id)
        for clip in existing:
            if is_manual_clip(clip):
                continue
            for old_ref in (clip.get("file"), clip.get("thumb"), (clip.get("meta") or {}).get("srt")):
                old_file = resolve_media_path(old_ref, (WORK_DIR / clip["job_id"],))
                if old_file is not None:
                    safe_unlink(old_file)
            db.delete_clip(clip["id"])

        # 5c) hook vs ucapan: kalau hook hasil parafrase tidak ada di ucapan,
        # tandai supaya UI bisa menawarkan versi verbatim (teks == suara).
        hook_speech: dict[str, dict[str, Any]] = {}
        for idx, cand in enumerate(candidates):
            if not transcript:
                continue
            try:
                segs = editor.normalize_segments(
                    [{"start": cand.start, "end": cand.start + cand.duration, "speed": 1.0}],
                    {"start": cand.start, "duration": cand.duration}, float((job or {}).get("duration") or 0),
                )
                spoken = speech.words_in_output(transcript, segs)
                check = speech.verify_text(cand.hook, spoken)
                hook_speech[idx] = {
                    "ok": check["ok"], "ratio": check["ratio"],
                    "speech_text": speech.hook_from_speech(spoken)["text"],
                }
            except Exception:
                continue

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
                    "origin": "ai",
                    "manual": False,
                    "features": cand.features,
                    "hook": getattr(cand, "hook_info", {}) or {},
                    "hook_speech": hook_speech.get(idx, {}),
                    "framing": framing_results.get(idx, {}),
                    "transcript": cand.text[:4000],
                    "language": (transcript or {}).get("language", ""),
                    "caption": bool(transcript),
                },
            )

        message = f"{len(candidates)} clip siap dirender"
        if quality_rejected:
            message += f" · {quality_rejected} kandidat di bawah skor minimum {quality_threshold}"
        if transcript_error:
            message += f" · transkrip dilewati: {transcript_error[:120]}"
        _job_set(job_id, status="done", stage="selesai", progress=1.0, message=message,
                 error="", meta={**(db.get_job(job_id) or {}).get("meta", {}),
                                 "ai_done": True,
                                 "transcript_error": transcript_error,
                                 "language": (transcript or {}).get("language", ""),
                                 "clip_target": final_target,
                                 "quality_gate": {
                                     "min_score": quality_threshold,
                                     "applied": bool(quality_threshold > 0 and has_transcript_candidates),
                                     "rejected": quality_rejected,
                                 }})

        if settings.get("auto_render") and candidates:
            render_job_all(job_id, mode="ai")

    except OperationCancelled:
        _job_set(job_id, status="cancelled", stage="dibatalkan", progress=0.0,
                 message="Proses analisis dibatalkan", error="")
    except Exception as exc:
        traceback.print_exc()
        _job_set(job_id, status="error", stage="gagal", progress=0.0,
                 message="Gagal", error=friendly_error(exc))
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



def friendly_error(exc: Exception) -> str:
    """Ubah error teknis (khususnya Windows) jadi pesan yang bisa ditindaklanjuti."""
    msg = str(exc) or exc.__class__.__name__
    msg = ingest.strip_ansi(msg)  # buang kode warna dari yt-dlp/ffmpeg
    low = msg.lower()
    if "winerror 32" in low or "being used by another process" in low or "errno 32" in low:
        return ("File sedang dipakai proses lain (antivirus, pemutar video, atau preview di "
                "browser). Tutup preview/lainnya lalu klik Coba lagi. · detail: " + msg[:180])
    if "winerror 5" in low or "access is denied" in low or "errno 13" in low:
        return ("Akses file ditolak. Pindahkan folder Clipper ke lokasi yang bisa ditulis "
                "(mis. D:\\Clipper) atau jalankan sebagai pengguna biasa. · detail: " + msg[:180])
    if "no space left" in low or "not enough space" in low:
        return "Disk penuh — kosongkan ruang lalu coba lagi. · detail: " + msg[:180]
    if "no such file or directory" in low or "cannot find the file" in low:
        return "File sumber tidak ditemukan (mungkin dihapus/dipindah). Unggah ulang videonya. · detail: " + msg[:180]
    return msg[:600]

# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def start_render(clip_id: str) -> bool:
    """Queue one clip and persist its queued state before returning to the UI."""
    key = f"render:{clip_id}"
    if not _claim(key):
        return False
    try:
        # Set this synchronously, before the worker probes the source. Otherwise the
        # client can refresh before the worker marks the clip as rendering and miss
        # the entire render (including its terminal error) because it never polls.
        db.update_clip(
            clip_id,
            status="queued",
            progress=0.0,
            message="Menunggu render…",
            error="",
        )
        render_pool.submit(_run_render, clip_id)
    except Exception as exc:
        _release(key)
        try:
            db.update_clip(
                clip_id,
                status="error",
                message="Render gagal dimulai",
                error=friendly_error(exc),
            )
        except Exception:
            pass
        raise
    return True


def render_job_all(job_id: str, only_dirty: bool = False, mode: str | None = None) -> int:
    """Antre render semua clip project. `only_dirty` = hanya yang perlu render ulang."""
    queued = 0
    for clip in db.list_clips(job_id):
        if mode == "ai" and is_manual_clip(clip):
            continue
        if mode == "manual" and not is_manual_clip(clip):
            continue
        if clip["status"] == "uploaded":
            continue
        if only_dirty and not (clip.get("meta") or {}).get("dirty"):
            continue
        if start_render(clip["id"]):
            queued += 1
    return queued


def _run_render(clip_id: str) -> None:
    key = f"render:{clip_id}"
    clip = db.get_clip(clip_id)
    if not clip:
        _release(f"render:{clip_id}")
        return
    job = db.get_job(clip["job_id"])
    try:
        _check_cancelled(key)
        if not job:
            raise RuntimeError("Project tidak ditemukan")
        settings = load_settings()
        source_path = resolve_media_path(job.get("source_path"), (WORK_DIR / job["id"],))
        if source_path is None:
            raise RuntimeError("File sumber sudah tidak ada — jalankan repair.bat "
                               "(atau analisis ulang project)")
        if str(source_path) != (job.get("source_path") or ""):
            # path tersimpan masih menunjuk folder lama → rapikan sekalian
            db.update_job(job["id"], source_path=str(source_path))
            job["source_path"] = str(source_path)
        _check_cancelled(key)
        info = media.probe(source_path)
        _check_cancelled(key)
        transcript = transcribe.load_transcript(job["id"])

        _check_cancelled(key)
        db.update_clip(clip_id, status="rendering", progress=0.01, message="Mulai render…", error="")

        def on_progress(fraction: float, message: str) -> None:
            _check_cancelled(key)
            db.update_clip(clip_id, progress=round(fraction, 4), message=message)

        # Jika clip dibuat dengan detektor framing versi lama (atau belum pernah dianalisa),
        # jalankan deteksi pembicara/gameplay otomatis sebelum render agar otomatis fokus ke host.
        _check_cancelled(key)
        if settings.get("auto_framing", True):
            try:
                from . import framing as framing_module

                cur_meta = dict(clip.get("meta") or {})
                cur_framing = cur_meta.get("framing") if isinstance(cur_meta.get("framing"), dict) else {}
                if (
                    framing_module.available()
                    and int(cur_framing.get("detector_v") or 0) < framing_module.DETECTOR_VERSION
                ):
                    db.update_clip(clip_id, progress=0.03, message="Mendeteksi host / gameplay…")
                    job_meta = (job.get("meta") or {}) if isinstance(job, dict) else {}
                    src_meta = (
                        job_meta.get("source_meta")
                        if isinstance(job_meta.get("source_meta"), dict)
                        else {}
                    ) or ingest.load_source_meta(WORK_DIR / str(job["id"]))
                    ctx = {
                        **src_meta,
                        "job_title": str(job.get("title") or ""),
                        "clip_title": str(clip.get("title") or ""),
                        "hook": str(clip.get("hook") or ""),
                        "transcript": str(cur_meta.get("transcript") or "")[:1500],
                    }
                    f_res = framing_module.analyze(
                        source_path,
                        float(clip.get("start") or 0.0),
                        float(clip.get("duration") or 30.0),
                        context=ctx,
                    )
                    cur_meta["framing"] = f_res
                    db.update_clip(clip_id, meta=cur_meta)
                    clip["meta"] = cur_meta
            except OperationCancelled:
                raise
            except Exception:
                traceback.print_exc()

        _check_cancelled(key)
        spec = editor.load_or_create_spec(clip, transcript, float(info.duration or 0), settings)
        result = editor.render_spec(
            clip, spec, source_path, info, on_progress, settings,
            cancel_check=lambda: _cancel_requested(key),
            before_commit=lambda: _seal_cancellation(key),
        )
        db.update_clip(
            clip_id, status="rendered", progress=1.0, message="Clip siap",
            file=result["file"], thumb=result["thumb"], spec=json.dumps(spec, ensure_ascii=False),
            duration=round(result["duration"], 3),
            meta={**(clip.get("meta") or {}), "srt": result.get("srt", ""), "dirty": False,
                  "resolution": f"{result.get('width')}x{result.get('height')}"},
        )
        editor.cleanup_workdir(job["id"], clip_id)

        if settings.get("auto_pipeline"):
            want_yt = bool(settings.get("yt_auto_upload") and youtube.is_connected())
            want_tt = bool(
                settings.get("tt_enabled") and settings.get("tt_auto_upload") and tiktok.ready()
            )
            if want_yt and want_tt:
                start_upload(clip_id, "both")
            elif want_yt:
                start_upload(clip_id, "youtube")
            elif want_tt:
                start_upload(clip_id, "tiktok")
    except OperationCancelled:
        if job:
            editor.cleanup_workdir(job["id"], clip_id)
        db.update_clip(
            clip_id, status="cancelled", progress=0.0,
            message="Render dibatalkan", error="",
        )
    except Exception as exc:
        traceback.print_exc()
        db.update_clip(clip_id, status="error", message="Render gagal", error=friendly_error(exc))
    finally:
        _release(f"render:{clip_id}")


# --------------------------------------------------------------------------- #
# upload
# --------------------------------------------------------------------------- #
def start_upload(clip_id: str, target: str = "youtube") -> None:
    """Antre upload. `target`: youtube | tiktok | both."""
    target = (target or "youtube").lower()
    if target == "both":
        start_upload(clip_id, "youtube")
        start_upload(clip_id, "tiktok")
        return
    if not _claim(f"upload:{target}:{clip_id}"):
        return
    upload_pool.submit(_run_upload, clip_id, target)


def _run_upload(clip_id: str, target: str = "youtube") -> None:
    key = f"upload:{target}:{clip_id}"
    clip = db.get_clip(clip_id)
    if not clip:
        _release(key)
        return
    try:
        settings = load_settings()
        clip_file = resolve_media_path(clip.get("file"), (WORK_DIR / clip["job_id"],))
        if clip_file is None:
            raise RuntimeError("Render clip dulu sebelum upload")
        if target == "tiktok":
            _upload_tiktok(clip, clip_file, settings)
        else:
            _upload_youtube(clip, clip_file, settings)
    except Exception as exc:
        if not isinstance(exc, tiktok.TikTokError):
            traceback.print_exc()
        # error OAuth / browser dijelaskan dalam Bahasa Indonesia
        detail = youtube.friendly_error(str(exc), "Upload gagal")
        if target == "tiktok":
            detail = str(exc)[:700]
        latest = db.get_clip(clip_id) or clip
        already = bool(latest.get("yt_url") or latest.get("tk_url"))
        fallback_status = "uploaded" if already else ("rendered" if latest.get("file") else "suggested")
        db.update_clip(clip_id, status=fallback_status,
                       message=f"Upload {_target_label(target)} gagal", error=detail[:700])
    finally:
        _release(key)


def _target_label(target: str) -> str:
    return "TikTok" if target == "tiktok" else "YouTube"


def _upload_youtube(clip: dict[str, Any], clip_file: Path, settings: dict[str, Any]) -> None:
    clip_id = clip["id"]
    if not youtube.is_connected():
        raise RuntimeError("Belum login YouTube")
    db.update_clip(clip_id, status="uploading", progress=0.01, message="Mulai upload ke YouTube…",
                   error="")

    def on_progress(fraction: float, message: str) -> None:
        db.update_clip(clip_id, progress=round(fraction, 4), message=message)

    srt_raw = (clip.get("meta") or {}).get("srt") or ""
    srt_resolved = resolve_media_path(srt_raw, (WORK_DIR / clip["job_id"],)) if srt_raw else None
    result = youtube.upload_video(
        str(clip_file),
        title=clip.get("title") or "Clip",
        description=clip.get("description") or "",
        hashtags=clip.get("hashtags") or settings.get("yt_hashtags") or "#Shorts",
        privacy=settings.get("yt_privacy") or "public",
        on_progress=on_progress,
        srt_path=str(srt_resolved) if srt_resolved and srt_resolved.exists() else None,
        category_id=str(settings.get("yt_category") or "24"),
        notify_subscribers=bool(settings.get("yt_notify_subscribers")),
    )
    db.update_clip(
        clip_id, status="uploaded", progress=1.0, message="Terkirim ke YouTube",
        yt_id=result["id"], yt_url=result["url"],
        yt_privacy=result["privacy"], error="",
    )


def _upload_tiktok(clip: dict[str, Any], clip_file: Path, settings: dict[str, Any]) -> None:
    clip_id = clip["id"]
    db.update_clip(clip_id, status="uploading", progress=0.02, message="Menyiapkan upload TikTok…",
                   error="")

    def on_progress(fraction: float, message: str) -> None:
        db.update_clip(clip_id, progress=round(fraction, 4), message=message)

    caption = tiktok.build_caption(
        clip.get("title") or "Clip",
        clip.get("description") or "",
        clip.get("hashtags") or settings.get("tt_hashtags") or "#fyp",
    )
    result = tiktok.upload_video(
        str(clip_file),
        caption=caption,
        hashtags="",
        privacy=str(settings.get("tt_privacy") or "public"),
        auto_post=bool(settings.get("tt_auto_post", True)),
        on_progress=on_progress,
        wait_seconds=float(settings.get("tt_login_wait") or 300) + 300,
    )
    already = bool(clip.get("yt_url"))
    db.update_clip(
        clip_id,
        status="uploaded" if (result["posted"] or already) else "rendered",
        progress=1.0,
        message="Terkirim ke TikTok" if result["posted"] else "TikTok: cek jendela browser",
        tk_id=(result["url"].rsplit("/", 1)[-1] if result.get("url") else ""),
        tk_url=result.get("url") or "",
        meta={**(clip.get("meta") or {}), "tk_posted": bool(result.get("posted"))},
        error="",
    )


# --------------------------------------------------------------------------- #
# util
# --------------------------------------------------------------------------- #
def busy_state() -> dict[str, bool]:
    with _busy_lock:
        return dict(_busy)


def shutdown() -> None:
    for pool in (analysis_pool, render_pool, upload_pool):
        pool.shutdown(wait=False, cancel_futures=True)
