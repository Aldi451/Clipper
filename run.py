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
    args = parser.parse_args()

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
