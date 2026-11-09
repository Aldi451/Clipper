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

import json
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
    "login_thread": None,
    "login_step": "",
    "login_url": "",
    "login_cookies": [],
    "message": "",
    "version": "",
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
        "profile": str(_active_profile()),
        "profile_locks": _profile_locks(_active_profile()),
        "browser_log": str(BROWSER_LOG),
        "os": f"{os.name} {sys.platform}",
    }


def status() -> dict[str, Any]:
    if not _state.get("version"):
        try:
            from .config import VERSION

            _state["version"] = str(VERSION)
        except Exception:  # noqa: BLE001
            pass
    with _state_lock:
        # pengaman: kalau proses login mati tanpa sempat mereset penanda
        # (mis. aplikasi ditutup paksa), jangan biarkan tombol terkunci selamanya
        started = float(_state.get("login_started") or 0)
        limit = float(_state.get("login_wait") or 0) or 900
        if _state.get("login_window") and started and time.time() - started > limit + 120:
            _state["login_window"] = False
            _state["login_started"] = 0
            _state["message"] = "Jendela login ditutup karena waktu habis."
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
        "last_mode": state.get("last_mode") or "",
        "browser_pick": browser_pick(),
        "session": session_info(),
        "login": login_state(),
        "browser_errors": state.get("browser_errors") or [],
        "browser_log": str(BROWSER_LOG),
        "profile_active": str(_active_profile()),
        "profile_in_use": _profile_in_use(_active_profile()) if os.name == "nt" else bool(
            _profile_locks(_active_profile())
        ),
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


def _in_venv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _pip_install() -> bool:
    """Pasang Playwright — di venv langsung biasa, di Python sistem coba --user dulu."""
    base = [sys.executable, "-m", "pip", "install", "--upgrade"]
    attempts: list[tuple[list[str], str]] = []
    if _in_venv():
        attempts.append((base + ["playwright"], "pip install playwright"))
    else:
        attempts.append((base + ["--user", "playwright"], "pip install playwright (--user)"))
        attempts.append((base + ["playwright"], "pip install playwright"))
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
BROWSER_LOG = DATA_DIR / "logs" / "tiktok_browser.log"

# Penanda proses dijalankan oleh Windows Service (SCM). Service tidak punya
# desktop, jadi jendela browser tidak mungkin muncul.
SERVICE_FLAG = "CLIPPER_SERVICE"


def session_info() -> dict[str, Any]:
    """Apakah Clipper bisa membuka jendela browser di layar user?

    Penting: kalau Clipper dijalankan sebagai Windows service, prosesnya ada di
    *session 0* (tanpa desktop). Browser headless jalan, tapi jendela login
    TIDAK bisa muncul — dulu inilah sebab "Gagal membuka jendela TikTok" yang
    tidak jelas (log menunjukkan profil di C:\WINDOWS\system32\config\systemprofile).
    """
    info: dict[str, Any] = {
        "session_id": None,
        "session_name": os.environ.get("SESSIONNAME", ""),
        "user": os.environ.get("USERNAME", ""),
        "service_flag": os.environ.get(SERVICE_FLAG, "") == "1",
        "interactive": True,
        "reason": "",
    }
    if os.name == "nt":
        try:
            import ctypes

            sid = ctypes.c_ulong()
            if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
                info["session_id"] = int(sid.value)
        except Exception:  # noqa: BLE001
            pass
    name = str(info["session_name"] or "").lower()
    user = str(info["user"] or "").lower()
    sid = info["session_id"]
    if info["service_flag"] or sid == 0 or name.startswith("services") or user in (
        "system", "local service", "network service",
    ):
        info["interactive"] = False
        info["reason"] = (
            "Clipper sedang berjalan sebagai Windows service (tanpa desktop), "
            "jadi jendela browser tidak bisa dibuka."
        )
    return info
PROFILE_PICK = DATA_DIR / "tiktok_profile_dir.txt"

# Argumen dasar untuk semua cara buka browser. Tidak ada yang berbahaya di sini:
# hanya membuat jendela tampil penuh dan mengurangi tanda "dikendalikan robot".
BASE_ARGS = [
    "--start-maximized",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]


def _write_browser_log(lines: list[str]) -> None:
    """Simpan detail percobaan buka browser ke data/logs/tiktok_browser.log."""
    try:
        BROWSER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with BROWSER_LOG.open("a", encoding="utf-8") as out:
            out.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            for line in lines:
                out.write(line.rstrip() + "\n")
    except OSError:
        pass


def _short_error(exc: Exception | str) -> str:
    """Satu baris pesan error yang masih bisa dibaca manusia."""
    text = str(exc).strip().splitlines()
    return (text[0] if text else "tidak diketahui")[:220]


def _active_profile() -> Path:
    """Folder profil browser yang sedang dipakai (bisa berpindah kalau terkunci)."""
    try:
        recorded = PROFILE_PICK.read_text(encoding="utf-8").strip()
        if recorded:
            return Path(recorded)
    except OSError:
        pass
    return PROFILE_DIR


def _set_active_profile(path: Path) -> None:
    try:
        PROFILE_PICK.write_text(str(path), encoding="utf-8")
    except OSError:
        pass


def _profile_locks(profile: Path) -> list[str]:
    """File penanda profil sedang dipakai proses browser lain."""
    names = ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile")
    found: list[str] = []
    for name in names:
        try:
            if (profile / name).exists():
                found.append(name)
        except OSError:
            continue
    return found


def _profile_in_use(profile: Path) -> bool:
    """Ada proses browser yang memakai folder profil ini? (deteksi cepat)"""
    if os.name != "nt":
        return bool(_profile_locks(profile))
    try:
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'"
            " or Name='brave.exe' or Name='vivaldi.exe'\" | "
            "Where-Object { $_.CommandLine -like '*tiktok_profile*' } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=15,
        )
        return bool(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


def _pick_file() -> Path:
    return DATA_DIR / "tiktok_browser_pick.json"


def browser_pick() -> dict[str, Any]:
    """Browser yang terakhir terbukti bisa dipakai (disimpan antar sesi)."""
    try:
        data = json.loads(_pick_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def set_browser_pick(label: str, extra: dict[str, Any] | None = None) -> None:
    """Ingat browser yang berhasil supaya percobaan berikutnya langsung ke situ.

    Kasus nyata: di Windows tertentu Edge gagal dibuka (kebijakan/antivirus)
    padahal Chrome normal. Tanpa ini Clipper selalu mencoba Edge lebih dulu.
    """
    payload = {"label": str(label), "extra": dict(extra or {}), "at": int(time.time()),
               "channel": _mode_channel(label)}
    try:
        _pick_file().parent.mkdir(parents=True, exist_ok=True)
        _pick_file().write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _mode_channel(label: str) -> str:
    low = (label or "").lower()
    for name in ("msedge", "edge", "chrome", "brave", "vivaldi", "chromium"):
        if name in low:
            return "msedge" if name == "edge" else name
    return ""


def reclaim_profile() -> dict[str, Any]:
    """Tutup proses browser yang masih memegang profil login Clipper.

    Aman: hanya proses yang command line-nya memuat folder profil Clipper
    (``data/tiktok_profile``). Browser yang Bang pakai untuk kegiatan lain
    tidak disentuh. Berguna kalau jendela login sebelumnya tidak tertutup
    sempurna sehingga profil "terkunci" dan login berikutnya gagal.
    """
    profile = _active_profile()
    killed: list[str] = []
    if os.name == "nt":
        script = (
            "$p = Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'"
            " or Name='brave.exe' or Name='vivaldi.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{profile.name}*' }}; "
            "foreach ($x in $p) { try { Stop-Process -Id $x.ProcessId -Force -ErrorAction Stop; "
            "Write-Output $x.ProcessId } catch {} }"
        )
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                 capture_output=True, text=True, timeout=25)
            killed = [line.strip() for line in out.stdout.split() if line.strip().isdigit()]
        except Exception as exc:  # noqa: BLE001
            _say(f"Gagal menutup proses browser lama: {_short_error(exc)}")
    # hapus sisa file kunci (kalau prosesnya sudah mati)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
        try:
            (profile / name).unlink()
        except OSError:
            pass
    time.sleep(0.4)
    still = _profile_in_use(profile) or bool(_profile_locks(profile))
    if killed:
        _say(f"Menutup {len(killed)} proses browser lama yang memegang profil TikTok.")
    if not still:
        _say("Profil login TikTok bebas dipakai lagi — coba login sekarang.")
    return {"ok": not still, "killed": len(killed), "profile": str(profile),
            "still_in_use": still, "status": status()}


def _launch_modes() -> list[tuple[str, dict[str, Any]]]:
    """Daftar cara membuka browser, dari yang paling diinginkan.

    Kalau sebuah browser pernah berhasil dipakai, browser itu didahulukan —
    jadi mesin yang Edge-nya diblokir langsung memakai Chrome tanpa menunggu.
    """
    modes: list[tuple[str, dict[str, Any]]] = []
    seen_paths: set[str] = set()
    seen_labels: set[str] = set()

    def add(label: str, extra: dict[str, Any]) -> None:
        key = str(extra.get("executable_path") or extra.get("channel") or label)
        if key in seen_paths or label in seen_labels:
            return
        seen_paths.add(key)
        seen_labels.add(label)
        modes.append((label, extra))

    browsers = _browser_paths()
    preferred = browser_pick()
    preferred_channel = str(preferred.get("channel") or "")

    # 1) browser yang terakhir berhasil
    for channel, path in browsers:
        if str(path) == str((preferred.get("extra") or {}).get("executable_path") or ""):
            add(f"{channel} → {path}", {"executable_path": str(path)})
            break
    if preferred_channel and not modes:
        for channel, path in browsers:
            if channel == preferred_channel:
                add(f"{channel} → {path}", {"executable_path": str(path)})
                break

    # 2) sisanya: Chrome lebih dulu daripada Edge (Edge paling sering diblokir
    #    kebijakan kantor/sekolah, Chrome hampir selalu aman)
    order = {"chrome": 0, "brave": 1, "vivaldi": 2, "msedge": 3}
    for channel, path in sorted(browsers[:6], key=lambda item: order.get(item[0], 9)):
        add(f"{channel} → {path}", {"executable_path": str(path)})
    for channel, path in sorted(browsers[:4], key=lambda item: order.get(item[0], 9)):
        if channel in ("chrome", "msedge"):
            add(f"kanal {channel}", {"channel": channel})
    # 3) sebagian mesin gagal karena driver GPU/antivirus
    if browsers:
        channel, path = browsers[0]
        add(f"{channel} tanpa GPU",
            {"executable_path": str(path),
             "args": list(BASE_ARGS) + ["--disable-gpu", "--disable-software-rasterizer"]})
    add("Chromium bawaan Playwright", {})
    return modes


class _StateContext:
    """Bungkus context non-persistent: simpan cookie saat ditutup.

    Cadangan kalau profil permanen tidak bisa dibuka (mis. terkunci browser
    lain). Login tetap tersimpan sebagai storage_state JSON, jadi lain kali
    tidak perlu login ulang.
    """

    def __init__(self, context: Any, browser: Any, state_file: Path) -> None:
        object.__setattr__(self, "_ctx", context)
        object.__setattr__(self, "_browser", browser)
        object.__setattr__(self, "_state_file", state_file)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_ctx"), name)

    def close(self) -> None:
        ctx = object.__getattribute__(self, "_ctx")
        brw = object.__getattribute__(self, "_browser")
        state_file = object.__getattribute__(self, "_state_file")
        try:
            ctx.storage_state(path=str(state_file))
            _state["storage_state"] = str(state_file)
        except Exception:  # noqa: BLE001
            pass
        for item in (ctx, brw):
            try:
                item.close()
            except Exception:  # noqa: BLE001
                pass


def _state_file() -> Path:
    ensure_dirs()
    return PROFILE_DIR / "state.json"


def _launch_with_state(playwright: Any, mode: tuple[str, dict[str, Any]],
                       headless: bool) -> Any:
    """Buka browser biasa (tanpa profil tetap) lalu muat cookie tersimpan."""
    _label, extra = mode
    kwargs: dict[str, Any] = {"headless": headless, "args": list(BASE_ARGS), "timeout": 25000}
    kwargs.update(extra)
    browser = playwright.chromium.launch(**kwargs)
    state = _state_file()
    context_opts: dict[str, Any] = {"viewport": None}
    if state.exists():
        try:
            context_opts["storage_state"] = str(state)
        except Exception:  # noqa: BLE001
            pass
    try:
        context = browser.new_context(**context_opts)
    except Exception:  # noqa: BLE001
        context = browser.new_context()
    return _StateContext(context, browser, state)


def _launch(playwright: Any, headless: bool = False) -> Any:
    """Buka browser dengan profil tetap supaya login TikTok tidak hilang.

    Urutan percobaan (semua dicatat; kalau semuanya gagal, pesan error memuat
    penyebab SEBENARNYA dari browser yang dipakai — bukan pesan "Executable
    doesn't exist" dari Chromium bawaan yang belum diunduh):

      1. browser terpasang (Edge/Chrome/Brave) + profil yang ada
      2. kanal resmi Playwright (msedge/chrome)
      3. chromium bawaan Playwright
      4. ulangi 1-3 dengan profil BARU (profil lama terkunci/rusak)
      5. cadangan terakhir: browser biasa + cookie tersimpan (storage_state)

    Kalau percobaan 4 berhasil, profil baru itu diingat supaya login berikutnya
    memakai folder yang sama.
    """
    ensure_dirs()
    profile = _active_profile()
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:
        profile = PROFILE_DIR
        profile.mkdir(parents=True, exist_ok=True)

    modes = _launch_modes()
    failures: list[tuple[str, str]] = []

    # Profil yang sedang dipegang browser lain tidak akan bisa dibuka sama
    # sekali ("Target page, context or browser has been closed") — langsung
    # pakai profil baru supaya tidak menunggu 25 detik x jumlah cara.
    locked = _profile_in_use(profile) or bool(_profile_locks(profile))
    if locked:
        _say(
            "Profil login TikTok sedang dipakai jendela browser lain — memakai profil baru "
            "(login lama tetap aman di folder lama)."
        )

    def persistent(target: Path, mode: tuple[str, dict[str, Any]]) -> Any:
        label, extra = mode
        options: dict[str, Any] = dict(
            user_data_dir=str(target),
            headless=headless,
            viewport=None,
            args=list(BASE_ARGS),
            ignore_default_args=["--enable-automation"],
            timeout=25000,
        )
        options.update(extra)
        del label
        return playwright.chromium.launch_persistent_context(**options)

    # --- 1-3: profil yang sudah ada (login lama tetap terpakai)
    for mode in ([] if locked else modes):
        try:
            context = persistent(profile, mode)
            _state["last_mode"] = mode[0]
            set_browser_pick(mode[0], mode[1])
            return context
        except Exception as exc:  # noqa: BLE001
            failures.append((mode[0], _short_error(exc)))

    # --- 4: profil baru. Sering menyembuhkan "Process failed to launch"
    #        karena profil terkunci / rusak setelah pindah drive.
    fresh = profile.parent / f"{profile.name}_baru"
    for mode in modes[:4]:
        try:
            context = persistent(fresh, mode)
            _set_active_profile(fresh)
            _state["last_mode"] = mode[0] + " (profil baru)"
            set_browser_pick(mode[0], mode[1])
            _say(f"Profil browser lama tidak bisa dipakai — memakai profil baru: {fresh.name}")
            return context
        except Exception as exc:  # noqa: BLE001
            failures.append((mode[0] + " + profil baru", _short_error(exc)))

    # --- 5: tanpa profil permanen, login disimpan sebagai cookie JSON
    for mode in modes[:4]:
        try:
            context = _launch_with_state(playwright, mode, headless)
            _state["last_mode"] = mode[0] + " (tanpa profil)"
            set_browser_pick(mode[0], mode[1])
            _say("Memakai mode cadangan: browser biasa + cookie tersimpan.")
            return context
        except Exception as exc:  # noqa: BLE001
            failures.append((mode[0] + " tanpa profil", _short_error(exc)))

    # --- semuanya gagal: laporkan penyebab sebenarnya + saran yang tepat
    _write_browser_log(
        [f"profil aktif : {profile}", f"headless     : {headless}", "percobaan:"]
        + [f"  - {label}: {msg}" for label, msg in failures]
    )
    _state["browser_errors"] = [{"mode": label, "error": msg} for label, msg in failures]
    primary = failures[0][1] if failures else "tidak diketahui"
    # jangan salahkan profil kalau profil memang sudah dilewati (terkunci)
    hint = _browser_hint(failures, profile, profile_skipped=locked)
    raise TikTokError(
        f"Browser tidak bisa dibuka. {hint} "
        f"Penyebab dari {failures[0][0] if failures else 'browser'}: {primary} "
        f"(detail lengkap: {BROWSER_LOG})"
    )


def _browser_hint(failures: list[tuple[str, str]], profile: Path,
                  profile_skipped: bool = False) -> str:
    """Saran yang cocok dengan pesan error yang benar-benar muncul."""
    joined = " ".join(msg.lower() for _label, msg in failures)
    if not profile_skipped and (_profile_in_use(profile) or _profile_locks(profile)):
        return (
            "Profil browser Clipper sedang dipakai proses browser lain. "
            "Tutup semua jendela Edge/Chrome yang terbuka (atau restart komputer), lalu coba lagi."
        )
    if "target page, context or browser has been closed" in joined:
        return (
            "Browser langsung menutup saat dibuka — khas Edge yang diblokir kebijakan "
            "kantor/sekolah atau profilnya rusak. Paling cepat: pakai Chrome. "
            "Di halaman TikTok klik \"Tes buka browser\", lalu klik cara yang bertanda ✓ "
            "supaya Clipper selalu memakai browser itu."
        )
    if "process failed to launch" in joined:
        return (
            "Browser menolak dijalankan — biasanya kebijakan Windows, antivirus, atau profil rusak. "
            "Coba klik \"Unduh browser Clipper\" (Chromium sendiri, tidak kena kebijakan Edge), "
            "atau matikan antivirus sementara."
        )
    if "executable doesn't exist" in joined or "is not found at" in joined:
        return (
            "File browser yang dicari tidak ada. Klik \"Unduh browser\" di halaman TikTok "
            "(memakai Chromium bawaan Clipper, sekali unduh ±130 MB)."
        )
    if "cannot find" in joined or "winerror" in joined or "access is denied" in joined:
        return "Akses file browser ditolak (antivirus/izin folder). Coba jalankan run.bat sebagai administrator."
    if "protocol" in joined or "version" in joined:
        return (
            "Versi browser dan Playwright tidak cocok. Klik \"Pasang dukungan TikTok\" sekali lagi "
            "untuk memperbarui Playwright, atau klik \"Unduh browser\"."
        )
    return (
        "Coba klik \"Unduh browser\" di halaman TikTok (memakai Chromium bawaan Clipper, "
        "tidak bergantung pada Edge/Chrome)."
    )


def diagnose_browser(headless: bool = True) -> dict[str, Any]:
    """Tes semua cara buka browser dan kembalikan hasil per cara (untuk UI/doctor).

    Read-only: tidak menyentuh profil TikTok asli. Setiap percobaan memakai
    folder sementara yang dihapus lagi setelah tes.
    """
    result: dict[str, Any] = {
        "ok": False,
        "profile": str(_active_profile()),
        "profile_in_use": False,
        "results": [],
        "errors": [],
        "log": str(BROWSER_LOG),
    }
    if not _ensure_importable()[0]:
        result["fatal"] = f"Playwright tidak bisa diimpor: {import_error()}"
        _write_browser_log([result["fatal"]])
        return result

    profile = _active_profile()
    result["profile_in_use"] = _profile_in_use(profile)
    result["profile_locks"] = _profile_locks(profile)

    from playwright.sync_api import sync_playwright

    tmp = DATA_DIR / "tiktok_profile_tes"
    try:
        with sync_playwright() as pw:
            for label, extra in _launch_modes():
                entry: dict[str, Any] = {"mode": label, "ok": False, "error": ""}
                try:
                    kwargs = {"user_data_dir": str(tmp), "headless": headless,
                              "args": list(BASE_ARGS)}
                    kwargs.update(extra)
                    context = pw.chromium.launch_persistent_context(**kwargs)
                    try:
                        page = context.new_page()
                        page.goto("about:blank", timeout=15000)
                        title = page.title()
                        page.close()
                        entry["ok"] = True
                        entry["title"] = title
                    finally:
                        try:
                            context.close()
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as exc:  # noqa: BLE001
                    entry["error"] = _short_error(exc)
                result["results"].append(entry)
                if entry["ok"]:
                    result["ok"] = True
    except Exception as exc:  # noqa: BLE001
        result["fatal"] = _short_error(exc)
    finally:
        try:
            from .fsutil import safe_rmtree

            safe_rmtree(tmp)
        except Exception:  # noqa: BLE001
            pass

    result["errors"] = [f"{e['mode']}: {e['error']}" for e in result["results"] if not e["ok"]]
    if not result["ok"]:
        failures = [(e["mode"], e["error"]) for e in result["results"] if not e["ok"]]
        result["hint"] = _browser_hint(failures, profile)
    _write_browser_log(
        [f"tes browser (headless={headless}) — profil aktif: {profile}",
         f"sesi: id={session_info()['session_id']} user='{session_info()['user']}' "
         f"service={session_info()['service_flag']} interaktif={session_info()['interactive']}",
         f"profil dipakai proses lain: {result['profile_in_use']}"]
        + [f"  [{'OK ' if e['ok'] else 'X  '}] {e['mode']}{'' if e['ok'] else ': ' + e['error']}"
           for e in result["results"]]
    )
    return result


def _page(context: Any) -> Any:
    pages = [p for p in context.pages if not p.is_closed()]
    page = pages[0] if pages else context.new_page()
    try:
        page.bring_to_front()
    except Exception:
        pass
    return page


def _has_session(page: Any) -> bool:
    """Sudah login? Cek cookie sesi TikTok (beberapa nama versi web)."""
    try:
        return _looks_logged_in(page.context)
    except Exception:  # noqa: BLE001
        return False


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
# Cookie yang menandakan sesi TikTok sudah masuk (nama berbeda antar versi web).
LOGIN_COOKIES = (
    "sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt", "uid_tt_ss",
    "passport_csrf_token", "tt_csrf_token", "s_v_web_id",
)
# Minimal satu dari ini harus ada; `s_v_web_id` saja tidak cukup (itu dipasang
# bahkan untuk pengunjung yang belum login).
STRONG_COOKIES = ("sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt")


def login_cookies(context: Any) -> list[str]:
    """Nama cookie login yang sudah ada di context (kosong = belum login)."""
    try:
        cookies = context.cookies("https://www.tiktok.com")
    except Exception:  # noqa: BLE001
        return []
    return sorted({
        str(c.get("name")) for c in cookies
        if c.get("name") in LOGIN_COOKIES and c.get("value")
    })


def _looks_logged_in(context: Any) -> bool:
    return any(name in STRONG_COOKIES for name in login_cookies(context))


def login_state() -> dict[str, Any]:
    """Status jendela login saat ini (dipakai UI untuk menampilkan progres)."""
    with _state_lock:
        state = dict(_state)
    started = float(state.get("login_started") or 0)
    running = bool(state.get("login_window"))
    thread = state.get("login_thread")
    if running and thread is not None and not getattr(thread, "is_alive", lambda: True)():
        running = False
    return {
        "running": running,
        "step": state.get("login_step") or "",
        "url": state.get("login_url") or "",
        "elapsed": round(time.time() - started, 1) if started and running else 0.0,
        "wait": float(state.get("login_wait") or 0),
        "shot": str(SCREENSHOT_FILE) if SCREENSHOT_FILE.exists() else "",
        "cookies": state.get("login_cookies") or [],
        "message": state.get("message") or "",
        "version": _state.get("version") or "",
    }


def open_login_window(wait_seconds: float = 900) -> dict[str, Any]:
    """Buka jendela browser TikTok supaya user bisa login sekali."""
    if not _ensure_importable()[0]:
        raise TikTokError(
            "Playwright belum bisa dipakai. Buka halaman TikTok di Clipper → "
            "klik \"Pasang dukungan TikTok\" (sekali saja), lalu \"Periksa ulang\"."
        )
    info = session_info()
    _write_browser_log([
        f"login TikTok diminta — profil: {_active_profile()}",
        f"sesi: id={info['session_id']} nama='{info['session_name']}' user='{info['user']}' "
        f"service={info['service_flag']} interaktif={info['interactive']}",
        f"pid={os.getpid()} versi={_state.get('version') or '?'}",
    ])
    if not info["interactive"]:
        message = (
            "Jendela login TikTok butuh desktop, tapi Clipper sedang jalan sebagai Windows service. "
            "Cara paling cepat: buka Clipper lewat run.bat (service boleh tetap jalan, tapi login "
            "dilakukan dari jendela run.bat), login di sana, lalu tutup. Setelah itu service bisa "
            "memakai login yang sama untuk upload."
        )
        _write_browser_log([f"DITOLAK: {message}"])
        _say(message)
        raise TikTokError(message)

    with _state_lock:
        thread = _state.get("login_thread")
        if _state["login_window"] and thread is not None and getattr(thread, "is_alive", lambda: False)():
            _write_browser_log(["login: diabaikan — jendela login sebelumnya masih terbuka"])
            return status()
        _state["login_window"] = True
        _state["login_started"] = time.time()
        _state["login_wait"] = float(wait_seconds)
        _state["login_step"] = "menyiapkan browser"
        _state["login_url"] = ""
        _state["login_cookies"] = []
    _say("Membuka jendela TikTok — login di sana, jendela menutup sendiri setelah berhasil.")

    def set_step(step: str, url: str = "") -> None:
        with _state_lock:
            _state["login_step"] = step
            if url:
                _state["login_url"] = url

    def worker() -> None:
        log: list[str] = []
        shots = 0
        last_url = ""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                set_step("membuka browser")
                context = _launch(pw, headless=False)
                try:
                    page = _page(context)
                    mode = _state.get("last_mode") or "?"
                    log.append(f"login: browser terbuka ({mode})")
                    _write_browser_log(log)
                    set_step("membuka halaman login")
                    try:
                        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)
                    except Exception as exc:  # noqa: BLE001
                        log.append(f"login: gagal membuka halaman login — {_short_error(exc)}")
                        _write_browser_log(log)
                    deadline = time.time() + float(wait_seconds)
                    next_shot = time.time() + 6
                    found: list[str] = []
                    while time.time() < deadline:
                        try:
                            url = page.url or ""
                        except Exception:  # noqa: BLE001
                            url = ""
                        if url and url != last_url:
                            last_url = url
                            set_step(f"di halaman TikTok ({url[:90]})", url)
                            log.append(f"login: URL -> {url[:120]}")
                            _write_browser_log(log)
                            log = []
                        found = login_cookies(context)
                        with _state_lock:
                            _state["login_cookies"] = found
                        if any(name in STRONG_COOKIES for name in found):
                            _mark_logged_in(True)
                            _screenshot(page)
                            _write_browser_log([
                                f"login BERHASIL — cookie: {', '.join(found)}",
                                f"login: URL akhir {last_url[:120]}",
                            ])
                            _say(f"Login TikTok berhasil disimpan (cookie: {', '.join(found)}).")
                            set_step("berhasil")
                            time.sleep(2)
                            break
                        if page.is_closed():
                            _write_browser_log(["login: jendela ditutup sebelum selesai"])
                            _say("Jendela login ditutup sebelum login selesai.")
                            break
                        # tangkapan layar berkala: bukti apa yang tampil di jendela
                        if time.time() >= next_shot and shots < 8:
                            shots += 1
                            next_shot = time.time() + 25
                            try:
                                _screenshot(page)
                                _write_browser_log([
                                    f"login: tangkapan layar #{shots} — URL {last_url[:100]}",
                                    f"login: cookie terlihat: {', '.join(found) or '(belum ada)'}",
                                ])
                            except Exception:  # noqa: BLE001
                                pass
                        time.sleep(2)
                    else:
                        _write_browser_log([
                            "login: WAKTU HABIS — login tidak terdeteksi",
                            f"login: URL terakhir {last_url[:120]}",
                            f"login: cookie terlihat: {', '.join(found) or '(tidak ada)'}",
                        ])
                        _say(
                            "Waktu login habis. Jendela ditutup. Kalau kamu sudah login di jendela itu, "
                            "coba tombol \"Buka Chrome manual\" di halaman TikTok (login di Chrome biasa)."
                        )
                finally:
                    try:
                        context.close()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            _write_browser_log([f"login GAGAL: {_short_error(exc)}"])
            message = " ".join(str(exc).split())
            if len(message) > 420:
                message = message[:420] + "…"
            _say(f"Gagal membuka jendela TikTok: {message}")
            _say("Klik tombol \"Tes buka browser\" di halaman TikTok — hasilnya menunjukkan "
                 "cara mana yang gagal dan kenapa.")
        finally:
            with _state_lock:
                _state["login_window"] = False
                _state["login_thread"] = None
                if _state.get("login_step") != "berhasil":
                    _state["login_step"] = "selesai"

    thread = threading.Thread(target=worker, daemon=True, name="tiktok-login")
    with _state_lock:
        _state["login_thread"] = thread
    thread.start()
    return status()


# --------------------------------------------------------------------------- #
# jalur cadangan: Chrome biasa (tanpa automasi Playwright)
# --------------------------------------------------------------------------- #
def _chrome_exe() -> Path | None:
    for channel, path in _browser_paths():
        if channel == "chrome":
            return path
    for channel, path in _browser_paths():
        if channel in ("msedge", "brave", "vivaldi"):
            return path
    return None


def open_manual_browser(url: str = LOGIN_URL) -> dict[str, Any]:
    """Buka Chrome/Edge biasa dengan profil Clipper (tanpa Playwright sama sekali).

    Tidak ada flag automasi, tidak ada CDP — jadi kalau jendela login versi
    Playwright diblokir/tertutup sendiri, cara ini hampir selalu bisa. Setelah
    login selesai, tutup jendelanya lalu klik "Cek login" di Clipper.
    """
    if not session_info()["interactive"]:
        raise TikTokError(
            "Tidak ada desktop (Clipper jalan sebagai service), jadi jendela browser "
            "tidak bisa dibuka. Jalankan run.bat lalu login dari sana."
        )
    exe = _chrome_exe()
    if exe is None:
        raise TikTokError(
            "Chrome/Edge tidak ditemukan. Klik \"Unduh browser Clipper\" dulu, lalu coba lagi."
        )
    ensure_dirs()
    profile = _active_profile()
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(exe),
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        url,
    ]
    try:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        proc = subprocess.Popen(cmd, close_fds=True, creationflags=flags)  # noqa: S603
    except Exception as exc:  # noqa: BLE001
        message = f"Gagal membuka {exe.name}: {_short_error(exc)}"
        _write_browser_log([f"manual: {message}"])
        raise TikTokError(message) from exc
    _write_browser_log([
        f"manual: membuka {exe} dengan profil {profile} (pid {proc.pid})",
        "manual: login di jendela itu, tutup jendelanya, lalu klik 'Cek login'",
    ])
    _say(
        f"Jendela {exe.name} dibuka dengan profil Clipper. Login di sana, TUTUP jendelanya, "
        "lalu klik \"Cek login\"."
    )
    return {"ok": True, "exe": str(exe), "pid": proc.pid, "profile": str(profile)}


def check_login_now() -> dict[str, Any]:
    """Cek apakah login sudah tersimpan (tanpa membuka jendela terlihat)."""
    if not _ensure_importable()[0]:
        raise TikTokError("Playwright belum bisa dipakai — klik \"Pasang dukungan TikTok\" dulu.")
    profile = _active_profile()
    if _profile_in_use(profile) or _profile_locks(profile):
        return {
            "ok": False,
            "logged_in": False,
            "reason": "Profil masih dipakai jendela browser. Tutup dulu jendela Chrome/Edge "
                      "yang dipakai login, lalu klik \"Cek login\" lagi.",
            "cookies": [],
        }
    from playwright.sync_api import sync_playwright

    cookies: list[str] = []
    detail = ""
    try:
        with sync_playwright() as pw:
            context = _launch(pw, headless=True)
            try:
                cookies = login_cookies(context)
            finally:
                try:
                    context.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        detail = _short_error(exc)
    ok = any(name in STRONG_COOKIES for name in cookies)
    if ok:
        _mark_logged_in(True)
    _write_browser_log([
        f"check-login: cookie {', '.join(cookies) or '(tidak ada)'} -> {'login OK' if ok else 'belum login'}"
        + (f" · {detail}" if detail else ""),
    ])
    return {
        "ok": ok,
        "logged_in": ok,
        "cookies": cookies,
        "reason": "" if ok else (detail or "Belum ada cookie login di profil Clipper."),
        "status": status(),
    }


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

    info = session_info()
    if not info["interactive"] and not headless:
        # Service tidak punya desktop: jendela tidak akan muncul dan browser
        # langsung tertutup. Upload TikTok Studio tetap jalan tanpa jendela.
        headless = True
        _write_browser_log(["upload: tidak ada desktop -> memakai mode headless"])
        progress(0.02, "Mode service: upload TikTok berjalan tanpa jendela browser…")

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
