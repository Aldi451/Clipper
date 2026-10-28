"""API + server Clipper (FastAPI). Jalankan: python run.py"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import analyze, db, discover, editor, ingest, media, pipeline, transcribe, youtube
from .config import (
    APP_NAME, ASSET_DIR, MAX_UPLOAD_BYTES, VERSION, WEB_DIR, WORK_DIR,
    ensure_dirs, ffmpeg_exe, load_settings, update_settings,
)
from .fsutil import safe_rmtree, safe_unlink


def create_app() -> FastAPI:
    ensure_dirs()
    db.init_db()
    db.reset_jobs_on_startup()

    app = FastAPI(title=APP_NAME, version=VERSION, docs_url="/api/docs", redoc_url=None)

    # ------------------------------------------------------------------ #
    # info & settings
    # ------------------------------------------------------------------ #
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        ffmpeg_ok, ffmpeg_path = True, ""
        try:
            ffmpeg_path = ffmpeg_exe()
        except Exception:
            ffmpeg_ok = False
        try:
            import yt_dlp  # type: ignore

            ytdlp_version = getattr(yt_dlp.version, "__version__", "?")
        except Exception:
            ytdlp_version = ""
        try:
            import faster_whisper  # type: ignore  # noqa: F401

            whisper_ok = True
        except Exception:
            whisper_ok = False
        return {
            "app": APP_NAME,
            "version": VERSION,
            "ffmpeg": ffmpeg_ok,
            "ffmpeg_path": ffmpeg_path,
            "ytdlp": bool(ytdlp_version),
            "ytdlp_version": ytdlp_version,
            "whisper": whisper_ok,
            "youtube": youtube.is_connected(),
            "cpu": os.cpu_count() or 1,
            "busy": pipeline.busy_state(),
        }

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        settings = load_settings()
        settings.pop("yt_client_secret", None)
        return settings

    @app.patch("/api/settings")
    def patch_settings(patch: dict[str, Any] = Body(...)) -> dict[str, Any]:
        saved = update_settings(patch)
        saved.pop("yt_client_secret", None)
        return saved

    # ------------------------------------------------------------------ #
    # project / job
    # ------------------------------------------------------------------ #
    @app.get("/api/jobs")
    def api_jobs(limit: int = 50) -> list[dict[str, Any]]:
        jobs = db.list_jobs(limit=max(1, min(200, limit)))
        for job in jobs:
            clips = db.list_clips(job["id"])
            job["clip_count"] = len(clips)
            job["uploaded_count"] = sum(1 for c in clips if c["status"] == "uploaded")
            job["thumb"] = next((c["thumb"] for c in clips if c.get("thumb")), "")
            job["thumb_clip"] = next((c["id"] for c in clips if c.get("thumb")), "")
        return jobs

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str) -> dict[str, Any]:
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "Project tidak ditemukan")
        job["clips"] = db.list_clips(job_id)
        job["settings_snapshot"] = {
            k: load_settings().get(k) for k in ("min_clip_seconds", "max_clip_seconds", "target_clips")
        }
        return job

    @app.post("/api/jobs/url")
    def create_from_url(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        url = str(payload.get("url") or "").strip()
        if not ingest.is_url(url):
            raise HTTPException(400, "Link tidak valid (harus diawali http/https)")
        job = db.create_job("url", source=url, title=str(payload.get("title") or "").strip())
        pipeline.start_analysis(job["id"])
        return job

    @app.post("/api/jobs/local")
    def create_from_local(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        raw = str(payload.get("path") or "").strip().strip('"')
        path = Path(raw).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise HTTPException(400, f"File tidak ditemukan di server: {path}")
        job = db.create_job("local", source=str(path), source_path=str(path),
                            title=str(payload.get("title") or path.stem))
        pipeline.start_analysis(job["id"])
        return job

    @app.post("/api/jobs/upload")
    def create_from_upload(
        file: UploadFile = File(...),
        title: str = "",
    ) -> dict[str, Any]:
        ext = Path(file.filename or "").suffix.lower()
        if ext and ext not in ingest.ALLOWED_EXT:
            raise HTTPException(400, f"Format {ext} belum didukung")
        saved = ingest.save_upload(file.file, file.filename or "video.mp4")
        if saved.stat().st_size > MAX_UPLOAD_BYTES:
            safe_unlink(saved)
            raise HTTPException(413, "File terlalu besar")
        job = db.create_job("upload", source=file.filename or saved.name,
                            source_path=str(saved), title=title.strip() or saved.stem)
        pipeline.start_analysis(job["id"])
        return job

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str) -> dict[str, Any]:
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "Project tidak ditemukan")
        db.update_job(job_id, status="queued", stage="antre", progress=0.0, error="", message="")
        pipeline.start_analysis(job_id)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/render")
    def render_all(job_id: str) -> dict[str, Any]:
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "Project tidak ditemukan")
        pipeline.render_job_all(job_id)
        return {"ok": True, "clips": len(db.list_clips(job_id))}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict[str, Any]:
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "Project tidak ditemukan")
        for clip in db.list_clips(job_id):
            for key in ("file", "thumb"):
                if clip.get(key):
                    safe_unlink(clip[key])
            srt = (clip.get("meta") or {}).get("srt")
            if srt:
                safe_unlink(srt)
        source_path = job.get("source_path")
        db.delete_job(job_id)
        safe_rmtree(WORK_DIR / job_id)
        if source_path:
            p = Path(source_path)
            if p.is_dir():
                safe_rmtree(p)
            else:
                safe_unlink(p)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/transcript")
    def job_transcript(job_id: str) -> dict[str, Any]:
        data = transcribe.load_transcript(job_id)
        if not data:
            raise HTTPException(404, "Transkrip belum tersedia")
        return data

    # ------------------------------------------------------------------ #
    # clips
    # ------------------------------------------------------------------ #
    @app.get("/api/clips")
    def all_clips(job_id: str | None = None) -> list[dict[str, Any]]:
        clips = db.list_clips(job_id)
        for clip in clips:
            clip["has_file"] = bool(clip.get("file")) and Path(clip["file"]).exists()
        return clips

    @app.post("/api/jobs/{job_id}/clips")
    def add_manual_clip(job_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "Project tidak ditemukan")
        try:
            start = max(0.0, float(payload.get("start") or 0))
            duration = max(2.0, float(payload.get("duration") or 30))
        except (TypeError, ValueError):
            raise HTTPException(400, "start/duration tidak valid")
        title = str(payload.get("title") or "").strip() or f"Clip manual {start:.0f}s"
        clip = db.create_clip(
            job_id, idx=len(db.list_clips(job_id)), start=start, duration=duration,
            score=float(payload.get("score") or 0), title=title,
            hook=title[:48], hashtags=load_settings().get("yt_hashtags", "#Shorts"),
            description=title, status="suggested", meta={"manual": True},
        )
        return clip

    @app.get("/api/clips/{clip_id}")
    def get_clip(clip_id: str) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip tidak ditemukan")
        clip["has_file"] = bool(clip.get("file")) and Path(clip["file"]).exists()
        return clip

    @app.patch("/api/clips/{clip_id}")
    def patch_clip(clip_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip tidak ditemukan")
        patch: dict[str, Any] = {}
        for key in ("title", "hook", "description", "hashtags"):
            if key in payload:
                patch[key] = str(payload[key])[:5000]
        for key in ("start", "duration"):
            if key in payload:
                try:
                    patch[key] = round(max(0.0, float(payload[key])), 3)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{key} tidak valid")
        meta = dict(clip.get("meta") or {})
        if "focus" in payload:
            try:
                meta["focus"] = max(0.0, min(1.0, float(payload["focus"])))
                patch["meta"] = meta
            except (TypeError, ValueError):
                pass
        if "captions" in payload:
            meta["captions"] = bool(payload["captions"])
            patch["meta"] = meta
        # perubahan yang memengaruhi hasil video → tandai perlu render ulang
        rerender_keys = {"start", "duration", "hook", "focus", "captions"}
        if rerender_keys & set(patch) and clip.get("file"):
            meta = dict(patch.get("meta") or clip.get("meta") or {})
            if meta.get("dirty") is not True:
                meta["dirty"] = True
                patch["meta"] = meta
        if patch:
            db.update_clip(clip_id, **patch)
        return db.get_clip(clip_id)  # type: ignore[return-value]

    @app.delete("/api/clips/{clip_id}")
    def delete_clip(clip_id: str) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip tidak ditemukan")
        for key in ("file", "thumb"):
            if clip.get(key):
                safe_unlink(clip[key])
        srt = (clip.get("meta") or {}).get("srt")
        if srt:
            safe_unlink(srt)
        db.delete_clip(clip_id)
        return {"ok": True}

    @app.post("/api/clips/{clip_id}/render")
    def render_clip(clip_id: str) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip tidak ditemukan")
        if clip["status"] == "uploaded":
            raise HTTPException(400, "Clip ini sudah terupload")
        pipeline.start_render(clip_id)
        return {"ok": True}

    @app.post("/api/clips/{clip_id}/upload")
    def upload_clip(clip_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip tidak ditemukan")
        if not clip.get("file") or not Path(clip["file"]).exists():
            raise HTTPException(400, "Render clip dulu sebelum upload")
        if not youtube.is_connected():
            raise HTTPException(400, "Hubungkan akun YouTube dulu di tab YouTube")
        if payload.get("privacy"):
            update_settings({"yt_privacy": str(payload["privacy"])})
        pipeline.start_upload(clip_id)
        return {"ok": True}

    @app.post("/api/clips/upload-all")
    def upload_all(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        job_id = payload.get("job_id")
        if not youtube.is_connected():
            raise HTTPException(400, "Hubungkan akun YouTube dulu di tab YouTube")
        clips = db.list_clips(job_id)
        queued = 0
        for clip in clips:
            if clip.get("file") and Path(clip["file"]).exists() and clip["status"] != "uploaded":
                pipeline.start_upload(clip["id"])
                queued += 1
        return {"ok": True, "queued": queued}

    # ------------------------------------------------------------------ #
    # media streaming (support Range supaya bisa di-seek di browser)
    # ------------------------------------------------------------------ #
    def _stream(path: Path, request: Request, download_name: str | None = None) -> Response:
        if not path.exists():
            raise HTTPException(404, "File tidak ditemukan")
        size = path.stat().st_size
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        range_header = request.headers.get("range") or request.headers.get("Range")
        headers: dict[str, str] = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
        if download_name:
            headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
        start, end = 0, size - 1
        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if m:
                g1, g2 = m.groups()
                if g1:
                    start = int(g1)
                if g2:
                    end = min(int(g2), size - 1)
                elif g1:
                    end = size - 1
                if start > end or start >= size:
                    return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
                length = end - start + 1
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
                headers["Content-Length"] = str(length)

                def chunked():  # type: ignore[no-untyped-def]
                    with path.open("rb") as fh:
                        fh.seek(start)
                        remaining = length
                        while remaining > 0:
                            data = fh.read(min(1024 * 512, remaining))
                            if not data:
                                break
                            remaining -= len(data)
                            yield data

                return StreamingResponse(chunked(), status_code=206, media_type=media_type, headers=headers)
        headers["Content-Length"] = str(size)
        return FileResponse(path, media_type=media_type, headers=headers)

    @app.get("/api/clips/{clip_id}/video")
    def clip_video(clip_id: str, request: Request) -> Response:
        clip = db.get_clip(clip_id)
        if not clip or not clip.get("file"):
            raise HTTPException(404, "Video belum dirender")
        return _stream(Path(clip["file"]), request)

    @app.get("/api/clips/{clip_id}/download")
    def clip_download(clip_id: str) -> Response:
        clip = db.get_clip(clip_id)
        if not clip or not clip.get("file"):
            raise HTTPException(404, "Video belum dirender")
        safe = re.sub(r"[^\w\-. ]+", "_", clip.get("title") or "clip")
        return FileResponse(clip["file"], media_type="video/mp4",
                            filename=f"{safe[:60] or 'clip'}.mp4")

    @app.get("/api/clips/{clip_id}/thumb")
    def clip_thumb(clip_id: str) -> Response:
        clip = db.get_clip(clip_id)
        if not clip or not clip.get("thumb") or not Path(clip["thumb"]).exists():
            raise HTTPException(404, "Thumbnail belum ada")
        return FileResponse(clip["thumb"], media_type="image/jpeg")

    @app.get("/api/jobs/{job_id}/source")
    def job_source(job_id: str, request: Request) -> Response:
        job = db.get_job(job_id)
        if not job or not job.get("source_path"):
            raise HTTPException(404, "Sumber tidak ditemukan")
        return _stream(Path(job["source_path"]), request)

    # ------------------------------------------------------------------ #
    # video editor (timeline, teks, subtitle, template, export 4K)
    # ------------------------------------------------------------------ #
    @app.get("/api/editor/meta")
    def editor_meta() -> dict[str, Any]:
        payload = editor.templates_payload()
        payload["fonts"] = [
            "Inter", "Arial", "Helvetica", "DejaVu Sans", "Roboto", "Montserrat",
            "Poppins", "Impact", "Oswald", "Bebas Neue", "Verdana", "Georgia",
        ]
        return payload

    @app.get("/api/clips/{clip_id}/editor")
    def clip_editor_state(clip_id: str) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip tidak ditemukan")
        job = db.get_job(clip["job_id"])
        if not job:
            raise HTTPException(404, "Project tidak ditemukan")
        settings = load_settings()
        source_path = Path(job.get("source_path") or "")
        if not source_path.exists():
            raise HTTPException(400, "File sumber sudah tidak ada — jalankan ulang analisis project")
        info = media.probe(source_path)
        transcript = transcribe.load_transcript(job["id"])
        spec = editor.load_or_create_spec(clip, transcript, float(info.duration or 0), settings)
        return {
            "clip": {**clip, "has_file": bool(clip.get("file")) and Path(clip["file"]).exists()},
            "job": {"id": job["id"], "title": job.get("title"), "duration": job.get("duration")},
            "source": {
                "duration": info.duration, "width": info.width, "height": info.height,
                "has_audio": info.has_audio, "fps": info.fps,
                "has_video": info.has_video,
                "has_real_video": info.has_real_video,
                "attached_pic": info.attached_pic,
                "warning": editor.source_warning(info),
            },
            "spec": spec,
            "summary": editor.summarize(spec),
            "transcript_available": bool(transcript and transcript.get("segments")),
            "meta": editor.templates_payload(),
        }

    @app.put("/api/clips/{clip_id}/spec")
    def save_clip_spec(clip_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip tidak ditemukan")
        job = db.get_job(clip["job_id"])
        if not job:
            raise HTTPException(404, "Project tidak ditemukan")
        source_path = Path(job.get("source_path") or "")
        duration = float(job.get("duration") or 0)
        if source_path.exists():
            try:
                duration = media.probe(source_path).duration or duration
            except Exception:
                pass
        spec = editor.normalize_spec(payload.get("spec") if isinstance(payload, dict) else payload,
                                     clip, load_settings(), duration)
        first, last = spec["segments"][0], spec["segments"][-1]
        db.update_clip(
            clip_id,
            spec=json.dumps(spec, ensure_ascii=False),
            start=round(first["start"], 3),
            duration=round(editor.spec_duration(spec), 3),
            meta={**((db.get_clip(clip_id) or {}).get("meta") or {}), "dirty": True,
                  "edited": True, "last_end": round(last["end"], 3)},
        )
        return {"ok": True, "spec": spec, "summary": editor.summarize(spec)}

    @app.post("/api/clips/{clip_id}/spec/template")
    def apply_clip_template(clip_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip tidak ditemukan")
        template_id = str(payload.get("template") or "")
        if not editor.template_by_id(template_id):
            raise HTTPException(400, "Template tidak dikenal")
        job = db.get_job(clip["job_id"]) or {}
        source_path = Path(job.get("source_path") or "")
        duration = 0.0
        if source_path.exists():
            try:
                duration = media.probe(source_path).duration
            except Exception:
                duration = float(job.get("duration") or 0)
        spec = editor.load_or_create_spec(clip, transcribe.load_transcript(clip["job_id"]), duration)
        spec = editor.normalize_spec(editor.apply_template(spec, template_id), clip,
                                     load_settings(), duration)
        db.update_clip(clip_id, spec=json.dumps(spec, ensure_ascii=False),
                       meta={**(clip.get("meta") or {}), "dirty": True, "edited": True})
        return {"ok": True, "spec": spec, "summary": editor.summarize(spec)}

    @app.post("/api/clips/{clip_id}/subtitles")
    def clip_subtitles(clip_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip tidak ditemukan")
        job = db.get_job(clip["job_id"]) or {}
        source_path = Path(job.get("source_path") or "")
        duration = 0.0
        if source_path.exists():
            try:
                duration = media.probe(source_path).duration
            except Exception:
                duration = float(job.get("duration") or 0)
        transcript = transcribe.load_transcript(clip["job_id"])
        spec = editor.load_or_create_spec(clip, transcript, duration)
        action = str(payload.get("action") or "regenerate")

        if action == "regenerate":
            if not transcript or not transcript.get("segments"):
                raise HTTPException(
                    400,
                    "Transkrip belum tersedia untuk project ini (Whisper belum jalan / video tanpa audio).",
                )
            spec["subtitles"] = editor.generate_subtitles(
                transcript, spec["segments"],
                int(payload.get("words_per_line") or spec["captions"].get("words_per_line", 3)),
                bool(payload.get("word_level", spec["captions"].get("word_level", True))),
            )
            spec["captions"]["enabled"] = True
        elif action == "shift":
            shift = max(-30.0, min(30.0, float(payload.get("seconds") or 0)))
            for line in spec.get("subtitles") or []:
                line["start"] = round(max(0.0, float(line.get("start", 0)) + shift), 3)
                line["end"] = round(max(0.2, float(line.get("end", 0)) + shift), 3)
                for word in line.get("words") or []:
                    word["start"] = round(max(0.0, float(word.get("start", 0)) + shift), 3)
                    word["end"] = round(max(0.2, float(word.get("end", 0)) + shift), 3)
            spec["subtitles"] = sorted(spec.get("subtitles") or [], key=lambda s: s["start"])
        elif action == "clear":
            spec["subtitles"] = []
        elif action == "set_words_per_line":
            spec["captions"]["words_per_line"] = int(payload.get("words_per_line") or 3)
        else:
            raise HTTPException(400, f"Aksi subtitle '{action}' tidak dikenal")

        spec = editor.normalize_spec(spec, clip, load_settings(), duration)
        db.update_clip(clip_id, spec=json.dumps(spec, ensure_ascii=False),
                       meta={**(clip.get("meta") or {}), "dirty": True, "edited": True})
        return {"ok": True, "spec": spec, "summary": editor.summarize(spec)}

    @app.post("/api/clips/{clip_id}/assets")
    def upload_clip_asset(clip_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip tidak ditemukan")
        ext = Path(file.filename or "logo.png").suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            raise HTTPException(400, "Logo harus PNG/JPG/WebP")
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        target = ASSET_DIR / f"{clip_id}_{int(time.time())}{ext}"
        with target.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        if target.stat().st_size > 8 * 1024 * 1024:
            safe_unlink(target)
            raise HTTPException(413, "Logo terlalu besar (maks 8 MB)")
        # PNG transparan paling aman untuk overlay
        return {"ok": True, "path": str(target), "url": f"/api/clips/{clip_id}/assets/{target.name}"}

    @app.get("/api/clips/{clip_id}/assets/{name}")
    def get_clip_asset(clip_id: str, name: str) -> Response:
        safe = Path(name).name
        path = ASSET_DIR / safe
        if not path.exists() or not safe.startswith(clip_id):
            raise HTTPException(404, "Aset tidak ditemukan")
        return FileResponse(path)

    # ------------------------------------------------------------------ #
    # youtube
    # ------------------------------------------------------------------ #
    @app.get("/api/youtube/status")
    def yt_status() -> dict[str, Any]:
        status = youtube.device_status()
        client_id, secret = youtube.client_credentials()
        status["client_configured"] = bool(client_id)
        status["client_has_secret"] = bool(secret)
        return status

    @app.get("/api/youtube/channel")
    def yt_channel() -> dict[str, Any]:
        if not youtube.is_connected():
            raise HTTPException(400, "Belum terhubung")
        try:
            return youtube.channel_info()
        except Exception as exc:
            raise HTTPException(502, f"Gagal ambil channel: {exc}")

    @app.post("/api/youtube/client")
    def yt_client(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        raw = payload.get("client") or ""
        if not raw and payload.get("client_id"):
            raw = f"{payload.get('client_id')}:{payload.get('client_secret') or ''}"
        try:
            return youtube.import_client_secret(str(raw))
        except youtube.YouTubeError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/youtube/login/device")
    def yt_login_device() -> Any:
        try:
            return youtube.start_device_auth()
        except youtube.YouTubeError as exc:
            return JSONResponse(
                {"detail": str(exc), "help": youtube.help_for(str(exc))}, status_code=400
            )

    @app.post("/api/youtube/login/browser")
    def yt_login_browser() -> Any:
        try:
            return youtube.local_browser_auth()
        except youtube.YouTubeError as exc:
            return JSONResponse(
                {"detail": str(exc), "help": youtube.help_for(str(exc))}, status_code=400
            )
        except Exception as exc:
            return JSONResponse(
                {"detail": f"Login browser gagal: {exc}"}, status_code=500
            )

    @app.post("/api/youtube/disconnect")
    def yt_disconnect() -> dict[str, Any]:
        youtube.disconnect()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # video viral (buat bahan clip / re-upload)
    # ------------------------------------------------------------------ #
    @app.get("/api/discover/config")
    def discover_config() -> dict[str, Any]:
        settings = load_settings()
        return {
            "regions": [{"code": c, "label": label} for c, label in discover.REGIONS],
            "categories": [{"id": i, "label": label} for i, label in discover.CATEGORIES],
            "presets": discover.suggestion_queries(settings.get("discover_region") or "ID"),
            "backends": discover.backends(),
            "defaults": {
                "region": settings.get("discover_region"),
                "days": settings.get("discover_days"),
                "min_views": settings.get("discover_min_views"),
                "min_duration": settings.get("discover_min_duration"),
                "max_duration": settings.get("discover_max_duration"),
                "limit": settings.get("discover_limit"),
                "queries": settings.get("discover_queries"),
                "use_api": settings.get("discover_use_api"),
            },
        }

    @app.get("/api/discover")
    def discover_list(
        sort: str = "score",
        q: str = "",
        platform: str = "",
        min_views: int = 0,
        min_duration: float = 0,
        max_duration: float = 0,
        only_unused: bool = False,
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        return db.list_discoveries(
            limit=limit, sort=sort, query=q, platform=platform,
            min_views=min_views, min_duration=min_duration,
            max_duration=max_duration, only_unused=only_unused,
        )

    @app.post("/api/discover/search")
    def discover_search(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        params = discover.SearchParams.from_payload(payload or {})
        try:
            return discover.search(params)
        except discover.DiscoverError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"Pencarian gagal: {exc}")

    @app.post("/api/discover/trending")
    def discover_trending(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        payload = payload or {}
        settings = load_settings()
        try:
            return discover.trending_now(
                region=str(payload.get("region") or settings.get("discover_region") or "ID"),
                limit=int(payload.get("limit") or settings.get("discover_limit") or 24),
                min_views=int(payload.get("min_views") or 0),
                category=str(payload.get("category") or ""),
                days=int(payload.get("days") or settings.get("discover_days") or 30),
                use_api=bool(payload.get("use_api", True)),
                enrich=bool(payload.get("enrich", False)),
            )
        except discover.DiscoverError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"Gagal mengambil trending: {exc}")

    def _job_from_discovery(item: dict[str, Any]) -> dict[str, Any]:
        url = item.get("url") or ""
        if not url and item.get("ext_id") and (item.get("platform") or "youtube") == "youtube":
            url = f"https://www.youtube.com/watch?v={item['ext_id']}"
        if not url:
            raise HTTPException(400, "URL video tidak tersedia")
        job = db.create_job(
            "url",
            source=url,
            title=item.get("title") or "",
            meta={
                "discovery_id": item["id"],
                "channel": item.get("channel", ""),
                "views": item.get("views", 0),
                "viral_score": item.get("score", 0),
                "original_url": url,
            },
        )
        db.mark_discovery_used(item["id"], job["id"])
        pipeline.start_analysis(job["id"])
        return job

    @app.post("/api/discover/{discovery_id}/clip")
    def discover_make_clip(discovery_id: str) -> dict[str, Any]:
        item = db.get_discovery(discovery_id)
        if not item:
            raise HTTPException(404, "Video tidak ditemukan di daftar viral")
        job = _job_from_discovery(item)
        return {"ok": True, "job": job}

    @app.post("/api/discover/clip-batch")
    def discover_make_clips(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        ids = payload.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise HTTPException(400, "Pilih minimal satu video")
        created: list[str] = []
        for discovery_id in ids[:5]:  # batasi supaya CPU tidak penuh
            item = db.get_discovery(str(discovery_id))
            if not item:
                continue
            try:
                job = _job_from_discovery(item)
            except HTTPException:
                continue  # lewati video yang datanya tidak lengkap
            created.append(job["id"])
        if not created:
            raise HTTPException(400, "Tidak ada video valid yang bisa diproses")
        return {"ok": True, "jobs": created, "count": len(created)}

    @app.delete("/api/discover")
    def discover_clear() -> dict[str, Any]:
        db.clear_discoveries()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # util
    # ------------------------------------------------------------------ #
    @app.get("/api/llm/status")
    def llm_status() -> dict[str, Any]:
        settings = load_settings()
        return {"enabled": bool(settings.get("llm_enabled")),
                "available": analyze.ollama_available(settings)}

    @app.get("/api/analyze/models")
    def whisper_models() -> dict[str, Any]:
        return {"available": transcribe.available(),
                "models": ["tiny", "base", "small", "medium", "large-v3"]}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        return JSONResponse(status_code=500, content={"detail": str(exc)[:500]})

    # static UI
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app
