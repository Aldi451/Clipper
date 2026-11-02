"""Jalankan Clipper sebagai Windows service (tanpa jendela cmd).

Service ini muncul di `services.msc` sebagai **Clipper**, start otomatis saat
Windows menyala, dan berjalan di latar belakang — jadi tidak ada jendela Command
Prompt yang harus dibiarkan terbuka.

Butuh paket `pywin32` (hanya di Windows). Kalau tidak bisa dipakai, tersedia
alternatif autostart lewat Task Scheduler yang juga tanpa jendela (lihat
`install_autostart_task()`).

Semua fungsi di sini aman di-import di Linux/macOS: modul win32 hanya dimuat
saat benar-benar dipakai.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CLIPPER_DATA_DIR") or (ROOT / "data"))
SERVICE_FILE = DATA_DIR / "service.json"
LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "clipper-service.log"

SERVICE_NAME = "Clipper"
DISPLAY_NAME = "Clipper - Auto Clip to Shorts"
DESCRIPTION = (
    "Clipper berjalan di latar belakang (tanpa jendela cmd). "
    "Buka http://localhost:8420 di browser untuk memakai aplikasinya."
)
TASK_NAME = "Clipper (autostart)"
DEFAULT_PORT = int(os.environ.get("CLIPPER_PORT") or 8420)
DEFAULT_HOST = os.environ.get("CLIPPER_HOST") or "127.0.0.1"


class ServiceError(RuntimeError):
    """Kesalahan yang pesannya sudah ramah untuk pengguna."""


# --------------------------------------------------------------------------- #
# pywin32 (dimuat hanya di Windows)
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - hanya jalan di Windows
    import servicemanager  # type: ignore
    import win32event  # type: ignore
    import win32service  # type: ignore
    import win32serviceutil  # type: ignore

    HAS_PYWIN32 = True
except Exception:  # noqa: BLE001 - di Linux/macOS memang tidak ada
    servicemanager = None  # type: ignore
    win32event = None  # type: ignore
    win32service = None  # type: ignore
    win32serviceutil = None  # type: ignore
    HAS_PYWIN32 = False


def is_windows() -> bool:
    return sys.platform.startswith("win")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ServiceError(message)


def _require_pywin32() -> None:
    _require(is_windows(), "Fitur service hanya tersedia di Windows.")
    _require(
        HAS_PYWIN32,
        "Paket pywin32 belum terpasang. Jalankan:\n"
        "  .venv\\Scripts\\python -m pip install pywin32",
    )


# --------------------------------------------------------------------------- #
# pengaturan service (host/port disimpan, dipakai ulang saat service start)
# --------------------------------------------------------------------------- #
def service_settings() -> dict[str, Any]:
    data: dict[str, Any] = {"host": DEFAULT_HOST, "port": DEFAULT_PORT}
    try:
        if SERVICE_FILE.exists():
            saved = json.loads(SERVICE_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data["host"] = str(saved.get("host") or data["host"])
                data["port"] = int(saved.get("port") or data["port"])
    except Exception:  # noqa: BLE001 - file rusak: pakai default
        pass
    return data


def save_service_settings(host: str | None = None, port: int | None = None) -> dict[str, Any]:
    data = service_settings()
    if host:
        data["host"] = host
    if port:
        data["port"] = int(port)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SERVICE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def service_url(settings: dict[str, Any] | None = None) -> str:
    data = settings or service_settings()
    host = data.get("host") or "127.0.0.1"
    label = "localhost" if host in ("0.0.0.0", "::", "") else host
    return f"http://{label}:{data.get('port') or DEFAULT_PORT}"


# --------------------------------------------------------------------------- #
# logging ke file (service tidak punya jendela, jadi error harus tercatat)
# --------------------------------------------------------------------------- #
def configure_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    already = any(
        isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")) == LOG_FILE
        for h in root.handlers
    )
    if not already:
        handler = RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    logging.getLogger("clipper").info("Log service aktif: %s", LOG_FILE)
    return LOG_FILE


def read_log_tail(lines: int = 40) -> str:
    if not LOG_FILE.exists():
        return ""
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(content[-lines:])


# --------------------------------------------------------------------------- #
# inti server (dipakai service maupun mode autostart)
# --------------------------------------------------------------------------- #
def serve_forever(stop_event: "threading.Event | None" = None,
                  should_exit: Any = None) -> None:
    """Jalankan uvicorn sampai stop_event diset (atau proses dimatikan)."""
    os.chdir(ROOT)  # service Windows start di C:\Windows\System32
    settings = service_settings()
    configure_logging()
    log = logging.getLogger("clipper")

    from app.config import ensure_dirs
    from app.server import create_app

    import uvicorn

    ensure_dirs()
    app = create_app()
    config = uvicorn.Config(
        app,
        host=settings["host"],
        port=int(settings["port"]),
        log_config=None,      # pakai logging root (file), bukan console
        access_log=False,     # jangan banjiri log dengan tiap request
        log_level="info",
    )
    server = uvicorn.Server(config)
    if should_exit is not None:
        try:
            should_exit.append(server)
        except Exception:  # noqa: BLE001
            pass
    log.info("Clipper service siap di %s", service_url(settings))
    try:
        server.run()
    except Exception:  # noqa: BLE001
        log.exception("Server berhenti karena error")


# --------------------------------------------------------------------------- #
# kelas service (hanya didefinisikan kalau pywin32 ada)
# --------------------------------------------------------------------------- #
if HAS_PYWIN32:  # pragma: no cover - Windows only

    class ClipperService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = DISPLAY_NAME
        _svc_description_ = DESCRIPTION

        def __init__(self, args):  # noqa: D401
            super().__init__(args)
            self._stop = win32event.CreateEvent(None, 0, 0, None)
            self._servers: list[Any] = []

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            for server in self._servers:
                try:
                    server.should_exit = True
                except Exception:  # noqa: BLE001
                    pass
            win32event.SetEvent(self._stop)

        def SvcDoRun(self) -> None:
            try:
                servicemanager.LogInfoMsg(f"{SERVICE_NAME}: mulai")
            except Exception:  # noqa: BLE001
                pass
            logging.getLogger("clipper").info("Service %s dimulai", SERVICE_NAME)
            worker = threading.Thread(
                target=serve_forever,
                kwargs={"should_exit": self._servers},
                name="clipper-server",
                daemon=True,
            )
            worker.start()
            while True:
                rc = win32event.WaitForSingleObject(self._stop, 2000)
                if rc == win32event.WAIT_OBJECT_0:
                    break
                if not worker.is_alive():
                    logging.getLogger("clipper").error(
                        "Server berhenti sendiri (cek port %s atau file log)",
                        service_settings().get("port"),
                    )
                    break
            logging.getLogger("clipper").info("Service %s berhenti", SERVICE_NAME)

else:  # pragma: no cover
    ClipperService = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# perintah service
# --------------------------------------------------------------------------- #
def _service_command() -> str:
    """Baris perintah yang dijalankan SCM."""
    exe = Path(sys.executable)
    # python.exe (bukan pythonw.exe) aman: proses service tidak punya console
    return f'"{exe}" "{ROOT / "run.py"}" --run-service'


def is_installed() -> bool:
    if not (is_windows() and HAS_PYWIN32):
        return False
    try:
        handle = win32serviceutil.QueryServiceStatus(SERVICE_NAME)
        return bool(handle)
    except Exception:  # noqa: BLE001 - 1060 = service tidak ada
        return False


def service_state() -> str:
    """'running' | 'stopped' | 'not-installed' | 'unknown'."""
    if not (is_windows() and HAS_PYWIN32):
        return "not-installed"
    try:
        status = win32serviceutil.QueryServiceStatus(SERVICE_NAME)[1]
    except Exception:  # noqa: BLE001
        return "not-installed"
    if status == win32service.SERVICE_RUNNING:
        return "running"
    if status in (win32service.SERVICE_STOPPED, win32service.SERVICE_STOP_PENDING):
        return "stopped"
    return "unknown"


def install_service(host: str | None = None, port: int | None = None,
                    autostart: bool = True) -> dict[str, Any]:
    """Pasang service Clipper (butuh cmd/PowerShell sebagai Administrator)."""
    _require_pywin32()
    settings = save_service_settings(host=host, port=port)
    start_type = win32service.SERVICE_AUTO_START if autostart else win32service.SERVICE_DEMAND_START
    was_installed = False
    try:
        win32serviceutil.QueryServiceStatus(SERVICE_NAME)
        was_installed = True
    except Exception:  # noqa: BLE001
        was_installed = False

    if was_installed:
        # service lama: hentikan dulu, lalu hapus supaya path/argumen ikut ter-update
        try:
            if service_state() == "running":
                win32serviceutil.StopService(SERVICE_NAME)
        except Exception:  # noqa: BLE001
            pass
        try:
            win32serviceutil.RemoveService(SERVICE_NAME)
        except Exception:  # noqa: BLE001
            pass

    try:
        win32serviceutil.InstallService(
            "app.winservice.ClipperService",
            SERVICE_NAME,
            DISPLAY_NAME,
            startType=start_type,
            exeName=_service_command(),
            description=DESCRIPTION,
        )
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(
            "Gagal memasang service. Pastikan jendela ini dijalankan sebagai Administrator.\n"
            f"Detail: {exc}"
        ) from exc
    _configure_recovery()
    return {"installed": True, "state": service_state(), "settings": settings,
            "replaced": was_installed}


def _configure_recovery() -> None:
    """Minta Windows menjalankan ulang service kalau berhenti karena error.

    Contoh kasus: port 8420 sempat dipakai program lain saat boot, atau ffmpeg
    kehabisan memori. Tanpa ini service berhenti dan tidak pernah hidup lagi.
    """
    try:
        subprocess.run(
            ["sc.exe", "failure", SERVICE_NAME,
             "reset=", "86400",
             "actions=", "restart/60000/restart/120000/restart/300000"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:  # noqa: BLE001 - bukan hal fatal
        pass


def remove_service() -> dict[str, Any]:
    _require_pywin32()
    if not is_installed():
        return {"removed": False, "reason": "service belum terpasang"}
    try:
        if service_state() == "running":
            win32serviceutil.StopService(SERVICE_NAME)
    except Exception:  # noqa: BLE001
        pass
    try:
        win32serviceutil.RemoveService(SERVICE_NAME)
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(
            "Gagal menghapus service (butuh Administrator).\n" f"Detail: {exc}"
        ) from exc
    return {"removed": True}


def start_service() -> dict[str, Any]:
    _require_pywin32()
    if not is_installed():
        raise ServiceError("Service belum terpasang. Jalankan dulu: python run.py --install-service")
    try:
        win32serviceutil.StartService(SERVICE_NAME)
    except Exception as exc:  # noqa: BLE001
        win32serviceutil.StartService  # noqa: B018 - hanya untuk keterbacaan
        raise ServiceError(f"Gagal memulai service: {exc}") from exc
    return {"started": True, "url": service_url()}


def stop_service() -> dict[str, Any]:
    _require_pywin32()
    if service_state() != "running":
        return {"stopped": False, "reason": "service tidak sedang berjalan"}
    try:
        win32serviceutil.StopService(SERVICE_NAME)
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"Gagal menghentikan service: {exc}") from exc
    return {"stopped": True}


START_TYPES = {"auto": "auto", "manual": "demand", "disabled": "disabled"}


def set_start_type(kind: str) -> dict[str, Any]:
    """Ubah tipe startup service: auto | manual | disabled."""
    key = (kind or "").lower()
    _require(key in START_TYPES, "Tipe startup harus: auto, manual, atau disabled.")
    _require(is_windows(), "Fitur service hanya tersedia di Windows.")
    if not is_installed():
        raise ServiceError("Service belum terpasang. Jalankan: python run.py --install-service")
    proc = subprocess.run(
        ["sc.exe", "config", SERVICE_NAME, "start=", START_TYPES[key]],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ServiceError(
            "Gagal mengubah tipe startup (butuh Administrator).\n"
            f"Detail: {(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return {"start_type": key}


def run_service() -> None:
    """Dipanggil SCM: sambungkan proses ini ke Service Control Manager."""
    _require_pywin32()
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(ClipperService)
    servicemanager.StartServiceCtrlDispatcher()


# --------------------------------------------------------------------------- #
# alternatif tanpa pywin32: autostart lewat Task Scheduler (juga tanpa jendela)
# --------------------------------------------------------------------------- #
def _pythonw() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def install_autostart_task(port: int | None = None, host: str | None = None) -> dict[str, Any]:
    """Daftarkan Clipper start otomatis saat login tanpa jendela cmd.

    Memakai `pythonw.exe` + Task Scheduler, jadi tidak butuh Administrator.
    """
    _require(is_windows(), "Autostart Task Scheduler hanya untuk Windows.")
    save_service_settings(host=host, port=port)
    command = f'"{_pythonw()}" "{ROOT / "run.py"}"'
    args = [
        "schtasks", "/Create", "/F",
        "/TN", TASK_NAME,
        "/SC", "ONLOGON",
        "/TR", command,
        "/RL", "LIMITED",
    ]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ServiceError(
            "Gagal membuat autostart.\n" f"Detail: {(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return {"created": True, "task": TASK_NAME}


def remove_autostart_task() -> dict[str, Any]:
    _require(is_windows(), "Autostart Task Scheduler hanya untuk Windows.")
    proc = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
                          capture_output=True, text=True)
    return {"removed": proc.returncode == 0,
            "detail": (proc.stderr or proc.stdout).strip()[:200]}


# --------------------------------------------------------------------------- #
# shortcut desktop (buka Clipper tanpa mengetik URL)
# --------------------------------------------------------------------------- #
def desktop_dir() -> Path | None:
    if is_windows():
        profile = os.environ.get("USERPROFILE")
        if profile:
            return Path(profile) / "Desktop"
        return None
    home = Path.home()
    for name in ("Desktop", "OneDrive/Desktop", "桌面"):
        candidate = home / name
        if candidate.exists():
            return candidate
    return None


def create_desktop_shortcut() -> dict[str, Any]:
    """Buat file .url di Desktop yang langsung membuka Clipper di browser."""
    target = desktop_dir()
    if target is None:
        raise ServiceError("Folder Desktop tidak ditemukan.")
    target.mkdir(parents=True, exist_ok=True)
    path = target / "Clipper.url"
    path.write_text(f"[InternetShortcut]\nURL={service_url()}\n", encoding="utf-8")
    return {"path": str(path), "url": service_url()}
