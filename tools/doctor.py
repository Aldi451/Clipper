#!/usr/bin/env python3
"""Doctor TikTok Clipper — diagnosa & perbaikan dukungan upload TikTok.

Kenapa script ini ada
---------------------
Upload TikTok di Clipper memakai Playwright + browser (Edge/Chrome) di komputer
sendiri. Di Windows sering muncul dua masalah yang terlihat sama:

  1. ``pip`` memasang Playwright ke folder *user site*
     (``%APPDATA%\\Python\\Python3XX\\site-packages``) padahal Clipper jalan di
     ``.venv``, sehingga aplikasi menganggap Playwright "tidak ada".
  2. Browser tidak terdeteksi (Edge ada, tapi dicari di tempat yang salah),
     atau unduhan Chromium gagal tanpa pesan yang jelas.

Script ini menjawab pertanyaan "sebenarnya yang kurang apa?" dengan bukti,
lalu memperbaikinya dengan aman. Logikanya **memakai modul yang sama** dengan
aplikasi (``app/tiktok.py``), jadi hasil diagnosa tidak pernah berbeda dengan
apa yang dilihat aplikasi.

Mode
----
  (tanpa argumen)   diagnosa saja — READ-ONLY, tidak mengubah apa pun
  --fix             pasang Playwright ke .venv + unduh browser bila perlu
  --verify          uji betulan: buka browser headless lalu tutup lagi
  --check-update    bandingkan versi Clipper di folder ini dengan versi terbaru
  --json            keluarkan hasil diagnosa sebagai JSON (untuk otomatisasi)

Keamanan
--------
* Default tidak mengubah apa pun (read-only). Perubahan hanya terjadi bila
  ``--fix`` diminta secara eksplisit.
* Tidak pernah menghapus/menyentuh ``data/`` (video, klip, cookies, token
  YouTube, profil TikTok). ``--fix`` hanya memasang paket Python & browser.
* Tidak butuh Administrator: paket dipasang ke ``.venv`` dan browser ke folder
  milik user (``%LOCALAPPDATA%\\ms-playwright``).
* Setiap langkah ditulis ke ``logs/tiktok-doctor-<waktu>.log``.

Exit code: 0 = siap, 1 = ada peringatan, 2 = gagal/belum siap.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs"
DEFAULT_PORT = 8420
REPO_SLUG = "Aldi451/Clipper"

OK = "[ OK ]"
NO = "[ !! ]"
WARN = "[ ?? ]"


# --------------------------------------------------------------------------- #
# logging: tampil di layar sekaligus disimpan ke berkas
# --------------------------------------------------------------------------- #
class Log:
    def __init__(self) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.path: Path | None = LOG_DIR / f"tiktok-doctor-{stamp}.log"
            self.path.write_text(
                f"Clipper TikTok Doctor — {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Folder  : {ROOT}\n"
                f"Python  : {sys.executable} ({sys.version.split()[0]})\n"
                f"OS      : {os.name} / {sys.platform}\n\n",
                encoding="utf-8",
            )
        except OSError:
            self.path = None
        self.lines: list[str] = []

    def __call__(self, text: str = "", status: str = "") -> None:
        prefix = f"{status} " if status else ""
        line = prefix + text
        print(line, flush=True)
        self.lines.append(line)
        if self.path is not None:
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass

    def head(self, title: str) -> None:
        self("")
        self("=" * 62)
        self(f"  {title}")
        self("=" * 62)

    def save(self) -> None:
        if self.path is None:
            return
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("\n--- ringkasan ---\n")
                handle.write("\n".join(self.lines) + "\n")
        except OSError:
            pass


log = Log()


# --------------------------------------------------------------------------- #
# helper kecil
# --------------------------------------------------------------------------- #
def local_version() -> str:
    """Baca VERSION dari app/config.py tanpa mengimpor paket (aman & cepat)."""
    try:
        text = (ROOT / "app" / "config.py").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "?"
    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1) if match else "?"


def venv_python() -> Path | None:
    """Python milik .venv Clipper (yang dipakai aplikasi saat dijalankan)."""
    for rel in ("Scripts/python.exe", "bin/python", "bin/python3"):
        candidate = ROOT / ".venv" / rel
        if candidate.exists():
            return candidate
    return None


def run_capture(cmd: list[str], timeout: float = 300) -> tuple[int, str]:
    """Jalankan perintah, kembalikan (exit code, gabungan stdout+stderr)."""
    try:
        flags: dict[str, Any] = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=timeout, **flags
        )
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except FileNotFoundError:
        return 127, f"perintah tidak ditemukan: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout setelah {timeout:.0f} detik"
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


_NOISE = re.compile(r"^(host|port|localAddress|localPort|remoteAddress|remotePort|code|errno|syscall|address|stack|\}|\{|\s*)")


def useful_tail(output: str, limit: int = 4) -> list[str]:
    """Ambil beberapa baris terakhir yang benar-benar berguna dari output tool."""
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    keep: list[str] = []
    important = re.compile(r"error|gagal|failed|denied|timeout|refused|not found|cannot|tidak", re.I)
    for line in lines:
        if _NOISE.match(line) and not important.search(line):
            continue
        if line in keep:
            continue
        keep.append(line[:180])
    if not keep:
        keep = [line[:180] for line in lines[-limit:]]
    hits = [line for line in keep if important.search(line)]
    chosen = hits[-limit:] if hits else keep[-limit:]
    return chosen


def error_line(output: str) -> str:
    """Ambil SATU baris yang paling menjelaskan penyebab kegagalan."""
    patterns = (
        r"Executable doesn't exist",
        r"no such file",
        r"Executable .* not found",
        r"error while loading shared libraries",
        r"Failed to launch",
        r"Target page, context or browser has been closed",
        r"connection refused",
        r"ECONNREFUSED",
        r"Permission denied",
        r"Access is denied",
        r"Error:\s*[^\s]",
    )
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    for pattern in patterns:
        for line in lines:
            if re.search(pattern, line, re.I):
                return line[:180]
    for line in reversed(lines):
        if not _NOISE.match(line):
            return line[:180]
    return lines[-1][:180] if lines else "(tanpa pesan)"


def port_open(port: int = DEFAULT_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.6)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def server_health(port: int = DEFAULT_PORT) -> dict[str, Any] | None:
    """Ambil /api/health dari Clipper yang sedang jalan (kalau ada)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def latest_remote_version() -> str:
    """Versi terbaru di repo GitHub (butuh internet; gagal = string kosong)."""
    url = f"https://api.github.com/repos/{REPO_SLUG}/contents/app/config.py?ref=main"
    request = urllib.request.Request(url, headers={"User-Agent": "Clipper-Doctor"})
    try:
        with urllib.request.urlopen(request, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        import base64

        content = base64.b64decode(data.get("content") or "").decode("utf-8", "replace")
        match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        return match.group(1) if match else ""
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, KeyError):
        return ""


# --------------------------------------------------------------------------- #
# diagnosa
# --------------------------------------------------------------------------- #
def diagnose(check_update: bool = False) -> dict[str, Any]:
    from app import tiktok

    info: dict[str, Any] = {"root": str(ROOT), "version": local_version()}

    log.head(f"Diagnosa TikTok — Clipper {info['version']}")
    log(f"Folder : {ROOT}")

    # --- python & venv ---
    vpy = venv_python()
    info["venv_python"] = str(vpy) if vpy else ""
    if vpy:
        code, out = run_capture([str(vpy), "-c", "import sys; print(sys.version.split()[0])"], 60)
        ver = out.strip() if code == 0 else "?"
        log(f"Python .venv : {vpy}  (versi {ver})", OK)
    else:
        log(".venv belum ada — jalankan run.bat sekali supaya dibuatkan.", WARN)

    if sys.executable and vpy and Path(sys.executable).resolve() != vpy.resolve():
        log(f"Script ini jalan di Python lain: {sys.executable}", WARN)
        log("   (tidak masalah; diagnosa tetap memakai modul app/ dari folder ini)")

    # --- Clipper sedang jalan? ---
    health = server_health()
    info["server_running"] = bool(health)
    if health:
        info["server_version"] = health.get("version", "?")
        log(f"Clipper jalan di port {DEFAULT_PORT} — versi {health.get('version', '?')}", OK)
        if str(health.get("version")) != info["version"]:
            log("Versi server beda dengan versi kode di folder ini.", WARN)
            log("   → Clipper masih memakai proses lama. Tutup lalu jalankan run.bat lagi.")
    else:
        log(f"Clipper tidak sedang jalan di port {DEFAULT_PORT} (tidak masalah).")

    # --- Playwright ---
    ok_import = tiktok.ensure_playwright()
    info["playwright_ok"] = ok_import
    info["playwright_error"] = tiktok.import_error()
    info["playwright_path"] = str(tiktok._import_state.get("path") or "")  # noqa: SLF001
    if ok_import:
        log(f"Playwright : terpakai dari {info['playwright_path']}", OK)
        venv_root = str(vpy.parent.parent).lower() if vpy else ""
        inside_venv = bool(venv_root) and info["playwright_path"].lower().startswith(venv_root)
        info["playwright_in_venv"] = inside_venv
        if not inside_venv:
            log("   Catatan: paket ini ada di LUAR .venv (biasanya folder user-site).", "   ")
            log("   Clipper bisa memakainya, tapi lebih rawan hilang/tersembunyi.")
            log("   perbaiki_tiktok.bat akan memasangnya ke dalam .venv supaya pasti terbaca.")
    else:
        log(f"Playwright : TIDAK bisa dipakai — {info['playwright_error']}", NO)

    # paket terpasang di mana saja? (inilah sumber kebingungan user-site)
    code, out = run_capture([sys.executable, "-m", "pip", "show", "playwright"], 120)
    pip_present = code == 0 and "Version:" in out
    info["pip_sees_playwright"] = pip_present
    if pip_present:
        ver = next((line for line in out.splitlines() if line.startswith("Version:")), "")
        loc = next((line for line in out.splitlines() if line.startswith("Location:")), "")
        log(f"pip melihat  : playwright {ver.replace('Version:', '').strip()} → {loc.replace('Location:', '').strip()}")

    if vpy and vpy != Path(sys.executable):
        code, out = run_capture([str(vpy), "-c", "import playwright, sys; print(playwright.__file__)"], 120)
        info["venv_has_playwright"] = code == 0
        if code == 0:
            log(f"Di .venv     : playwright ada ({out.strip()})", OK)
        else:
            log("Di .venv     : playwright TIDAK ada", NO)
            log("   → inilah penyebab status 'belum terpasang' walau pip bilang sudah ada.")
            log("   → jalankan perbaiki_tiktok.bat (memasang ke .venv, bukan ke Python global).")

    # --- browser ---
    browsers = tiktok._browser_paths()  # noqa: SLF001
    info["browsers"] = [{"channel": c, "path": str(p)} for c, p in browsers]
    if browsers:
        for channel, path in browsers:
            log(f"Browser     : {channel} → {path}", OK)
    else:
        log("Browser     : tidak ada Edge/Chrome/Brave/Vivaldi yang terdeteksi", NO)
        log("   → Edge biasanya ada di C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe")

    chromium = tiktok.chromium_downloaded()
    info["chromium"] = chromium
    if chromium:
        log("Chromium Clipper (Playwright): tersedia", OK)
    else:
        log("Chromium Clipper (Playwright): belum diunduh (hanya perlu kalau tidak ada Edge/Chrome)")

    info["ready"] = bool(tiktok.ready())
    if info["ready"]:
        log("Status      : SIAP dipakai untuk upload TikTok", OK)
    else:
        log("Status      : BELUM SIAP", NO)

    # --- update? ---
    if check_update:
        remote = latest_remote_version()
        info["remote_version"] = remote
        if remote:
            if remote != info["version"]:
                log(f"Versi terbaru di GitHub: {remote} — folder ini {info['version']}", WARN)
                log("   → jalankan update.bat lalu run.bat lagi.")
            else:
                log(f"Versi terbaru di GitHub: {remote} (sudah sama)", OK)
        else:
            log("Tidak bisa memeriksa versi terbaru (internet/API tidak terjangkau).", WARN)

    # --- saran ---
    log("")
    if info["ready"]:
        log("Tidak ada yang perlu diperbaiki. Langkah berikut: buka tab TikTok →")
        log("'Buka TikTok & login' → login sekali → upload clip.")
    else:
        log("Cara memperbaiki (pilih satu):")
        log("   1. Double-click  perbaiki_tiktok.bat   (paling mudah, otomatis)")
        log("   2. Manual:")
        py = str(vpy) if vpy else sys.executable
        log(f'      "{py}" -m pip install --upgrade playwright')
        log(f'      "{py}" -m playwright install chromium')
    if log.path:
        log("")
        log(f"Log lengkap: {log.path}")
    return info


# --------------------------------------------------------------------------- #
# perbaikan
# --------------------------------------------------------------------------- #
def _venv_has_playwright(vpy: Path) -> tuple[bool, str]:
    """Cek langsung di Python .venv (bukan di proses script ini)."""
    code, out = run_capture([str(vpy), "-c", "import playwright, sys; print(playwright.__file__)"], 120)
    return code == 0, out.strip()


def fix() -> bool:
    """Pasang Playwright ke .venv dan pastikan ada browser yang bisa dipakai.

    Penting: semua perintah memakai Python **.venv**, bukan Python yang
    menjalankan script ini. Kesalahan klasik: paket ada di user-site (terbaca
    oleh script) tapi TIDAK ada di .venv (yang dipakai Clipper) sehingga
    ``-m playwright install`` gagal dengan "No module named playwright".
    """
    from app import tiktok

    log.head("Perbaikan dukungan TikTok")
    log("Yang akan dilakukan:")
    log("  1. memastikan paket playwright terpasang DI DALAM .venv Clipper")
    log("  2. memastikan ada browser (pakai Edge/Chrome yang sudah ada,")
    log("     atau unduh Chromium ±130 MB)")
    log("Tidak ada data (video/klip/cookies/token) yang disentuh.")
    log("")

    vpy = venv_python()
    if vpy is None:
        log(".venv belum ada. Jalankan run.bat sekali dulu (dependensi dipasang di sana).", NO)
        return False
    log(f"Python Clipper (.venv): {vpy}")
    log("")

    # --- 1. Playwright di dalam .venv ---
    in_venv, where = _venv_has_playwright(vpy)
    if in_venv:
        log(f"Playwright sudah ada di .venv ({where})", OK)
    else:
        log("Playwright belum ada di .venv — memasang sekarang…")
        code, out = run_capture([str(vpy), "-m", "pip", "install", "--upgrade", "playwright"], 1800)
        for line in useful_tail(out):
            log(f"   {line}")
        in_venv, where = _venv_has_playwright(vpy)
        if not in_venv:
            log("Gagal memasang Playwright ke .venv.", NO)
            log("   Periksa koneksi internet / antivirus, lalu coba lagi.", "   ")
            log(f'   Manual: "{vpy}" -m pip install --upgrade playwright', "   ")
            # fallback: pakai Playwright yang mungkin ada di user-site
            if not tiktok.ensure_playwright():
                return False
            log("Sebagai jalan sementara, memakai Playwright dari luar .venv.", WARN)
        else:
            log(f"Playwright terpasang di .venv ({where})", OK)

    # --- 2. Browser ---
    browsers = tiktok._browser_paths()  # noqa: SLF001
    if browsers:
        channel, path = browsers[0]
        log(f"Memakai browser yang sudah terpasang: {channel} → {path}", OK)
    elif tiktok.chromium_downloaded():
        log("Memakai Chromium Playwright yang sudah ada.", OK)
    else:
        log("Belum ada browser — mengunduh Chromium (±130 MB, sekali saja)…")
        py = str(vpy) if in_venv else sys.executable
        code, out = run_capture([py, "-m", "playwright", "install", "chromium"], 1800)
        for line in useful_tail(out):
            log(f"   {line}")
        if code != 0 or not tiktok.chromium_downloaded():
            log("Unduhan browser gagal.", NO)
            if not browsers:
                log("Kalau komputer ini punya Microsoft Edge, sebenarnya tidak perlu unduh:", "   ")
                log("pastikan Edge tidak diblokir antivirus, lalu jalankan script ini lagi.", "   ")
            log(f'   Manual: "{py}" -m playwright install chromium', "   ")
            return False
        log("Chromium selesai diunduh.", OK)

    ready = tiktok.ready()
    log("")
    log("Status: " + ("SIAP dipakai" if ready else "BELUM SIAP"), OK if ready else NO)
    return ready


# --------------------------------------------------------------------------- #
# uji betulan: buka browser headless
# --------------------------------------------------------------------------- #
def verify() -> bool:
    from app import tiktok

    log.head("Uji coba browser (headless)")
    if not tiktok.ensure_playwright():
        log("Playwright belum bisa dipakai — jalankan dulu perbaiki_tiktok.bat", NO)
        return False

    code = (
        "import sys\n"
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as pw:\n"
        "    opts = {'headless': True, 'args': ['--no-sandbox']}\n"
        "    target = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "    if target:\n"
        "        opts['executable_path'] = target\n"
        "    browser = pw.chromium.launch(**opts)\n"
        "    page = browser.new_page()\n"
        "    page.set_content('<h1>ok</h1>')\n"
        "    print('BROWSER_OK', page.title(), page.inner_text('h1'))\n"
        "    browser.close()\n"
    )
    browsers = tiktok._browser_paths()  # noqa: SLF001
    attempts: list[tuple[str, list[str]]] = []
    if browsers:
        attempts.append((f"{browsers[0][0]} ({browsers[0][1].name})", [str(browsers[0][1])]))
    attempts.append(("Chromium Playwright", []))

    py = str(venv_python() or sys.executable)
    for label, extra in attempts:
        log(f"Mencoba membuka: {label}…")
        code_run, out = run_capture([py, "-c", code, *extra], 180)
        if "BROWSER_OK" in (out or ""):
            log(f"Berhasil membuka {label} dan memuat halaman uji.", OK)
            return True
        log(f"Gagal: {error_line(out)}", NO)

    log("Semua cara gagal membuka browser.", NO)
    log("Saran: pastikan Edge/Chrome tidak diblokir antivirus, atau klik", "   ")
    log("'Unduh browser Clipper' di halaman TikTok Clipper.", "   ")
    return False


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnosa & perbaikan dukungan upload TikTok di Clipper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Contoh:\n"
            "  python tools/doctor.py                 # diagnosa (read-only)\n"
            "  python tools/doctor.py --fix --verify  # perbaiki lalu uji\n"
            "  python tools/doctor.py --check-update  # cek versi terbaru\n"
            "  python tools/doctor.py --json          # keluaran JSON\n"
        ),
    )
    parser.add_argument("--fix", action="store_true", help="pasang Playwright/browser bila perlu")
    parser.add_argument("--verify", action="store_true", help="uji membuka browser headless")
    parser.add_argument("--check-update", action="store_true", help="bandingkan versi dengan GitHub")
    parser.add_argument("--json", action="store_true", help="cetak hasil sebagai JSON (tanpa log)")
    args = parser.parse_args()

    try:
        if args.json:
            from app import tiktok

            info = {
                "root": str(ROOT),
                "version": local_version(),
                "python": sys.executable,
                "venv_python": str(venv_python() or ""),
                "ready": bool(tiktok.ready()),
                "playwright_ok": tiktok.ensure_playwright(),
                "playwright_error": tiktok.import_error(),
                "browsers": [{"channel": c, "path": str(p)} for c, p in tiktok._browser_paths()],  # noqa: SLF001
                "chromium": tiktok.chromium_downloaded(),
                "server_running": port_open(),
            }
            if args.check_update:
                info["remote_version"] = latest_remote_version()
            print(json.dumps(info, indent=2, ensure_ascii=False))
            return 0 if info["ready"] else 2

        if args.fix:
            ok = fix()
            if args.verify and ok:
                ok = verify()
            return 0 if ok else 2

        info = diagnose(check_update=args.check_update)
        if args.verify:
            ok = verify()
            info["verified"] = ok
            return 0 if ok else 2
        return 0 if info.get("ready") else 2
    except KeyboardInterrupt:
        log("")
        log("Dibatalkan oleh pengguna.")
        return 130
    except Exception as exc:  # noqa: BLE001
        log(f"Error tak terduga: {type(exc).__name__}: {exc}", NO)
        import traceback

        traceback.print_exc()
        return 2
    finally:
        log.save()


if __name__ == "__main__":
    raise SystemExit(main())
