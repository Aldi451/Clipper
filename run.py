"""Launcher Clipper.

Pemakaian:
    python run.py              # jalankan server + buka browser
    python run.py --doctor     # cek lingkungan (kenapa ga jalan?)
    python run.py --port 9000  # ganti port
    python run.py --no-browser # jangan buka browser

Kalau ada masalah, launcher ini sengaja dibuat "cerewet": dia memberi tahu apa yang
kurang dan perintah persis untuk memperbaikinya, bukan cuma traceback.
"""
from __future__ import annotations

import argparse
import errno
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

MIN_PYTHON = (3, 10)
LINE = "=" * 62


# --------------------------------------------------------------------------- #
# util tampilan
# --------------------------------------------------------------------------- #
def say(text: str = "") -> None:
    print(text, flush=True)


def head(title: str) -> None:
    say("")
    say(LINE)
    say(f"  {title}")
    say(LINE)


def ok(text: str) -> None:
    say(f"  [OK]  {text}")


def bad(text: str) -> None:
    say(f"  [!]   {text}")


def info(text: str) -> None:
    say(f"        {text}")


def venv_python() -> Path | None:
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


# --------------------------------------------------------------------------- #
# pemeriksaan
# --------------------------------------------------------------------------- #
REQUIRED_MODULES = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("imageio_ffmpeg", "imageio-ffmpeg"),
    ("yt_dlp", "yt-dlp"),
    ("multipart", "python-multipart"),
]


def check_python() -> bool:
    version = sys.version_info
    good = version >= MIN_PYTHON
    label = f"Python {version.major}.{version.minor}.{version.micro} ({sys.executable})"
    (ok if good else bad)(label + ("" if good else f" — butuh {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+"))
    return good


def check_modules() -> list[str]:
    missing: list[str] = []
    for module, package in REQUIRED_MODULES:
        try:
            __import__(module)
            ok(f"paket {package}")
        except Exception:
            bad(f"paket {package} BELUM terpasang")
            missing.append(package)
    # opsional
    for module, package, note in (
        ("faster_whisper", "faster-whisper", "transkripsi lokal"),
        ("googleapiclient", "google-api-python-client", "upload YouTube"),
    ):
        try:
            __import__(module)
            ok(f"paket {package} (opsional, {note})")
        except Exception:
            bad(f"paket {package} belum ada (opsional: {note})")
    return missing


def check_ffmpeg() -> bool:
    try:
        from app.config import ffmpeg_exe

        path = ffmpeg_exe()
        ok(f"ffmpeg: {path}")
        return True
    except Exception as exc:
        bad(f"ffmpeg tidak ditemukan: {exc}")
        info("Perbaiki: pip install imageio-ffmpeg")
        return False


def check_ytdlp_ffmpeg() -> bool:
    """yt-dlp hanya bisa merge video+audio kalau tahu lokasi ffmpeg."""
    try:
        from app.config import ffmpeg_exe, ffmpeg_for_ytdlp

        loc = ffmpeg_for_ytdlp()
        if not loc:
            bad("yt-dlp tidak diberi lokasi ffmpeg (merge video+audio akan gagal)")
            info("Perbaiki: pip install -U imageio-ffmpeg")
            return False
        import subprocess

        target = loc if os.path.isfile(loc) else os.path.join(loc, "ffmpeg")
        out = subprocess.run([target, "-version"], capture_output=True, text=True, timeout=20)
        line = (out.stdout or out.stderr or "").splitlines()[:1]
        ok(f"yt-dlp → ffmpeg: {loc}")
        if line:
            info(f"  {line[0][:70]}")
        return True
    except Exception as exc:
        bad(f"gagal memeriksa ffmpeg untuk yt-dlp: {exc}")
        info("Perbaiki: pip install -U imageio-ffmpeg")
        return False


def check_files() -> bool:
    good = True
    if not (ROOT / "web" / "index.html").exists():
        bad("folder web/ tidak ditemukan — jalankan Clipper dari folder project yang utuh")
        good = False
    else:
        ok("antarmuka web ditemukan (web/index.html)")
    data = ROOT / "data"
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok(f"folder data bisa ditulis: {data}")
    except OSError as exc:
        bad(f"folder data tidak bisa ditulis: {exc}")
        info("Pindahkan project ke folder lain, atau set CLIPPER_DATA_DIR ke folder yang bisa ditulis")
        good = False
    return good


def port_free(host: str, port: int) -> bool:
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((target, port))
            return True
        except OSError as exc:
            return exc.errno not in (errno.EADDRINUSE, errno.EACCES)


def pick_port(host: str, preferred: int) -> int | None:
    for candidate in range(preferred, preferred + 25):
        if port_free(host, candidate):
            return candidate
    return None


# --------------------------------------------------------------------------- #
# mode doctor
# --------------------------------------------------------------------------- #
def doctor(host: str, port: int) -> int:
    head("Clipper — pemeriksaan lingkungan")
    problems = 0
    problems += 0 if check_python() else 1
    say("")
    missing = check_modules()
    if missing:
        problems += 1
    say("")
    problems += 0 if check_ffmpeg() else 1
    problems += 0 if check_ytdlp_ffmpeg() else 1
    say("")
    problems += 0 if check_files() else 1
    say("")
    if port_free(host, port):
        ok(f"port {port} bebas")
    else:
        bad(f"port {port} sedang dipakai program lain")
        alt = pick_port(host, port + 1)
        if alt:
            info(f"Pakai port lain: python run.py --port {alt}")
        problems += 1

    say("")
    if not in_virtualenv() and venv_python() is not None:
        bad("kamu belum mengaktifkan virtualenv .venv")
        info("Windows : .venv\\Scripts\\activate")
        info("macOS   : source .venv/bin/activate")

    say("")
    if problems == 0:
        ok("Semua siap. Jalankan: python run.py")
        say("")
        return 0
    bad(f"{problems} masalah ditemukan.")
    if missing:
        info("Perbaiki dependensi dengan: pip install -r requirements.txt")
        info("Atau pakai skrip otomatis: run.bat (Windows) / ./run.sh (macOS/Linux)")
    say("")
    return 1


# --------------------------------------------------------------------------- #
# start server
# --------------------------------------------------------------------------- #
def open_browser_later(url: str, delay: float = 1.5) -> None:
    def worker() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


# --------------------------------------------------------------------------- #
# mode Windows service (tanpa jendela cmd)
# --------------------------------------------------------------------------- #
def _winservice():
    from app import winservice

    return winservice


def handle_service_command(args) -> int | None:
    """Tangani perintah service. None = bukan perintah service."""
    wants = any([
        args.install_service, args.remove_service, args.start_service, args.stop_service,
        args.service_status, args.service_log, args.run_service, args.autostart, args.no_autostart,
        args.shortcut, args.service_start_type,
    ])
    if not wants:
        return None

    ws = _winservice()
    try:
        if args.run_service:
            # dijalankan oleh Service Control Manager: tidak ada output ke layar
            ws.run_service()
            return 0

        if args.install_service:
            head("Memasang service Clipper")
            start_kind = args.service_start_type or ("manual" if args.no_autostart else "auto")
            result = ws.install_service(host=args.service_host, port=args.service_port,
                                        autostart=start_kind != "manual")
            if start_kind != "auto":
                ws.set_start_type(start_kind)
            ok(f"Tipe startup: {start_kind}")
            ok(f"Service '{ws.SERVICE_NAME}' terpasang"
               + (" (mengganti versi lama)" if result.get("replaced") else ""))
            hint = {
                "auto": "Mulai otomatis saat Windows menyala",
                "manual": "Tipe Manual: nyalakan sendiri lewat 'Start' (services.msc atau --start-service)",
                "disabled": "Tipe Disabled: tidak akan menyala sampai tipenya diubah",
            }.get(start_kind, "")
            info(f"{hint} · alamat: {ws.service_url()}")
            if start_kind == "disabled":
                info("Ubah tipenya dengan: python run.py --service-start-type auto")
            info("Buka dengan: services.msc → cari 'Clipper'")
            if ws.service_state() != "running":
                info("Mulai sekarang? Jalankan:  python run.py --start-service")
            return 0

        if args.remove_service:
            head("Menghapus service Clipper")
            result = ws.remove_service()
            if result.get("removed"):
                ok("Service dihapus.")
            else:
                info(result.get("reason") or "Service belum terpasang.")
            return 0

        if args.start_service:
            result = ws.start_service()
            ok(f"Service dimulai. Buka {result['url']} di browser.")
            return 0

        if args.stop_service:
            result = ws.stop_service()
            if result.get("stopped"):
                ok("Service dihentikan.")
            else:
                info(result.get("reason") or "Service tidak berjalan.")
            return 0

        if args.service_start_type and not args.install_service:
            result = ws.set_start_type(args.service_start_type)
            ok(f"Tipe startup diubah ke: {result['start_type']}")
            return 0

        if args.service_status:
            head("Status Clipper")
            state = ws.service_state()
            label = {"running": "berjalan", "stopped": "berhenti",
                     "not-installed": "belum terpasang", "unknown": "tidak diketahui"}[state]
            say(f"  Service : {ws.SERVICE_NAME} — {label}")
            if state != "not-installed":
                say(f"  Alamat  : {ws.service_url()}")
                say(f"  Log     : {ws.LOG_FILE}")
            say(f"  pywin32 : {'terpasang' if ws.HAS_PYWIN32 else 'belum terpasang'}")
            if state != "not-installed":
                proc = __import__("subprocess").run(
                    ["sc.exe", "qc", ws.SERVICE_NAME], capture_output=True, text=True)
                for line in (proc.stdout or "").splitlines():
                    if "START_TYPE" in line.upper():
                        say(f"  Startup : {line.split(':', 1)[-1].strip()}")
            return 0

        if args.service_log:
            head("Log service (40 baris terakhir)")
            tail = ws.read_log_tail(40)
            say(tail or "  (belum ada log — service mungkin belum pernah jalan)")
            say("")
            info(f"File: {ws.LOG_FILE}")
            return 0

        if args.autostart:
            head("Membuat autostart tanpa jendela")
            result = ws.install_autostart_task(host=args.service_host, port=args.service_port)
            ok(f"Terdaftar di Task Scheduler sebagai '{result['task']}'")
            info("Clipper akan jalan otomatis setiap kamu login (tanpa jendela cmd).")
            info("Pakai sekarang juga: jalankan lewat shortcut Clipper di Desktop.")
            return 0

        if args.no_autostart and not args.install_service:
            result = ws.remove_autostart_task()
            if result.get("removed"):
                ok("Autostart dihapus.")
            else:
                info(f"Tidak ada autostart terdaftar. {result.get('detail') or ''}".strip())
            return 0

        if args.shortcut:
            result = ws.create_desktop_shortcut()
            ok(f"Shortcut dibuat: {result['path']}")
            info(f"Klik dua kali untuk membuka {result['url']}")
            return 0
    except Exception as exc:  # noqa: BLE001 - pesan sudah ramah
        head("Gagal")
        bad(str(exc))
        if "pywin32" in str(exc):
            info("Perbaiki dengan:")
            info("  .venv\\Scripts\\python -m pip install pywin32")
        return 1
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clipper — auto clip video jadi Shorts, offline & gratis",
        epilog="Contoh: python run.py --port 9000 --no-browser",
    )
    parser.add_argument("--host", default=os.environ.get("CLIPPER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CLIPPER_PORT", "8420")))
    parser.add_argument("--no-browser", action="store_true", help="Jangan buka browser otomatis")
    parser.add_argument("--no-pause", action="store_true", help="Jangan tunggu Enter saat error (untuk skrip)")
    parser.add_argument("--doctor", action="store_true", help="Cek lingkungan lalu keluar")
    parser.add_argument("--reload", action="store_true", help="Mode development (auto reload)")

    # --- Windows service: jalan tanpa jendela cmd ---
    svc = parser.add_argument_group("Windows service (tanpa jendela cmd)")
    svc.add_argument("--install-service", action="store_true",
                     help="Pasang Clipper sebagai service Windows (butuh Administrator)")
    svc.add_argument("--remove-service", action="store_true", help="Hapus service Clipper")
    svc.add_argument("--start-service", action="store_true", help="Mulai service Clipper")
    svc.add_argument("--stop-service", action="store_true", help="Hentikan service Clipper")
    svc.add_argument("--service-status", action="store_true", help="Lihat status service")
    svc.add_argument("--service-log", action="store_true", help="Tampilkan log service")
    svc.add_argument("--service-host", default=None, help="Host untuk service (default 127.0.0.1)")
    svc.add_argument("--service-port", type=int, default=None, help="Port untuk service (default 8420)")
    svc.add_argument("--service-start-type", choices=["auto", "manual", "disabled"], default=None,
                     help="Tipe startup service: auto (boot) | manual | disabled")
    svc.add_argument("--autostart", action="store_true",
                     help="Alternatif tanpa pywin32: autostart lewat Task Scheduler")
    svc.add_argument("--no-autostart", action="store_true", help="Hapus autostart Task Scheduler")
    svc.add_argument("--shortcut", action="store_true", help="Buat shortcut Clipper di Desktop")
    svc.add_argument("--run-service", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    service_code = handle_service_command(args)
    if service_code is not None:
        return service_code

    if args.doctor:
        return doctor(args.host, args.port)

    # ---- preflight: python & paket ----
    if sys.version_info < MIN_PYTHON:
        head("Python terlalu lama")
        bad(f"Versi sekarang {sys.version_info.major}.{sys.version_info.minor}, butuh 3.10+")
        info("Unduh Python terbaru: https://www.python.org/downloads/")
        return 2

    try:
        import uvicorn  # noqa: F401
        import fastapi  # noqa: F401
    except ModuleNotFoundError as exc:
        head("Dependensi belum terpasang")
        bad(f"Modul '{exc.name}' tidak ditemukan.")
        say("")
        info("Jalankan salah satu perintah berikut, lalu coba lagi:")
        info("")
        info("  Windows  :  py -m pip install -r requirements.txt")
        info("  macOS/Linux: python3 -m pip install -r requirements.txt")
        info("")
        info("Lebih mudah: pakai skrip otomatis")
        info("  Windows  :  run.bat")
        info("  macOS/Linux:  ./run.sh")
        info("")
        info("Kalau kamu memakai virtualenv, aktifkan dulu:")
        info("  Windows  :  .venv\\Scripts\\activate")
        info("  macOS/Linux:  source .venv/bin/activate")
        return 2
    except Exception as exc:  # noqa: BLE001 - rusak/tidak lengkap
        head("Gagal memuat dependensi")
        bad(str(exc))
        info("Coba install ulang: pip install -r requirements.txt")
        return 2

    # ---- preflight: aset & folder data ----
    try:
        from app.config import ensure_dirs

        ensure_dirs()
    except Exception as exc:  # noqa: BLE001
        head("Gagal menyiapkan folder data")
        bad(str(exc))
        info("Set CLIPPER_DATA_DIR ke folder yang bisa ditulis, contoh:")
        info("  Windows  :  set CLIPPER_DATA_DIR=C:\\clipper-data")
        info("  macOS/Linux:  CLIPPER_DATA_DIR=~/clipper-data python run.py")
        return 2

    if not (ROOT / "web" / "index.html").exists():
        head("File antarmuka tidak ditemukan")
        bad(f"web/index.html tidak ada di {ROOT}")
        info("Pastikan semua file project tersalin (git clone ulang kalau perlu).")
        return 2

    # ---- port ----
    if not port_free(args.host, args.port):
        alt = pick_port(args.host, args.port + 1)
        head("Port sudah dipakai")
        bad(f"Port {args.port} dipakai program lain (mungkin Clipper lain sedang jalan).")
        if alt:
            info(f"Memakai port {alt} sebagai gantinya.")
            args.port = alt
        else:
            info("Semua port percobaan terpakai. Hentikan program lain atau pakai --port lain.")
            return 3

    # ---- start ----
    from app.server import create_app

    app = create_app()
    host_label = "localhost" if args.host in ("0.0.0.0", "::", "") else args.host
    url = f"http://{host_label}:{args.port}"

    head("Clipper siap")
    say(f"  Buka di browser : {url}")
    say(f"  Data lokal      : {(ROOT / 'data')}")
    say(f"  Python          : {sys.executable}")
    say("")
    say("  Biarkan jendela ini terbuka. Tekan Ctrl+C untuk berhenti.")
    say(LINE)
    say("")

    if not args.no_browser:
        open_browser_later(url)

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except KeyboardInterrupt:
        say("")
        say("Clipper dihentikan. Sampai jumpa!")
        return 0
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        head("Server berhenti karena error")
        bad(str(exc))
        say("")
        traceback.print_exc()
        info("Laporkan pesan di atas kalau butuh bantuan.")
        return 1
    return 0


def _pause_if_windows() -> None:
    """Kalau dijalankan dengan double-click, jangan langsung tutup jendela."""
    if os.name != "nt":
        return
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\nTekan Enter untuk menutup jendela ini...")
    except Exception:
        pass


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    except KeyboardInterrupt:
        code = 0
    except Exception:  # noqa: BLE001 - error tak terduga tetap harus terbaca
        say("")
        say(LINE)
        say("  Terjadi error tak terduga")
        say(LINE)
        traceback.print_exc()
        code = 1
    finally:
        if code != 0 and "--no-pause" not in sys.argv:
            _pause_if_windows()
    sys.exit(code)
