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
# --------------------------------------------------------------------------- #
# impor Playwright (tahan kasus "pip bilang terpasang, tapi app tidak bisa impor")
# --------------------------------------------------------------------------- #
_import_state: dict[str, Any] = {"ok": False, "error": "", "path": ""}


def _site_dirs() -> list[Path]:
    """Semua folder site-packages yang mungkin berisi Playwright."""
    dirs: list[Path] = []
    try:
        import site

        candidates = [site.getusersitepackages()]
        try:
            candidates.extend(site.getsitepackages())
        except Exception:
            pass
        for item in candidates:
            if item:
                path = Path(item)
                if path not in dirs:
                    dirs.append(path)
    except Exception:
        pass
    ver = f"Python{sys.version_info.major}{sys.version_info.minor}"
    for root in (
        os.environ.get("APPDATA", ""),
        os.environ.get("LOCALAPPDATA", ""),
        str(Path.home() / "AppData" / "Roaming"),
    ):
        if root:
            path = Path(root) / "Python" / ver / "site-packages"
            if path not in dirs:
                dirs.append(path)
    return dirs


def playwright_available(refresh: bool = False) -> bool:
    """Playwright bisa diimpor oleh proses ini?"""
    if refresh:
        for name in [m for m in list(sys.modules) if m == "playwright" or m.startswith("playwright.")]:
            sys.modules.pop(name, None)
        _import_state.update({"ok": False, "error": ""})
    if _import_state["ok"]:
        return True
    try:
        import playwright.sync_api  # noqa: F401

        _import_state.update({"ok": True, "error": "", "path": str(Path(playwright.__file__))})
        return True
    except Exception as exc:  # noqa: BLE001 - pesan aslinya penting untuk diagnosa
        _import_state.update({"ok": False, "error": f"{type(exc).__name__}: {exc}".strip()})
        return False


def import_error() -> str:
    playwright_available()
    return str(_import_state.get("error") or "")


def _probe_playwright() -> tuple[str, str]:
    """Tanya proses Python terpisah: di mana Playwright berada / kenapa gagal.

    Dijalankan dengan lingkungan "bersih" (PYTHONNOUSERSITE dibuang) dan semua
    folder site-packages ditambahkan, jadi tetap bisa menemukan Playwright walau
    proses utama tidak bisa mengimpornya.
    """
    import json

    dirs = [str(path) for path in _site_dirs()]
    code = (
        "import json, sys\n"
        "for p in json.loads(sys.argv[1]):\n"
        "    if p not in sys.path: sys.path.append(p)\n"
        "try:\n"
        "    import playwright, playwright.sync_api\n"
        "    print(playwright.__file__)\n"
        "except Exception as exc:\n"
        "    print('ERR ' + type(exc).__name__ + ': ' + str(exc))\n"
        "    sys.exit(3)\n"
    )
    env = dict(os.environ)
    for key in ("PYTHONNOUSERSITE", "PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    try:
        flags: dict[str, Any] = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [sys.executable, "-c", code, json.dumps(dirs)],
            capture_output=True, text=True, errors="replace", timeout=180, env=env, **flags,
        )
    except Exception as exc:  # noqa: BLE001
        return "", f"tidak bisa menjalankan Python: {exc}"
    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and out:
        return out.splitlines()[-1].strip(), ""
    err = (out or (proc.stderr or "").strip()).splitlines()
    message = err[-1].strip() if err else f"kode keluar {proc.returncode}"
    return "", message[4:] if message.startswith("ERR ") else message


def _ensure_importable() -> tuple[bool, str]:
    """Pastikan Playwright bisa dipakai proses ini — perbaiki sys.path kalau perlu."""
    if playwright_available(refresh=True):
        return True, ""
    found, detail = _probe_playwright()
    added: list[str] = []
    candidates: list[Path] = [Path(found).parent.parent] if found else []
    candidates += _site_dirs()
    for candidate in candidates:
        text = str(candidate)
        if text and text not in sys.path:
            sys.path.append(text)
            added.append(text)
    if added and playwright_available(refresh=True):
        return True, ""
    return False, detail or import_error() or "playwright tidak ditemukan"


_ensure_cache: dict[str, Any] = {"at": 0.0, "ok": False}


def ensure_playwright(max_age: float = 15.0) -> bool:
    """Coba pakai Playwright, lengkapi sys.path kalau perlu (hasil di-cache sebentar)."""
    if playwright_available():
        return True
    if time.time() - float(_ensure_cache.get("at") or 0) < max_age:
        return bool(_ensure_cache.get("ok"))
    ok, _why = _ensure_importable()
    _ensure_cache.update({"at": time.time(), "ok": ok})
    return ok


# --------------------------------------------------------------------------- #
# deteksi browser
# --------------------------------------------------------------------------- #
def _registry_browsers() -> list[tuple[str, Path]]:
    """Browser resmi yang terdaftar di Windows (App Paths)."""
    if os.name != "nt":
        return []
    found: list[tuple[str, Path]] = []
    try:
        import winreg
    except Exception:
        return found
    names = {"msedge.exe": "msedge", "chrome.exe": "chrome", "brave.exe": "brave", "vivaldi.exe": "vivaldi"}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for exe, channel in names.items():
            try:
                with winreg.OpenKey(
                    hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
                ) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                path = Path(str(value).strip('"'))
                if path.exists() and path.is_file():
                    found.append((channel, path))
            except OSError:
                continue
            except Exception:
                continue
    return found


def _browser_paths() -> list[tuple[str, Path]]:
    """Kanal browser yang bisa dipakai tanpa mengunduh apa pun."""
    found: list[tuple[str, Path]] = []
    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("PROGRAMW6432", r"C:\Program Files"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        relative = {
            "msedge": [
                r"Microsoft\Edge\Application\msedge.exe",
                r"Microsoft\Edge Beta\Application\msedge.exe",
            ],
            "chrome": [
                r"Google\Chrome\Application\chrome.exe",
                r"Google\Chrome Beta\Application\chrome.exe",
                r"Chromium\Application\chrome.exe",
            ],
            "brave": [r"BraveSoftware\Brave-Browser\Application\brave.exe"],
            "vivaldi": [r"Vivaldi\Application\vivaldi.exe"],
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
                try:
                    if candidate.exists() and candidate.is_file():
                        found.append((channel, candidate))
                except OSError:
                    continue
    found.extend(_registry_browsers())
    if not found:
        for binary, channel in (
            ("google-chrome", "chrome"),
            ("chromium", "chrome"),
            ("microsoft-edge", "msedge"),
            ("msedge", "msedge"),
            ("chrome", "chrome"),
            ("brave-browser", "brave"),
        ):
            path = shutil.which(binary)
            if path:
                found.append((channel, Path(path)))
    unique: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for channel, path in found:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append((channel, path))
    return unique


def browser_channel() -> str:
    """Nama kanal browser yang dipakai Playwright ('' = chromium bawaan)."""
    browsers = _browser_paths()
    return browsers[0][0] if browsers else ""


def _chromium_roots() -> list[Path]:
    roots: list[Path] = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        roots.append(Path(local) / "ms-playwright")
    roots.append(Path.home() / ".cache" / "ms-playwright")
    return roots


def chromium_downloaded() -> bool:
    """Chromium bawaan Playwright benar-benar ada (folder + file exe-nya)."""
    for root in _chromium_roots():
        try:
            if not root.exists():
                continue
            entries = [p for p in root.glob("chromium-*") if p.is_dir()]
        except OSError:
            continue
        for entry in entries:
            for exe in ("chrome.exe", "chrome", "headless_shell.exe"):
                for candidate in (entry / exe, entry / "chrome-win" / exe, entry / "chrome-linux" / exe):
                    try:
                        if candidate.exists() and candidate.is_file():
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
    if not ensure_playwright():
        return False
    return bool(browser_channel()) or chromium_downloaded()


def install_command() -> str:
    """Perintah manual kalau pemasangan otomatis gagal (bisa disalin user)."""
    return f'"{sys.executable}" -m playwright install chromium'


def diag() -> dict[str, Any]:
    """Alasan teknis kenapa belum siap — ditampilkan di halaman TikTok."""
    browsers = _browser_paths()
    dirs = _site_dirs()
    return {
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "user_site": str(dirs[0]) if dirs else "",
        "user_site_in_path": bool(dirs and str(dirs[0]) in sys.path),
        "no_user_site": os.environ.get("PYTHONNOUSERSITE", ""),
        "playwright_ok": ensure_playwright(),
        "playwright_error": import_error(),
        "playwright_path": str(_import_state.get("path") or ""),
        "browsers": [{"channel": c, "path": str(p)} for c, p in browsers],
        "chromium": chromium_downloaded(),
        "chromium_dirs": [str(p) for p in _chromium_roots()],
        "install_command": install_command(),
        "os": f"{os.name} {sys.platform}",
    }


def status() -> dict[str, Any]:
    with _state_lock:
        state = dict(_state)
    browsers = _browser_paths()
    return {
        "installed": ensure_playwright(),
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
        "diag": diag(),
    }


# --------------------------------------------------------------------------- #
# pemasangan (sekali klik dari UI)
# --------------------------------------------------------------------------- #
def _sub_env() -> dict[str, str]:
    """Lingkungan untuk subprocess: user-site jangan dimatikan, path paket ikut."""
    env = dict(os.environ)
    for key in ("PYTHONNOUSERSITE", "PYTHONHOME"):
        env.pop(key, None)
    extra: list[str] = []
    found = str(_import_state.get("path") or "")
    if found:
        extra.append(str(Path(found).parent.parent))
    extra.extend(str(path) for path in _site_dirs() if path.exists())
    current = env.get("PYTHONPATH", "")
    have = current.split(os.pathsep) if current else []
    ordered: list[str] = []
    for item in extra:
        if item and item not in ordered and item not in have:
            ordered.append(item)
    env["PYTHONPATH"] = os.pathsep.join(ordered + have)
    return env


def _run(cmd: list[str], label: str) -> bool:
    _say(label)
    try:
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=1800,
            env=_sub_env(), **flags,
        )
    except Exception as exc:  # noqa: BLE001
        _say(f"Gagal: {exc}")
        return False
    raw = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip().splitlines()
    clean = [line.strip() for line in raw if line.strip() and line.strip() not in ("{", "}")]
    if proc.returncode != 0:
        hits = [line for line in clean
                if re.search(r"error|gagal|failed|denied|timeout|refused|resolve|not found", line, re.I)]
        tail = hits[-3:] if hits else clean[-3:]
    else:
        tail = clean[-6:]
    seen: set[str] = set()
    for line in tail:
        text = line[:220]
        if text in seen:
            continue
        seen.add(text)
        _say(text)
    if proc.returncode != 0:
        _say(f"{label} gagal (kode {proc.returncode})")
        return False
    return True


def _pip_install() -> bool:
    """Pasang Playwright — coba --user dulu, lalu biasa."""
    attempts = (
        ([sys.executable, "-m", "pip", "install", "--upgrade", "--user", "playwright"],
         "pip install playwright (--user)"),
        ([sys.executable, "-m", "pip", "install", "--upgrade", "playwright"],
         "pip install playwright"),
    )
    for cmd, label in attempts:
        if _run(cmd, label):
            return True
    return False


def _install_browser_sync() -> bool:
    with _state_lock:
        _state["installing_browser"] = True
    try:
        ok = _run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            "Mengunduh browser Chromium (±130 MB, sekali saja)…",
        )
        if ok and chromium_downloaded():
            _say("Browser Clipper siap dipakai.")
            return True
        if ok:
            _say("Unduhan selesai, tapi file browser tidak ditemukan — coba lagi.")
        else:
            _say("Unduhan browser gagal. Periksa koneksi internet, lalu coba lagi.")
            _say(f"Perintah manual: {install_command()}")
        return False
    finally:
        with _state_lock:
            _state["installing_browser"] = False


def install(with_browser: bool = True) -> dict[str, Any]:
    """Pasang Playwright (+ browser bawaan kalau tidak ada Chrome/Edge)."""
    with _state_lock:
        if _state["installing"] or _state["installing_browser"]:
            return status()
        _state["installing"] = True
        _state["log"] = []
    _say("Memasang dukungan TikTok (sekali saja)…")

    def worker() -> None:
        try:
            ok, why = _ensure_importable()
            if not ok:
                _say("Playwright belum bisa dipakai — memasang paketnya…")
                _pip_install()
                ok, why = _ensure_importable()
            if not ok:
                _say(f"Paket Playwright belum bisa dipakai: {why}")
                _say(f"Coba jalankan manual: \"{sys.executable}\" -m pip install --user playwright")
                return
            _say("Paket Playwright siap.")

            if browser_channel():
                _say(f"Memakai browser yang sudah terpasang: {_browser_paths()[0][1]}")
            elif chromium_downloaded():
                _say("Memakai Chromium bawaan Playwright yang sudah ada.")
            elif with_browser:
                if not _install_browser_sync():
                    return

            if ready():
                _say("Selesai. Tekan \"Buka TikTok & login\" untuk mulai.")
            elif not browser_channel() and not chromium_downloaded():
                _say(
                    "Paket Playwright terpasang, tapi belum ada browser. Pasang Google Chrome/Edge, "
                    "atau klik \"Unduh browser Clipper\"."
                )
            else:
                _say(f"Browser ada, tapi Playwright gagal dipakai: {import_error() or why}")
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
    _say("Mengunduh Chromium untuk Playwright (±130 MB)…")

    def worker() -> None:
        try:
            ok, why = _ensure_importable()
            if not ok:
                _say("Playwright belum bisa dipakai — memasang paketnya dulu…")
                _pip_install()
                ok, why = _ensure_importable()
            if not ok:
                _say(f"Paket Playwright belum bisa dipakai: {why}")
                return
            _install_browser_sync()
        finally:
            with _state_lock:
                _state["installing_browser"] = False

    threading.Thread(target=worker, daemon=True).start()
    return status()


def recheck() -> dict[str, Any]:
    """Periksa ulang tanpa memasang apa pun (untuk tombol "Periksa ulang")."""
    _ensure_cache.update({"at": 0.0, "ok": False})
    ok, why = _ensure_importable()
    _ensure_cache.update({"at": time.time(), "ok": ok})
    if ok:
        _say("Playwright siap dipakai.")
    else:
        _say(f"Playwright belum siap: {why}")
    browsers = _browser_paths()
    if browsers:
        _say(f"Browser ditemukan: {browsers[0][1]}")
    elif chromium_downloaded():
        _say("Chromium bawaan Playwright ditemukan.")
    else:
        _say("Belum ada browser yang bisa dipakai.")
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
    browsers = _browser_paths()
    attempts: list[dict[str, Any]] = []
    if browsers:
        channel, path = browsers[0]
        attempts.append({"executable_path": str(path)})
        if channel in ("chrome", "msedge"):
            attempts.append({"channel": channel})
    attempts.append({})
    last_error: Exception | None = None
    for extra in attempts:
        try:
            return playwright.chromium.launch_persistent_context(**options, **extra)
        except Exception as exc:  # noqa: BLE001 - coba cara lain dulu
            last_error = exc
            continue
    detail = str(last_error).splitlines()[0][:180] if last_error else "tidak diketahui"
    raise TikTokError(
        "Browser tidak bisa dibuka. Pastikan Chrome atau Edge terpasang, "
        "atau klik \"Unduh browser\" di halaman TikTok. "
        f"Detail: {detail}"
    )


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
    if not _ensure_importable()[0]:
        raise TikTokError(
            "Playwright belum bisa dipakai. Buka halaman TikTok di Clipper → "
            "klik \"Pasang dukungan TikTok\" (sekali saja), lalu \"Periksa ulang\"."
        )
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
    if not _ensure_importable()[0]:
        raise TikTokError(
            "Dukungan TikTok belum bisa dipakai. Buka halaman TikTok di Clipper → "
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
