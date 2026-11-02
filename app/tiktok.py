"""Upload clip ke TikTok — gratis, lokal, tanpa API berbayar.

TikTok tidak punya API upload gratis yang bisa langsung dipakai tanpa
mendaftarkan aplikasi & menunggu review, jadi Clipper memakai cara yang paling
jujur dan tahan lama: **mengemudikan browser di komputer kamu sendiri**.

Alur:
1. Clipper membuka jendela browser khusus (profil tersimpan di
   ``data/tiktok_profile``), memakai Chrome/Edge yang sudah terpasang di Windows.
2. Login sekali di jendela itu → cookie disimpan di profil, upload berikutnya
   tidak perlu login lagi.
3. Saat upload, Clipper membuka TikTok Studio, memasukkan file video, mengisi
   caption + hashtag, lalu (kalau diaktifkan) menekan tombol Post.

Semua proses berjalan di mesin user: tidak ada server perantara, tidak ada
kredensial yang dikirim ke mana pun.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR, ensure_dirs

PROFILE_DIR = DATA_DIR / "tiktok_profile"
SCREENSHOT_FILE = DATA_DIR / "tiktok_terakhir.png"
LOGIN_URL = "https://www.tiktok.com/login"
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=upload"
HOME_URL = "https://www.tiktok.com/"

# TikTok Studio memakai Draft.js; beberapa versi memakai textarea biasa.
FILE_INPUTS = ("input[type=file]", "input[accept*='video']")
CAPTION_BOXES = (
    "div[contenteditable='true']",
    "div.public-DraftEditor-content",
    "div[data-contents='true']",
    "textarea[data-e2e='caption']",
    "textarea",
)
POST_NAMES = re.compile(r"^(post|publish|upload|posting|unggah|kirim)$", re.IGNORECASE)

_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "installing": False,
    "installing_browser": False,
    "log": [],
    "login_window": False,
    "message": "",
}


class TikTokError(RuntimeError):
    pass


def _say(message: str) -> None:
    with _state_lock:
        _state["message"] = message
        log = list(_state["log"])
        log.append(f"{time.strftime('%H:%M:%S')}  {message}")
        _state["log"] = log[-200:]


# --------------------------------------------------------------------------- #
# deteksi lingkungan
# --------------------------------------------------------------------------- #
def playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def _browser_paths() -> list[tuple[str, Path]]:
    """Kanal browser yang bisa dipakai tanpa mengunduh apa pun."""
    found: list[tuple[str, Path]] = []
    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        relative = {
            "msedge": [r"Microsoft\Edge\Application\msedge.exe"],
            "chrome": [r"Google\Chrome\Application\chrome.exe"],
            "brave": [r"BraveSoftware\Brave-Browser\Application\brave.exe"],
        }
    else:
        roots = ["/usr/bin", "/usr/local/bin", "/opt/google/chrome", "/snap/bin"]
        relative = {
            "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
            "msedge": ["microsoft-edge", "microsoft-edge-stable"],
            "brave": ["brave-browser", "brave"],
        }
    for channel, names in relative.items():
        for root in roots:
            if not root:
                continue
            for name in names:
                candidate = Path(root) / name
                if candidate.exists() and candidate.is_file():
                    found.append((channel, candidate))
    if not found:
        for binary, channel in (
            ("google-chrome", "chrome"),
            ("chromium", "chrome"),
            ("microsoft-edge", "msedge"),
            ("brave-browser", "brave"),
        ):
            path = shutil.which(binary)
            if path:
                found.append((channel, Path(path)))
    return found


def browser_channel() -> str:
    """Nama kanal browser yang dipakai Playwright ('' = chromium bawaan)."""
    browsers = _browser_paths()
    return browsers[0][0] if browsers else ""


def chromium_downloaded() -> bool:
    roots = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            roots.append(Path(local) / "ms-playwright")
    roots.append(Path.home() / ".cache" / "ms-playwright")
    for root in roots:
        try:
            if root.exists() and any(root.glob("chromium-*")):
                return True
        except OSError:
            continue
    return False


def _login_marker() -> Path:
    return PROFILE_DIR / ".clipper_login"


def logged_in() -> bool:
    """Login TikTok pernah berhasil (ditandai setelah sesi terdeteksi)."""
    try:
        return _login_marker().exists()
    except OSError:
        return False


def _mark_logged_in(value: bool = True) -> None:
    try:
        ensure_dirs()
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        if value:
            _login_marker().write_text(str(int(time.time())), encoding="utf-8")
        elif _login_marker().exists():
            _login_marker().unlink()
    except OSError:
        pass


def ready() -> bool:
    """Siap dipakai: Playwright terpasang dan ada browser yang bisa dijalankan."""
    if not playwright_available():
        return False
    return bool(browser_channel()) or chromium_downloaded()


def status() -> dict[str, Any]:
    with _state_lock:
        state = dict(_state)
    browsers = _browser_paths()
    return {
        "installed": playwright_available(),
        "ready": ready(),
        "browser": browsers[0][0] if browsers else "",
        "browser_path": str(browsers[0][1]) if browsers else "",
        "browsers": [{"channel": c, "path": str(p)} for c, p in browsers],
        "chromium": chromium_downloaded(),
        "logged_in": logged_in(),
        "profile": str(PROFILE_DIR),
        "login_window": bool(state.get("login_window")),
        "installing": bool(state.get("installing") or state.get("installing_browser")),
        "message": state.get("message") or "",
        "log": state.get("log", [])[-12:],
    }


# --------------------------------------------------------------------------- #
# pemasangan (sekali klik dari UI)
# --------------------------------------------------------------------------- #
def _run(cmd: list[str], label: str) -> bool:
    _say(label)
    try:
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=1800, **flags
        )
    except Exception as exc:  # noqa: BLE001
        _say(f"Gagal: {exc}")
        return False
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-15:]
    for line in tail:
        if line.strip():
            _say(line.strip())
    if proc.returncode != 0:
        _say(f"{label} gagal (kode {proc.returncode})")
        return False
    return True


def install(with_browser: bool = True) -> dict[str, Any]:
    """Pasang Playwright (+ browser bawaan kalau tidak ada Chrome/Edge)."""
    with _state_lock:
        if _state["installing"] or _state["installing_browser"]:
            return status()
        _state["installing"] = True
        _state["log"] = []
    _say("Memasang Playwright (paket kecil, sekali saja)…")

    def worker() -> None:
        try:
            ok = _run([sys.executable, "-m", "pip", "install", "--upgrade", "playwright"],
                      "pip install playwright")
            if not ok:
                _say("Pemasangan gagal. Periksa koneksi internet, lalu coba lagi.")
                return
            if with_browser and not browser_channel() and not chromium_downloaded():
                with _state_lock:
                    _state["installing_browser"] = True
                try:
                    got_browser = _run(
                        [sys.executable, "-m", "playwright", "install", "chromium"],
                        "Mengunduh browser Chromium (±130 MB)…",
                    )
                finally:
                    with _state_lock:
                        _state["installing_browser"] = False
                if not got_browser:
                    _say("Paket Playwright siap, tapi browser belum ada. Pasang Google Chrome/Edge, "
                         "atau klik \"Unduh browser Clipper\".")
                    return
            if ready():
                _say("Selesai. Tekan \"Buka TikTok & login\" untuk mulai.")
            else:
                _say("Paket terpasang, tapi browser belum ditemukan. Pasang Chrome/Edge atau klik "
                     "\"Unduh browser Clipper\".")
        finally:
            with _state_lock:
                _state["installing"] = False
                _state["installing_browser"] = False

    threading.Thread(target=worker, daemon=True).start()
    return status()


def install_browser() -> dict[str, Any]:
    """Unduh Chromium bawaan Playwright (kalau Chrome/Edge tidak terpasang)."""
    with _state_lock:
        if _state["installing_browser"]:
            return status()
        _state["installing_browser"] = True
    _say("Mengunduh Chromium untuk Playwright (±130 MB)…")

    def worker() -> None:
        try:
            _run([sys.executable, "-m", "playwright", "install", "chromium"],
                 "playwright install chromium")
        finally:
            with _state_lock:
                _state["installing_browser"] = False

    threading.Thread(target=worker, daemon=True).start()
    return status()


# --------------------------------------------------------------------------- #
# helper Playwright
# --------------------------------------------------------------------------- #
def _launch(playwright: Any, headless: bool = False) -> Any:
    """Buka browser dengan profil tetap supaya login TikTok tidak hilang."""
    ensure_dirs()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    options = dict(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport=None,
        args=[
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        ignore_default_args=["--enable-automation"],
    )
    channel = browser_channel()
    try:
        if channel:
            return playwright.chromium.launch_persistent_context(channel=channel, **options)
        return playwright.chromium.launch_persistent_context(**options)
    except Exception as exc:  # noqa: BLE001
        raise TikTokError(
            "Browser tidak bisa dibuka. Pastikan Chrome atau Edge terpasang, "
            "atau klik \"Unduh browser\" di halaman TikTok. "
            f"Detail: {str(exc).splitlines()[0][:180]}"
        ) from exc


def _page(context: Any) -> Any:
    pages = [p for p in context.pages if not p.is_closed()]
    page = pages[0] if pages else context.new_page()
    try:
        page.bring_to_front()
    except Exception:
        pass
    return page


def _has_session(page: Any) -> bool:
    try:
        cookies = page.context.cookies("https://www.tiktok.com")
    except Exception:
        return False
    return any(
        c.get("name") in ("sessionid", "sessionid_ss", "sid_tt") and c.get("value")
        for c in cookies
    )


def _visible_first(page: Any, selectors: tuple[str, ...], timeout: float = 20.0) -> Any | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    return locator
            except Exception:
                continue
        time.sleep(0.5)
    return None


def _screenshot(page: Any) -> None:
    try:
        page.screenshot(path=str(SCREENSHOT_FILE), full_page=False)
    except Exception:
        pass


def _ensure_login(page: Any, progress: Callable[[float, str], None], wait_seconds: float) -> None:
    if _has_session(page):
        _mark_logged_in(True)
        return
    progress(0.08, "Belum login TikTok — silakan login di jendela browser yang terbuka…")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)
    except Exception:
        pass
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if _has_session(page):
            _mark_logged_in(True)
            progress(0.18, "Login TikTok berhasil (tersimpan untuk lain kali)")
            time.sleep(1.5)
            return
        if page.is_closed():
            raise TikTokError("Jendela TikTok ditutup sebelum login selesai")
        time.sleep(2)
    raise TikTokError(
        "Login TikTok belum selesai. Coba lagi lalu selesaikan login di jendela yang terbuka "
        "(email/telepon + kode, atau scan QR)."
    )


def _set_privacy(page: Any, privacy: str) -> None:
    """Pilih siapa yang bisa menonton (best effort, abaikan kalau tidak ada)."""
    labels = {
        "public": ("Everyone", "Semua orang", "Public"),
        "friends": ("Friends", "Teman"),
        "private": ("Only you", "Hanya kamu", "Private"),
    }.get((privacy or "public").lower(), ())
    for label in labels:
        try:
            option = page.get_by_text(label, exact=False).first
            if option.count():
                option.click(timeout=4000)
                time.sleep(0.6)
                return
        except Exception:
            continue


def _click_post(page: Any, timeout: float) -> bool:
    """Tekan tombol Post/Publish sampai benar-benar bisa diklik."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            button = page.get_by_role("button", name=POST_NAMES).first
            if button.count() and button.is_visible() and button.is_enabled():
                button.click(timeout=8000)
                return True
        except Exception:
            pass
        # fallback: TikTok kadang memakai <div role="button"> atau teks Indonesia
        for selector in (
            "button:has-text('Post')",
            "div[role='button']:has-text('Post')",
            "button:has-text('Posting')",
        ):
            try:
                button = page.locator(selector).first
                if button.count() and button.is_visible() and button.is_enabled():
                    button.click(timeout=8000)
                    return True
            except Exception:
                continue
        time.sleep(2)
    return False


def _wait_published(page: Any, timeout: float) -> str:
    """Tunggu konfirmasi TikTok; kembalikan URL video kalau muncul."""
    deadline = time.time() + timeout
    pattern = re.compile(r"tiktok\.com/@([^/?#]+)/video/(\d+)")
    while time.time() < deadline:
        if page.is_closed():
            return ""
        try:
            url = page.url or ""
        except Exception:
            url = ""
        match = pattern.search(url)
        if match:
            return f"https://www.tiktok.com/@{match.group(1)}/video/{match.group(2)}"
        time.sleep(2)
    return ""


# --------------------------------------------------------------------------- #
# login manual (tombol di UI)
# --------------------------------------------------------------------------- #
def open_login_window(wait_seconds: float = 900) -> dict[str, Any]:
    """Buka jendela browser TikTok supaya user bisa login sekali."""
    if not playwright_available():
        raise TikTokError("Playwright belum terpasang. Klik \"Pasang dukungan TikTok\" dulu.")
    with _state_lock:
        if _state["login_window"]:
            return status()
        _state["login_window"] = True
    _say("Membuka jendela TikTok — login di sana, jendela menutup sendiri setelah berhasil.")

    def worker() -> None:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                context = _launch(pw, headless=False)
                try:
                    page = _page(context)
                    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)
                    deadline = time.time() + wait_seconds
                    while time.time() < deadline:
                        if _has_session(page):
                            _mark_logged_in(True)
                            _say("Login TikTok berhasil disimpan.")
                            time.sleep(2)
                            break
                        if page.is_closed():
                            break
                        time.sleep(2)
                    else:
                        _say("Jendela login ditutup karena waktu habis.")
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
        except Exception as exc:  # noqa: BLE001
            _say(f"Gagal membuka jendela TikTok: {str(exc).splitlines()[0][:160]}")
        finally:
            with _state_lock:
                _state["login_window"] = False

    threading.Thread(target=worker, daemon=True).start()
    return status()


def logout() -> None:
    """Lupakan sesi TikTok: hapus penanda login + profil browser (perlu login lagi)."""
    _mark_logged_in(False)
    try:
        from .fsutil import safe_rmtree

        safe_rmtree(PROFILE_DIR)
    except Exception:  # noqa: BLE001
        pass
    _say("Sesi TikTok dihapus. Login lagi kalau mau upload lewat browser.")


# --------------------------------------------------------------------------- #
# upload
# --------------------------------------------------------------------------- #
def build_caption(title: str, description: str = "", hashtags: str = "") -> str:
    """Caption TikTok: judul + hashtag, maksimal 2200 karakter."""
    title = (title or "Clip").strip()
    description = (description or "").strip()
    tags = (hashtags or "").strip()
    if not tags:
        tags = "#fyp #foryou"
    tags = " ".join(
        part if part.startswith("#") else f"#{part}"
        for part in re.split(r"[,\s]+", tags)
        if part
    )
    text = title
    if description and description.lower() not in title.lower():
        text = f"{text}\n\n{description}"
    text = f"{text}\n\n{tags}".strip()
    return text[:2200]


def upload_video(
    file_path: str | Path,
    caption: str = "",
    hashtags: str = "",
    privacy: str = "public",
    auto_post: bool = True,
    on_progress: Callable[[float, str], None] | None = None,
    wait_seconds: float = 420,
    headless: bool = False,
) -> dict[str, Any]:
    """Kirim satu file video ke TikTok lewat jendela browser di mesin ini.

    Kembalian: ``{"url": str, "posted": bool, "caption": str}``.
    """
    path = Path(file_path)
    if not path.exists():
        raise TikTokError("File clip belum ada — render dulu")
    if not playwright_available():
        raise TikTokError(
            "Dukungan TikTok belum terpasang. Buka halaman TikTok di Clipper → "
            "klik \"Pasang dukungan TikTok\" (sekali saja, butuh internet)."
        )
    progress = on_progress or (lambda fraction, message: None)
    text = build_caption(caption, "", hashtags)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        context = _launch(pw, headless=headless)
        try:
            page = _page(context)
            progress(0.05, "Membuka TikTok Studio…")
            try:
                page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=120000)
            except Exception as exc:  # noqa: BLE001
                raise TikTokError(
                    "Tidak bisa membuka TikTok. Periksa koneksi internet. "
                    f"Detail: {str(exc).splitlines()[0][:160]}"
                ) from exc

            _ensure_login(page, progress, wait_seconds)
            if "tiktokstudio" not in (page.url or ""):
                page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=120000)
                time.sleep(2)

            progress(0.22, "Memilih file video…")
            file_input = None
            for selector in FILE_INPUTS:
                try:
                    candidate = page.locator(selector).first
                    candidate.wait_for(state="attached", timeout=60000)
                    file_input = candidate
                    break
                except Exception:
                    continue
            if file_input is None:
                _screenshot(page)
                raise TikTokError(
                    "Kolom upload TikTok tidak ditemukan. Buka halaman TikTok di Clipper → "
                    "\"Buka TikTok Studio\", lalu coba upload lagi."
                )
            file_input.set_input_files(str(path))
            progress(0.35, "Video diunggah ke TikTok…")

            progress(0.5, "Menunggu TikTok memproses video…")
            box = _visible_first(page, CAPTION_BOXES, timeout=180)
            if box is None:
                _screenshot(page)
                raise TikTokError("Kolom caption TikTok tidak muncul — coba lagi sebentar lagi.")
            try:
                box.click(timeout=10000)
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.keyboard.insert_text(text[:2200])
            except Exception:
                pass
            progress(0.62, "Caption terisi")

            _set_privacy(page, privacy)
            progress(0.72, "Menunggu video siap diposting…")

            if not auto_post:
                progress(0.9, "Semua siap — klik Post di jendela TikTok")
                url = _wait_published(page, min(wait_seconds, 300))
                return {"url": url, "posted": bool(url), "caption": text}

            posted = _click_post(page, timeout=min(wait_seconds, 300))
            if not posted:
                _screenshot(page)
                raise TikTokError(
                    "Tombol Post belum bisa diklik (TikTok masih memproses video). "
                    "Coba upload lagi sebentar lagi."
                )
            progress(0.9, "Terkirim! Menunggu konfirmasi TikTok…")
            url = _wait_published(page, timeout=min(wait_seconds, 240))
            progress(1.0, "Upload TikTok selesai")
            return {"url": url, "posted": True, "caption": text}
        except TikTokError:
            raise
        except Exception as exc:  # noqa: BLE001
            try:
                _screenshot(page)
            except Exception:
                pass
            raise TikTokError(f"Upload TikTok gagal: {str(exc).splitlines()[0][:200]}") from exc
        finally:
            try:
                context.close()
            except Exception:
                pass
