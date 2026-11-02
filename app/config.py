"""Konfigurasi global Clipper: path, setting user, dan pencarian ffmpeg."""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

APP_NAME = "Clipper"
VERSION = "1.0.11"

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

DATA_DIR = Path(os.environ.get("CLIPPER_DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
CLIP_DIR = DATA_DIR / "clips"
WORK_DIR = DATA_DIR / "work"
ASSET_DIR = DATA_DIR / "assets"
MODEL_DIR = Path(os.environ.get("CLIPPER_MODEL_DIR", ROOT / "models")).resolve()
SETTINGS_FILE = DATA_DIR / "settings.json"
TOKEN_FILE = DATA_DIR / "youtube_token.json"
COOKIE_FILE = DATA_DIR / "cookies.txt"
CLIENT_SECRET_FILE = DATA_DIR / "youtube_client_secret.json"
DB_FILE = DATA_DIR / "clipper.db"

HOST = os.environ.get("CLIPPER_HOST", "0.0.0.0")
PORT = int(os.environ.get("CLIPPER_PORT", "8420"))

MAX_UPLOAD_BYTES = int(os.environ.get("CLIPPER_MAX_UPLOAD_MB", "4096")) * 1024 * 1024


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOAD_DIR, CLIP_DIR, WORK_DIR, ASSET_DIR, MODEL_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# ffmpeg discovery
# --------------------------------------------------------------------------- #
_ffmpeg_path: str | None = None
_ffmpeg_lock = threading.Lock()


def ffmpeg_exe() -> str:
    """Cari binary ffmpeg: env CLIPPER_FFMPEG -> PATH -> paket imageio-ffmpeg."""
    global _ffmpeg_path
    with _ffmpeg_lock:
        if _ffmpeg_path:
            return _ffmpeg_path
        candidate = os.environ.get("CLIPPER_FFMPEG")
        if candidate and Path(candidate).exists():
            _ffmpeg_path = candidate
            return _ffmpeg_path
        found = shutil.which("ffmpeg")
        if found:
            _ffmpeg_path = found
            return _ffmpeg_path
        try:
            import imageio_ffmpeg  # type: ignore

            _ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
            return _ffmpeg_path
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "ffmpeg tidak ditemukan. Install ffmpeg atau jalankan: pip install imageio-ffmpeg"
            ) from exc


def ffmpeg_for_ytdlp() -> str | None:
    """Lokasi ffmpeg untuk opsi `ffmpeg_location` milik yt-dlp.

    Tanpa ini yt-dlp hanya mencari `ffmpeg` di PATH. Di Windows ffmpeg jarang ada
    di PATH (Clipper memakai binary dari paket imageio-ffmpeg), sehingga unduhan
    video+audio gagal dengan "You have requested merging of multiple formats but
    ffmpeg is not installed".

    yt-dlp menerima path file maupun folder. Kalau nama binary-nya persis
    `ffmpeg`/`ffmpeg.exe`, kita kirim foldernya supaya yt-dlp sekaligus menemukan
    `ffprobe` di sebelahnya; kalau namanya versi (mis. ffmpeg-win-x86_64-v7.0.2.exe)
    kita kirim path file-nya.
    """
    try:
        exe = Path(ffmpeg_exe())
    except Exception:  # pragma: no cover - ffmpeg benar-benar tidak ada
        return None
    if not exe.exists():
        return None
    if exe.stem.lower() == "ffmpeg":
        return str(exe.parent)
    return str(exe)


def ffprobe_exe() -> str | None:
    """ffprobe opsional; kalau tidak ada, probing dilakukan lewat ffmpeg."""
    candidate = os.environ.get("CLIPPER_FFPROBE")
    if candidate and Path(candidate).exists():
        return candidate
    return shutil.which("ffprobe")


# --------------------------------------------------------------------------- #
# setting user (persisted json)
# --------------------------------------------------------------------------- #
DEFAULT_SETTINGS: dict[str, Any] = {
    # analisis
    "min_clip_seconds": 20,
    "max_clip_seconds": 60,
    "target_clips": 5,
    "language": "auto",          # auto | id | en | ...
    "whisper_model": "small",    # tiny | base | small | medium | large-v3
    "whisper_device": "auto",    # auto | cpu | cuda
    "whisper_compute": "auto",   # auto | int8 | float16 | int8_float16
    "prefer_whisper": False,     # True = selalu transkripsi sendiri walau ada subtitle
    # AI lokal opsional (Ollama) untuk milih hook + bikin judul
    "llm_enabled": False,
    "llm_url": "http://127.0.0.1:11434",
    "llm_model": "llama3.1:8b",
    # render
    "layout": "fit",             # fit (blur bg) | crop (isi penuh)
    "crop_focus": 0.5,           # 0 = kiri, 0.5 = tengah, 1 = kanan (mode crop)
    "captions": True,
    "caption_words": 3,          # jumlah kata per baris caption
    "caption_uppercase": True,
    "caption_font": "Inter",
    "caption_color": "#FFFFFF",
    "caption_highlight": "#FFE02B",
    "hook_overlay": True,        # tampilkan judul hook di atas video
    "normalize_audio": True,
    "fps": 30,
    "crf": 20,
    "export_height": 1920,       # 1920 = 1080p, 2560 = 2K, 3840 = 4K
    "export_sharpen": True,      # pertajam saat upscale
    "default_template": "",      # template editor default (kosong = ikut gaya global)
    # youtube
    "yt_auto_upload": False,
    "yt_privacy": "public",      # public | unlisted | private
    "yt_category": "24",         # 24 = Entertainment
    "yt_hashtags": "#Shorts #ShortsIndonesia #fyp",
    "yt_notify_subscribers": True,
    # otomatisasi
    "auto_render": False,        # render semua clip setelah analisis
    "auto_pipeline": True,       # analisis -> render -> upload (kalau login) tanpa klik
    # yt-dlp
    "ytdlp_cookies_browser": "",  # chrome | firefox | edge | brave | safari | ""
    "ytdlp_cookies_file": "",     # path cookies.txt (Netscape). kosong = pakai data/cookies.txt
    "ytdlp_proxy": "",
    # client YouTube yang dipakai saat video dibatasi umur ("Sign in to confirm your age").
    # "auto" = coba client default dulu, lalu tv/web_safari kalau diblokir.
    "ytdlp_player_client": "auto",  # auto | default | tv
    # kualitas unduhan: video1080 (default) | video720 | best (tanpa batas resolusi)
    # "video1080" sengaja membatasi 1080p supaya hemat kuota & render lebih cepat.
    "ytdlp_format": "video1080",
    # kredensial youtube (diisi dari tab YouTube)
    "yt_client_id": "",
    "yt_client_secret": "",
    # pencarian video viral
    "discover_region": "ID",
    "discover_days": 30,
    "discover_min_views": 100000,
    "discover_min_duration": 240,   # 0 = semua durasi; 240 dtk = fokus video panjang
    "discover_max_duration": 0,     # 0 = tanpa batas
    "discover_limit": 24,
    "discover_queries": "podcast indonesia, motivasi hidup, ceramah agama, bisnis online",
    "discover_use_api": True,       # pakai YouTube Data API kalau tersedia (akurat, pakai kuota)
    "discover_api_key": "",         # opsional: API key gratis, tanpa perlu login
}

_settings_lock = threading.RLock()  # RLock: update_settings() memanggil load_settings()
_settings_cache: dict[str, Any] | None = None


def load_settings() -> dict[str, Any]:
    global _settings_cache
    with _settings_lock:
        if _settings_cache is not None:
            return dict(_settings_cache)
        data = dict(DEFAULT_SETTINGS)
        if SETTINGS_FILE.exists():
            try:
                stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    for key, value in stored.items():
                        if key in DEFAULT_SETTINGS:
                            data[key] = value
            except Exception:
                pass
        _settings_cache = data
        return dict(data)


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    global _settings_cache
    with _settings_lock:
        current = dict(_settings_cache) if _settings_cache else load_settings()
        for key, value in (patch or {}).items():
            if key not in DEFAULT_SETTINGS:
                continue
            current[key] = _coerce(DEFAULT_SETTINGS[key], value)
        ensure_dirs()
        SETTINGS_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        _settings_cache = current
        return dict(current)


def _coerce(default: Any, value: Any) -> Any:
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        return str(value)
    except (TypeError, ValueError):
        return default
