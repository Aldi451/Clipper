"""Ambil sumber video: upload file dari browser atau download dari link (yt-dlp)."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import BinaryIO, Callable

from .config import UPLOAD_DIR, WORK_DIR, ensure_dirs, ffmpeg_for_ytdlp, load_settings
from .fsutil import safe_rmtree

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


FORMAT_CHOICES = {
    # Rantai fallback, dari kualitas terbaik ke paling luas:
    #   1. video-only + audio-only (kualitas terbaik, tetap mp4/m4a kalau ada)
    #   2. video-only + audio-only tanpa batas ekstensi
    #   3. format gabungan (video+audio dalam satu stream) → untuk video lama/Shorts
    #   4. apa pun yang tersedia
    # Catatan: sengaja pakai `bv`/`ba` (bukan `bv*`) supaya yt-dlp tidak memilih
    # format gabungan beresolusi rendah ketika stream terpisah tersedia.
    "video1080": (
        "bv[height<=1080][ext=mp4]+ba[ext=m4a]/"
        "bv[height<=1080]+ba/"
        "b[height<=1080]/"
        "b"
    ),
    "video720": (
        "bv[height<=720][ext=mp4]+ba[ext=m4a]/"
        "bv[height<=720]+ba/"
        "b[height<=720]/"
        "b"
    ),
    "best": "bv+ba/b",
}

# dipakai saat format dari setting tetap ditolak yt-dlp
FORMAT_FALLBACKS = ["bv+ba/b", "b"]


def _format_selector(choice: str | None) -> str:
    key = (choice or "video1080").strip()
    return FORMAT_CHOICES.get(key, FORMAT_CHOICES["video1080"])


# --------------------------------------------------------------------------- #
# cookies (video terbatas usia / perlu login)
# --------------------------------------------------------------------------- #
COOKIE_TEMPLATE_HINT = (
    "Pengaturan → 'Cookies browser/berkas'. Cara termudah: login YouTube di browser, "
    "pasang ekstensi 'Get cookies.txt LOCALLY', ekspor youtube.com sebagai cookies.txt, "
    "lalu unggah/tempel di tab Pengaturan Clipper."
)


HTTPONLY_PREFIX = "#HttpOnly_"


def clean_cookie_text(raw: str) -> str:
    """Buang BOM dan samakan akhir baris (ekspor browser kerap CRLF + BOM)."""
    text = (raw or "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip("\n")


def _cookie_row(line: str) -> list[str] | None:
    """Baris cookie Netscape → 7 kolom, atau None kalau bukan baris cookie.

    Penting: baris `#HttpOnly_.youtube.com` BUKAN komentar — dia berisi cookie
    login (SID/HSID/SSID) yang justru dibutuhkan yt-dlp.
    """
    line = line.strip("\n").rstrip("\r")
    if not line.strip():
        return None
    if line.startswith(HTTPONLY_PREFIX):
        line = line[len(HTTPONLY_PREFIX):]
    elif line.startswith("#"):
        return None
    parts = line.split("\t")
    if len(parts) < 7:
        return None
    return parts


def cookie_path(settings: dict | None = None) -> Path | None:
    """Lokasi cookies.txt yang dipakai (setting manual atau data/cookies.txt)."""
    from .config import COOKIE_FILE

    settings = settings if settings is not None else load_settings()
    raw = (settings.get("ytdlp_cookies_file") or "").strip()
    path = Path(raw).expanduser() if raw else COOKIE_FILE
    return path if path.is_file() else None


def cookie_summary(settings: dict | None = None) -> dict:
    """Info singkat cookies untuk UI (tanpa membocorkan isinya)."""
    from .config import COOKIE_FILE

    settings = settings if settings is not None else load_settings()
    path = cookie_path(settings)
    target = path or COOKIE_FILE
    if not path:
        return {"configured": False, "path": str(target), "domains": [], "count": 0, "youtube": False}
    domains: list[str] = []
    count = 0
    names: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            row = _cookie_row(line)
            if row is None:
                continue
            count += 1
            domain = row[0].lstrip(".").lower()
            if domain and domain not in domains:
                domains.append(domain)
            if row[5] and row[5] not in names:
                names.append(row[5])
    except OSError:
        pass
    return {
        "configured": True,
        "path": str(path),
        "count": count,
        "domains": domains[:8],
        "youtube": any("youtube" in d or "google" in d for d in domains),
        "auth_cookies": any(n in ("SID", "HSID", "SSID", "SAPISID", "APISID", "__Secure-1PSID")
                            for n in names),
        "browser": (settings.get("ytdlp_cookies_browser") or ""),
    }


def _json_cookies_to_netscape(raw: list) -> str:
    """Konversi ekspor JSON (Cookie-Editor / EditThisCookie) ke format Netscape."""
    lines = ["# Netscape HTTP Cookie File", "# Dibuat oleh Clipper", ""]
    for item in raw:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "").strip()
        name = str(item.get("name") or "").strip()
        if not domain or not name:
            continue
        value = str(item.get("value") or "")
        path = str(item.get("path") or "/")
        secure = "TRUE" if item.get("secure") else "FALSE"
        expires = int(item.get("expirationDate") or item.get("expires") or 0)
        if expires <= 0:
            expires = 2147483647  # sesi → treat as jauh di masa depan
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        http_only = "#HttpOnly_" if item.get("httpOnly") else ""
        lines.append(
            f"{http_only}{domain}\t{include_sub}\t{path}\t{secure}\t{expires}\t{name}\t{value}"
        )
    if len(lines) <= 3:
        raise ValueError("Tidak ada cookie yang bisa dibaca dari JSON tersebut")
    return "\n".join(lines) + "\n"


def parse_cookies(raw: str) -> str:
    """Terima cookies.txt (Netscape) atau JSON ekspor ekstensi → kembalikan teks Netscape."""
    text = clean_cookie_text(raw)
    if not text:
        raise ValueError("Isi cookies masih kosong")
    if text.startswith("[") or text.startswith("{"):
        import json as _json

        try:
            data = _json.loads(text)
        except ValueError as exc:
            raise ValueError("JSON cookies tidak valid") from exc
        if isinstance(data, dict):
            data = data.get("cookies") or []
        return _json_cookies_to_netscape(data)
    rows = [line for line in text.splitlines() if _cookie_row(line) is not None]
    if not rows:
        if "\t" not in text:
            raise ValueError(
                "Format cookies tidak dikenali. Pakai cookies.txt (Netscape) atau JSON dari "
                "ekstensi (Cookie-Editor → Export → JSON)."
            )
        raise ValueError(
            "File ini tidak berisi baris cookie yang valid (tiap baris butuh 7 kolom dipisah TAB). "
            "Pastikan mengekspor dari halaman youtube.com, bukan file lain."
        )
    header = ["# Netscape HTTP Cookie File", "# Dibuat oleh Clipper", ""]
    return "\n".join(header + rows) + "\n"


def save_cookies(raw: str) -> dict:
    """Simpan cookies untuk dipakai yt-dlp (menangani video terbatas usia)."""
    from .config import COOKIE_FILE
    from .fsutil import safe_unlink
    from .config import update_settings

    text = parse_cookies(raw)
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(text, encoding="utf-8")
    try:
        COOKIE_FILE.chmod(0o600)
    except OSError:
        pass
    # setting manual dikosongkan supaya memakai data/cookies.txt yang baru
    update_settings({"ytdlp_cookies_file": ""})
    summary = cookie_summary()
    if not summary["count"]:
        safe_unlink(COOKIE_FILE)
        raise ValueError("Cookies tidak berisi baris cookie yang valid")
    return summary


def clear_cookies() -> dict:
    from .config import COOKIE_FILE
    from .fsutil import safe_unlink

    safe_unlink(COOKIE_FILE)
    return cookie_summary()


# --------------------------------------------------------------------------- #
# pesan error download
# --------------------------------------------------------------------------- #
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Buang kode warna ANSI, mis. '\x1b[0;31mERROR:\x1b[0m ...' dari yt-dlp."""
    return ANSI_RE.sub("", text or "").strip()


AGE_HINTS = (
    "sign in to confirm your age",
    "confirm your age",
    "age-restricted",
    "age restricted",
    "inappropriate for some users",
    "only available for users",
    "inappropriate",
)
BOT_HINTS = ("confirm you're not a bot", "confirm you’re not a bot", "not a bot", "unusual traffic", "captcha")
LOGIN_HINTS = ("login required", "sign in to confirm", "please log in", "this video is private", "private video",
               "members-only", "join this channel")
GEO_HINTS = ("available in your country", "country", "geo restricted", "geo-restricted", "geo-blocked")
UNAVAILABLE_HINTS = ("video unavailable", "has been removed", "no longer available", "does not exist",
                     "removed by the uploader", "account associated with this video has been terminated")
NETWORK_HINTS = ("timed out", "connection", "getaddrinfo", "ssl", "temporary failure", "network",
                 "unable to download webpage", "http error 5", "proxy")
RELOAD_HINTS = ("the page needs to be reloaded", "page needs to be reloaded",
                "needs to be reloaded", "page needs reloading")
FFMPEG_HINTS = ("ffmpeg is not installed", "ffmpeg not installed", "requested merging of multiple formats",
                "merging of multiple formats", "ffmpeg is required", "ffmpeg-location does not exist")
FORMAT_HINTS = ("requested format is not available", "requested format not available",
                "no video formats found", "no formats found", "requested downloading of",
                "format is not available", "unable to extract")


def classify_download_error(message: str) -> str:
    low = strip_ansi(message).lower()
    if any(k in low for k in RELOAD_HINTS):
        return "reload"
    if any(k in low for k in FFMPEG_HINTS):
        return "ffmpeg"
    if any(k in low for k in FORMAT_HINTS):
        return "format"
    if any(k in low for k in BOT_HINTS):
        return "bot"
    if any(k in low for k in AGE_HINTS):
        return "age"
    if any(k in low for k in GEO_HINTS):
        return "geo"
    if any(k in low for k in LOGIN_HINTS):
        return "login"
    if any(k in low for k in UNAVAILABLE_HINTS):
        return "unavailable"
    if any(k in low for k in NETWORK_HINTS):
        return "network"
    return "other"


def friendly_download_error(message: str, has_cookies: bool = False) -> str:
    """Ubah error yt-dlp jadi penjelasan + langkah perbaikan."""
    raw = strip_ansi(message)
    raw = re.sub(r"^ERROR:\s*", "", raw, flags=re.I)
    kind = classify_download_error(raw)
    detail = raw.splitlines()[0][:220] if raw else "tanpa detail"

    if kind in ("age", "bot", "login"):
        head = {
            "age": "Video ini dibatasi umur (YouTube minta login untuk memastikan usia).",
            "bot": "YouTube menganggap permintaan ini bot (biasanya butuh login/cookies).",
            "login": "Video ini butuh login (private / members-only / dibatasi).",
        }[kind]
        if has_cookies:
            return (
                f"{head} Cookies sudah terpasang tapi ditolak — kemungkinan sudah kedaluwarsa "
                "atau akunnya belum lolos verifikasi usia. Ekspor ulang cookies.txt dari browser "
                "yang sudah login ke akun berusia 18+, unggah lagi di Pengaturan, lalu coba lagi. "
                f"· detail: {detail}"
            )
        return (
            f"{head} Clipper perlu cookies dari browser yang sudah login. {COOKIE_TEMPLATE_HINT} "
            f"Alternatif: unduh videonya manual, lalu pakai 'Dari file' (drag & drop). · detail: {detail}"
        )
    if kind == "geo":
        return (
            "Video dibatasi untuk negara tertentu. Pakai proxy/VPN: isi kolom Proxy di Pengaturan "
            f"(mis. http://127.0.0.1:8080), lalu coba lagi. · detail: {detail}"
        )
    if kind == "unavailable":
        return f"Video tidak tersedia (dihapus/private/diblokir pemiliknya). Coba link lain. · detail: {detail}"
    if kind == "format":
        return (
            "Video ini tidak menyediakan format yang diminta (sering pada video lama atau "
            "Shorts yang hanya punya satu format gabungan). Clipper sudah mencoba beberapa "
            "alternatif otomatis. Kalau masih gagal: Pengaturan → Format unduhan → 'Best', "
            "lalu coba lagi. · detail: " + detail
        )
    if kind == "reload":
        return (
            "YouTube mengirim halaman/player yang sudah kedaluwarsa, jadi unduhan berhenti di "
            "tengah jalan. Ini biasanya sementara dan Clipper sudah mengulanginya otomatis. "
            "Kalau terus terjadi: 1) perbarui yt-dlp (pip install -U yt-dlp), 2) pasang cookies "
            "(Pengaturan → Cookies), 3) coba Client YouTube → TV. · detail: " + detail
        )
    if kind == "ffmpeg":
        return (
            "yt-dlp tidak menemukan ffmpeg, padahal ffmpeg dibutuhkan untuk menggabungkan "
            "video+audio. Clipper sudah mencoba ulang dengan format tunggal (tanpa gabung). "
            "Kalau masih gagal: jalankan `pip install -U imageio-ffmpeg` (Clipper memakai ffmpeg "
            "dari paket itu) atau install ffmpeg dan pastikan ada di PATH, lalu coba lagi. "
            "· detail: " + detail
        )
    if kind == "network":
        return (
            "Tidak bisa menghubungi server video (jaringan/DNS/proxy/SSL). Cek koneksi internet, "
            f"atau isi Proxy di Pengaturan kalau memakai VPN. · detail: {detail}"
        )
    return f"Gagal mengunduh: {detail}"


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #
TV_PLAYER_ARGS = {"youtube": {"player_client": ["tv", "web_safari"]}}


def _build_opts(out_dir: Path, settings: dict, hook: Callable[[dict], None], extra: dict | None = None) -> dict:
    ffmpeg = ffmpeg_for_ytdlp()
    opts: dict = {
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "format": _format_selector(settings.get("ytdlp_format")),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "restrictfilenames": True,
        "concurrent_fragment_downloads": 4,
        # biarkan error asli keluar supaya bisa diterjemahkan jadi pesan jelas
        "ignoreerrors": False,
        # subtitle video (kalau ada) dipakai sebagai fallback transkrip
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["id", "en"],
        "subtitlesformat": "vtt/srt/best",
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
    }
    if ffmpeg:
        # ffmpeg bawaan Clipper (imageio-ffmpeg) belum tentu ada di PATH —
        # tanpa opsi ini yt-dlp gagal merge video+audio di Windows.
        opts["ffmpeg_location"] = ffmpeg
    else:
        # ffmpeg tidak ada sama sekali: hindari format gabungan yang pasti gagal
        opts["format"] = "b[ext=mp4]/b"
    cookies = (settings.get("ytdlp_cookies_browser") or "").strip()
    if cookies:
        opts["cookiesfrombrowser"] = (cookies,)
    cookie_file = cookie_path(settings)
    if cookie_file is not None:
        opts["cookiefile"] = str(cookie_file)
    proxy = (settings.get("ytdlp_proxy") or "").strip()
    if proxy:
        opts["proxy"] = proxy
    if extra:
        opts.update(extra)
    return opts


def _clean_partials(out_dir: Path) -> None:
    """Buang file parsial/fragmen sisa percobaan sebelumnya.

    Kalau unduhan berhenti di 3% karena "The page needs to be reloaded", sisa
    file `.part`/fragmen bisa membuat percobaan berikutnya ikut gagal.
    """
    from .fsutil import safe_unlink

    patterns = ("*.part", "*.part-Frag*", "*.ytdl", "*.temp", "source.f*.mp4", "source.f*.webm",
                "source.f*.m4a")
    for pattern in patterns:
        for path in out_dir.glob(pattern):
            safe_unlink(path)


def _extract(yt_dlp, url: str, opts: dict, out_dir: Path) -> Path:
    """Satu percobaan unduh; mengembalikan path file hasil."""
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info and info.get("entries"):
            info = info["entries"][0]
        if not info:
            raise DownloadError("Hasil unduhan kosong (video tidak bisa diambil)")
        path = Path(ydl.prepare_filename(info))
        if not path.exists():
            candidates = sorted(out_dir.glob("source.*"))
            if not candidates:
                raise DownloadError("File hasil download tidak ditemukan")
            path = candidates[0]
    # Pastikan hasilnya benar-benar video: kalau ternyata hanya audio
    # (format fallback / video dihapus), beri tahu jelas.
    try:
        from . import media as _media

        downloaded = _media.probe(path)
    except Exception:
        downloaded = None
    if downloaded is not None and not downloaded.has_video:
        raise DownloadError(
            "Hasil unduhan hanya berisi audio — track video tidak tersedia untuk video ini "
            "(sering pada video yang dibatasi usia). Tambahkan cookies atau atur Format unduhan "
            "ke 'Best' di Pengaturan, lalu coba lagi."
        )
    return path


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

    has_cookies = bool(cookie_path(settings) is not None or (settings.get("ytdlp_cookies_browser") or "").strip())
    client_pref = (settings.get("ytdlp_player_client") or "auto").strip().lower()
    attempts: list[tuple[str, dict | None]] = []
    if client_pref == "tv":
        attempts.append(("tv", {"extractor_args": TV_PLAYER_ARGS}))
    elif client_pref == "default":
        attempts.append(("default", None))
    else:  # auto: client biasa dulu, lalu client tv kalau diblokir
        attempts.append(("default", None))
        attempts.append(("tv", {"extractor_args": TV_PLAYER_ARGS}))

    ffmpeg_ok = ffmpeg_for_ytdlp() is not None
    RETRYABLE = ("age", "bot", "login", "format", "reload", "ffmpeg")
    RETRY_MESSAGE = {
        "age": "Diblokir batas umur, mencoba metode lain…",
        "bot": "YouTube mendeteksi bot, mencoba metode lain…",
        "login": "Video butuh login, mencoba metode lain…",
        "format": "Format tidak tersedia, mencoba format lain…",
        "reload": "YouTube meminta halaman dimuat ulang, mengulang unduhan…",
        "ffmpeg": "ffmpeg tidak ditemukan, memakai format tunggal tanpa gabung…",
    }
    # tanpa ffmpeg, format gabungan (bv+ba) mustahil di-merge → langsung format tunggal
    format_chain = [None, *FORMAT_FALLBACKS] if ffmpeg_ok else [None, "b[ext=mp4]/b", "b"]

    def attempt(extra: dict | None, fmt: str | None) -> tuple[Path | None, str]:
        _clean_partials(out_dir)
        opts = _build_opts(out_dir, settings, hook, extra)
        if fmt:
            opts["format"] = fmt
        try:
            return _extract(yt_dlp, url, opts, out_dir), ""
        except Exception as exc:  # noqa: BLE001 - error yt-dlp apa pun
            return None, str(exc)

    last_error = ""
    last_kind = "other"
    # "reload"/"network" biasanya sesi YouTube basi → ulangi sekali lagi dengan
    # instance YoutubeDL baru (dan buang file parsial sisa unduhan sebelumnya).
    for pass_no in range(2):
        for label, extra in attempts:
            for fmt in format_chain:
                path, error = attempt(extra, fmt)
                if path is not None:
                    return path
                last_error = error
                last_kind = classify_download_error(last_error)
                if last_kind not in RETRYABLE:
                    raise DownloadError(friendly_download_error(last_error, has_cookies))
                if on_progress and last_kind in RETRY_MESSAGE:
                    on_progress(0.02, RETRY_MESSAGE[last_kind])
                # error format/ffmpeg masih bisa diselamatkan format lain;
                # error lain → langsung ganti client/percobaan berikutnya
                if last_kind not in ("format", "ffmpeg"):
                    break
        if last_kind not in ("reload", "network"):
            break
        if on_progress:
            on_progress(0.02, "Mengulang unduhan dari awal…")
        time.sleep(1.5)

    _clean_partials(out_dir)
    raise DownloadError(friendly_download_error(last_error, has_cookies))


def cleanup_dir(path: str | Path) -> None:
    safe_rmtree(path)
