"""Ambil sumber video: upload file dari browser atau download dari link (yt-dlp)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import BinaryIO, Callable

from .config import UPLOAD_DIR, WORK_DIR, ensure_dirs, load_settings

ALLOWED_EXT = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".ts", ".mpg", ".mpeg",
    ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus",
}


def safe_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^\w\-. ()\[\]]+", "_", name).strip() or "video"
    return name[:120]


def save_upload(stream: BinaryIO, filename: str, on_progress: Callable[[int], None] | None = None) -> Path:
    """Simpan file upload secara streaming (aman untuk file besar)."""
    ensure_dirs()
    stem = Path(safe_name(filename)).stem
    ext = Path(filename).suffix.lower() or ".mp4"
    if ext not in ALLOWED_EXT:
        ext = ".mp4"
    target = UPLOAD_DIR / f"{stem}-{abs(hash(filename)) % 10**6}{ext}"
    size = 0
    with target.open("wb") as out:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            size += len(chunk)
            if on_progress:
                on_progress(size)
    if size == 0:
        target.unlink(missing_ok=True)
        raise ValueError("File kosong / gagal di-upload")
    return target


def is_url(value: str) -> bool:
    return bool(re.match(r"^https?://", (value or "").strip(), re.I))


class DownloadError(RuntimeError):
    pass


def download(url: str, job_label: str = "download", on_progress: Callable[[float, str], None] | None = None) -> Path:
    """Download video dari link pakai yt-dlp (YouTube, TikTok, IG, X, FB, dll)."""
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:
        raise DownloadError(
            "yt-dlp belum terinstall. Jalankan: pip install yt-dlp"
        ) from exc

    ensure_dirs()
    out_dir = WORK_DIR / safe_name(job_label)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = load_settings()

    def hook(status: dict) -> None:
        if not on_progress:
            return
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
            done = status.get("downloaded_bytes") or 0
            pct = (done / total) if total else 0.0
            speed = status.get("speed") or 0
            speed_mb = f"{speed / 1048576:.1f} MB/s" if speed else ""
            on_progress(min(0.99, pct), f"Mengunduh {pct * 100:.0f}% {speed_mb}".strip())
        elif status.get("status") == "finished":
            on_progress(1.0, "Download selesai, memproses file…")

    ydl_opts: dict = {
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "format": (
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "restrictfilenames": True,
        "concurrent_fragment_downloads": 4,
        # subtitle video (kalau ada) dipakai sebagai fallback transkrip
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["id", "en"],
        "subtitlesformat": "vtt/srt/best",
        "ignoreerrors": True,
    }
    cookies = (settings.get("ytdlp_cookies_browser") or "").strip()
    if cookies:
        ydl_opts["cookiesfrombrowser"] = (cookies,)
    proxy = (settings.get("ytdlp_proxy") or "").strip()
    if proxy:
        ydl_opts["proxy"] = proxy

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info and info.get("entries"):
                info = info["entries"][0]
            if not info:
                raise DownloadError(
                    "Video tidak bisa diunduh. Cek linknya (video private/dihapus?), atau server "
                    "ini tidak punya akses internet ke YouTube/TikTok."
                )
            path = Path(ydl.prepare_filename(info))
            if not path.exists():
                candidates = sorted(out_dir.glob("source.*"))
                if not candidates:
                    raise DownloadError("File hasil download tidak ditemukan")
                path = candidates[0]
            return path
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"Gagal mengunduh link: {exc}") from exc


def cleanup_dir(path: str | Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
