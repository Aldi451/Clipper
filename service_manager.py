"""Manajer service Clipper — bisa dijalankan sebagai .py atau dibungkus jadi .exe.

Fungsi: memasang / menghapus / memulai / menghentikan service Windows Clipper, dan
mengatur tipe startup sesuai kehendak pengguna:

    Automatic  (auto)      → ikut nyala saat Windows boot
    Manual     (demand)    → hanya nyala kalau kamu klik Start
    Disabled               → dimatikan total, tidak bisa start sampai diaktifkan

Semua aksi memakai `sc.exe` bawaan Windows, jadi manajer ini tidak butuh pywin32.
Service-nya sendiri tetap dijalankan oleh Python milik project
(`.venv\\Scripts\\python.exe run.py --run-service`), sehingga `git pull` langsung
berlaku tanpa perlu membuat ulang .exe ini.

Pemakaian GUI (cara termudah):
    python service_manager.py            (atau double-click ClipperService.exe)

Pemakaian CLI:
    python service_manager.py --status
    python service_manager.py --install --start-type manual --port 8420
    python service_manager.py --start-type auto
    python service_manager.py --start | --stop | --remove | --open
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

SERVICE_NAME = "Clipper"
DISPLAY_NAME = "Clipper - Auto Clip to Shorts"
DESCRIPTION = (
    "Clipper berjalan di latar belakang tanpa jendela cmd. "
    "Buka http://localhost:8420 di browser untuk memakai aplikasinya."
)
DEFAULT_PORT = 8420
DEFAULT_HOST = "127.0.0.1"

START_TYPES = {
    "auto": ("auto", 2, "Automatic — nyala otomatis saat Windows boot"),
    "manual": ("demand", 3, "Manual — hanya nyala saat kamu klik Start"),
    "disabled": ("disabled", 4, "Disabled — dimatikan, tidak bisa start"),
}
STATE_LABELS = {
    1: "STOPPED (berhenti)",
    2: "START_PENDING (sedang mulai)",
    3: "STOP_PENDING (sedang berhenti)",
    4: "RUNNING (berjalan)",
}


# --------------------------------------------------------------------------- #
# lokasi project & python
# --------------------------------------------------------------------------- #
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Folder tempat .exe (atau skrip ini) berada."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def settings_dir() -> Path:
    """Simpan pilihan folder project di folder pengguna (tahan pindah .exe)."""
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "Clipper"
    return Path.home() / ".clipper"


SETTINGS_FILE = settings_dir() / "manager.json"


def load_prefs() -> dict[str, Any]:
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001
        pass
    return {}


def save_prefs(**values: Any) -> None:
    data = load_prefs()
    data.update({k: v for k, v in values.items() if v is not None})
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 - bukan hal fatal
        pass


def looks_like_project(folder: Path) -> bool:
    return (folder / "run.py").exists() and (folder / "app").is_dir()


def find_project() -> Path | None:
    """Cari folder project Clipper (yang ada run.py + app/)."""
    candidates: list[Path] = []
    preference = load_prefs().get("project_dir")
    if preference:
        candidates.append(Path(preference))
    candidates.append(app_dir())
    if not is_frozen():
        candidates.append(Path(__file__).resolve().parent)
    for folder in candidates:
        try:
            if looks_like_project(folder):
                return folder.resolve()
        except Exception:  # noqa: BLE001
            continue
    return None


def venv_python(project: Path) -> Path | None:
    """Python milik project: Windows pakai Scripts\\python.exe, lain-lain bin/python."""
    candidates = [
        project / ".venv" / "Scripts" / "python.exe",
        project / ".venv" / "Scripts" / "pythonw.exe",
        # dipakai saat pengembangan/manajer diuji di Linux & macOS
        project / ".venv" / "bin" / "python3",
        project / ".venv" / "bin" / "python",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


# --------------------------------------------------------------------------- #
# sc.exe
# --------------------------------------------------------------------------- #
def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_sc(*args: str) -> tuple[int, str]:
    """Jalankan sc.exe; kembalikan (returncode, output gabungan)."""
    try:
        proc = subprocess.run(
            ["sc.exe", *args],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=_no_window() if sys.platform.startswith("win") else 0,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "sc.exe tidak ditemukan (bukan Windows?)"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def parse_state(output: str) -> int | None:
    """Ambil angka STATE dari `sc query` (1..4), None kalau service tidak ada."""
    for line in (output or "").splitlines():
        if "STATE" in line.upper():
            digits = "".join(ch if ch.isdigit() else " " for ch in line).split()
            if digits:
                try:
                    return int(digits[0])
                except ValueError:
                    return None
    return None


def parse_start_type(output: str) -> str:
    """Ambil tipe startup dari `sc qc` → auto|manual|disabled|unknown."""
    low = (output or "").upper()
    if "START_TYPE" not in low:
        return "unknown"
    if "AUTO_START" in low:
        return "auto"
    if "DEMAND_START" in low:
        return "manual"
    if "DISABLED" in low:
        return "disabled"
    return "unknown"


def service_status() -> dict[str, Any]:
    """Ringkasan kondisi service (aman dipanggil walau sc.exe tidak ada)."""
    rc, out = run_sc("query", SERVICE_NAME)
    exists = rc == 0 and "SERVICE_NAME" in out.upper()
    if not exists:
        return {"exists": False, "state": "not-installed", "state_code": None,
                "start_type": "unknown", "url": service_url(), "raw": out.strip()[:400]}
    code = parse_state(out)
    _, qc = run_sc("qc", SERVICE_NAME)
    return {
        "exists": True,
        "state": "running" if code == 4 else "stopped" if code == 1 else "pending",
        "state_code": code,
        "state_label": STATE_LABELS.get(code or 0, "tidak diketahui"),
        "start_type": parse_start_type(qc),
        "url": service_url(),
        "raw": out.strip()[:400],
    }


def service_url(port: int | None = None) -> str:
    prefs = load_prefs()
    port = int(port or prefs.get("port") or DEFAULT_PORT)
    host = prefs.get("host") or DEFAULT_HOST
    label = "localhost" if host in ("0.0.0.0", "::", "") else host
    return f"http://{label}:{port}"


def bin_path(project: Path) -> str:
    """Perintah yang dijalankan Windows saat service start."""
    python = venv_python(project)
    if python is None:
        raise RuntimeError(
            "Python milik project belum ada (.venv\\Scripts\\python.exe).\n"
            "Jalankan run.bat sekali dulu supaya .venv dan dependensi terpasang."
        )
    return f'"{python}" "{project / "run.py"}" --run-service'


def log_file(project: Path) -> Path:
    data_dir = Path(os.environ.get("CLIPPER_DATA_DIR") or (project / "data"))
    return data_dir / "logs" / "clipper-service.log"


# --------------------------------------------------------------------------- #
# aksi
# --------------------------------------------------------------------------- #
def install(project: Path, start_type: str = "auto", port: int | None = None,
            host: str | None = None) -> dict[str, Any]:
    """Pasang (atau pasang ulang) service dengan tipe startup pilihan."""
    kind = (start_type or "auto").lower()
    if kind not in START_TYPES:
        raise ValueError(f"start-type harus salah satu dari: {', '.join(START_TYPES)}")
    sc_start = START_TYPES[kind][0]
    command = bin_path(project)

    save_prefs(project_dir=str(project), port=port, host=host, start_type=kind)
    # service.json dibaca oleh proses service (host/port), tulis di folder data
    data_dir = Path(os.environ.get("CLIPPER_DATA_DIR") or (project / "data"))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "service.json").write_text(
            json.dumps({"host": host or load_prefs().get("host") or DEFAULT_HOST,
                        "port": int(port or DEFAULT_PORT)}, indent=2),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass

    existed = service_status()["exists"]
    steps: list[str] = []
    if existed:
        if service_status()["state"] == "running":
            run_sc("stop", SERVICE_NAME)
            steps.append("service lama dihentikan")
        rc, out = run_sc("delete", SERVICE_NAME)
        steps.append(f"service lama dihapus (rc={rc})")

    rc, out = run_sc(
        "create", SERVICE_NAME,
        "binPath=", command,
        "start=", sc_start,
        "DisplayName=", DISPLAY_NAME,
        "obj=", "LocalSystem",
    )
    if rc != 0:
        raise RuntimeError(
            "Gagal memasang service.\n"
            "Pastikan aplikasi ini dijalankan sebagai Administrator (klik kanan → "
            "Run as administrator).\n\n" + out.strip()[:400]
        )
    steps.append(f"service dibuat (start={sc_start})")
    run_sc("description", SERVICE_NAME, DESCRIPTION)
    # hidupkan lagi otomatis kalau crash (bukan untuk mode disabled)
    if kind != "disabled":
        run_sc("failure", SERVICE_NAME, "reset=", "86400",
               "actions=", "restart/60000/restart/120000/restart/300000")
        steps.append("auto-restart dipasang")
    return {"installed": True, "start_type": kind, "command": command,
            "replaced": existed, "steps": steps}


def set_start_type(start_type: str) -> dict[str, Any]:
    kind = (start_type or "").lower()
    if kind not in START_TYPES:
        raise ValueError(f"start-type harus salah satu dari: {', '.join(START_TYPES)}")
    status = service_status()
    if not status["exists"]:
        raise RuntimeError("Service belum terpasang. Pilih 'Pasang service' dulu.")
    rc, out = run_sc("config", SERVICE_NAME, "start=", START_TYPES[kind][0])
    if rc != 0:
        raise RuntimeError("Gagal mengubah tipe startup (butuh Administrator).\n\n"
                           + out.strip()[:300])
    save_prefs(start_type=kind)
    return {"ok": True, "start_type": kind}


def start() -> dict[str, Any]:
    if not service_status()["exists"]:
        raise RuntimeError("Service belum terpasang.")
    rc, out = run_sc("start", SERVICE_NAME)
    if rc not in (0, 1056):  # 1056 = sudah berjalan
        raise RuntimeError("Gagal memulai service (butuh Administrator?).\n\n"
                           + out.strip()[:300])
    return {"ok": True, "url": service_url()}


def stop() -> dict[str, Any]:
    if not service_status()["exists"]:
        raise RuntimeError("Service belum terpasang.")
    rc, out = run_sc("stop", SERVICE_NAME)
    if rc not in (0, 1062):  # 1062 = belum berjalan
        raise RuntimeError("Gagal menghentikan service.\n\n" + out.strip()[:300])
    return {"ok": True}


def remove() -> dict[str, Any]:
    if not service_status()["exists"]:
        return {"removed": False, "reason": "service belum terpasang"}
    if service_status()["state"] == "running":
        run_sc("stop", SERVICE_NAME)
    rc, out = run_sc("delete", SERVICE_NAME)
    if rc != 0:
        raise RuntimeError("Gagal menghapus service (butuh Administrator).\n\n"
                           + out.strip()[:300])
    return {"removed": True}


def open_app() -> dict[str, Any]:
    url = service_url()
    try:
        if sys.platform.startswith("win"):
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            import webbrowser

            webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Tidak bisa membuka browser: {exc}") from exc
    return {"url": url}


def read_log(project: Path | None, lines: int = 60) -> str:
    if project is None:
        return "(folder Clipper tidak ditemukan)"
    path = log_file(project)
    if not path.exists():
        return f"(belum ada log)\n{path}"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:  # noqa: BLE001
        return f"(gagal membaca log: {exc})"
    return "\n".join(content[-lines:])


# --------------------------------------------------------------------------- #
# perbaikan setelah folder dipindah
# --------------------------------------------------------------------------- #
def _python_of(project: Path) -> Path | None:
    return venv_python(project)


def venv_healthy(project: Path | None) -> tuple[bool, str]:
    """venv dianggap sehat kalau python-nya bisa import fastapi + uvicorn."""
    if project is None:
        return False, "folder Clipper tidak ditemukan"
    python = venv_python(project)
    if python is None:
        return False, ".venv belum ada"
    try:
        proc = subprocess.run([str(python), "-c", "import fastapi, uvicorn"],
                              capture_output=True, text=True, timeout=180,
                              creationflags=_no_window() if sys.platform.startswith("win") else 0)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:160]
    if proc.returncode != 0:
        last = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        return False, (last[-1] if last else "venv tidak bisa dipakai")[:160]
    return True, "OK"


def rebuild_venv(project: Path, progress=None) -> tuple[bool, str]:
    """Hapus .venv lalu buat ulang + pasang dependensi."""
    import shutil

    target = project / ".venv"
    if target.exists():
        try:
            shutil.rmtree(target)
        except Exception as exc:  # noqa: BLE001
            return False, (f"Gagal menghapus .venv: {exc}\n"
                           "Tutup Clipper/service dulu, lalu coba lagi.")
    launcher = sys.executable
    steps = [
        ([launcher, "-m", "venv", str(target)], 600, "membuat .venv"),
        ([str(venv_python(project)), "-m", "pip", "install", "--upgrade", "pip", "--quiet"], 900,
         "memperbarui pip"),
        ([str(venv_python(project)), "-m", "pip", "install", "-r",
          str(project / "requirements.txt")], 3600, "memasang dependensi"),
    ]
    for cmd, timeout, label in steps:
        if progress:
            progress(label)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return False, f"gagal {label}: {exc}"
        if proc.returncode != 0:
            detail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
            return False, f"gagal {label}: {(detail[-1] if detail else '')[:200]}"
    return True, "venv baru siap"


def service_bin_path() -> str:
    """Baris BINARY_PATH_NAME dari `sc qc` (path yang dipakai service)."""
    code, out = run_sc("qc", SERVICE_NAME)
    if code != 0:
        return ""
    for line in out.splitlines():
        if "BINARY_PATH_NAME" in line.upper():
            return line.split(":", 1)[-1].strip()
    return ""


def service_path_stale() -> bool:
    """Service menunjuk .exe yang sudah tidak ada (folder dipindah)."""
    if not service_status()["exists"]:
        return False
    import re

    paths = re.findall(r'"([^"]+)"', service_bin_path())
    return bool(paths) and any(not Path(p).exists() for p in paths)


def repair(project: Path | None, progress=None) -> dict[str, Any]:
    """Perbaiki venv + arahkan service ke folder baru (tipe startup dipertahankan)."""
    if project is None:
        raise RuntimeError("Folder Clipper belum dipilih.")
    result: dict[str, Any] = {"steps": []}

    healthy, reason = venv_healthy(project)
    if healthy:
        result["steps"].append("venv sehat")
    else:
        result["steps"].append(f"venv bermasalah ({reason}) → dibangun ulang")
        ok, message = rebuild_venv(project, progress)
        result["steps"].append(message)
        if not ok:
            raise RuntimeError("\n".join(result["steps"]))

    status = service_status()
    if status["exists"]:
        if service_path_stale():
            start = status["start_type"] if status["start_type"] in START_TYPES else "auto"
            install(project, start)
            result["steps"].append(f"service diarahkan ke folder baru (start={start})")
        else:
            result["steps"].append("service sudah benar")
    else:
        result["steps"].append("service belum terpasang (dilewati)")
    return result


# --------------------------------------------------------------------------- #
# hak akses Administrator (Windows)
# --------------------------------------------------------------------------- #
def is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - Linux/macOS
        return False


def relaunch_as_admin() -> bool:
    """Minta UAC lalu jalankan ulang diri sendiri (exe/script) sebagai admin."""
    try:
        import ctypes

        if is_frozen():
            target = sys.executable
            params = " ".join(sys.argv[1:])
        else:
            target = sys.executable
            params = " ".join([f'"{Path(__file__).resolve()}"', *sys.argv[1:]])
        rc = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", target, params, str(app_dir()), 1
        )
        return int(rc) > 32
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Manajer service Clipper")
    parser.add_argument("--status", action="store_true", help="Tampilkan status service")
    parser.add_argument("--install", action="store_true", help="Pasang / pasang ulang service")
    parser.add_argument("--remove", action="store_true", help="Hapus service")
    parser.add_argument("--start", action="store_true", help="Mulai service")
    parser.add_argument("--stop", action="store_true", help="Hentikan service")
    parser.add_argument("--open", action="store_true", help="Buka Clipper di browser")
    parser.add_argument("--log", action="store_true", help="Tampilkan log service")
    parser.add_argument("--repair", action="store_true",
                        help="Perbaiki venv + service setelah folder dipindah")
    parser.add_argument("--start-type", choices=list(START_TYPES), help="auto | manual | disabled")
    parser.add_argument("--port", type=int, default=None, help=f"Port (default {DEFAULT_PORT})")
    parser.add_argument("--host", default=None, help=f"Host (default {DEFAULT_HOST})")
    parser.add_argument("--project", default=None, help="Folder project Clipper")
    args = parser.parse_args(argv)

    project = Path(args.project).resolve() if args.project else find_project()
    if project is not None and not looks_like_project(project):
        if args.install or args.log:
            print(f"[!] Folder itu bukan folder Clipper: {project}")
            print("    Harus berisi run.py dan folder app/.")
            return 2
        project = find_project()
    if project is None and (args.install or args.log or args.repair):
        print("[!] Folder Clipper tidak ditemukan (harus ada run.py + app/).")
        print("    Letakkan ClipperService.exe di folder Clipper, atau pakai --project.")
        return 2

    try:
        if args.install:
            result = install(project, args.start_type or "auto", args.port, args.host)
            print(f"[OK] Service '{SERVICE_NAME}' terpasang (start={result['start_type']})")
            print(f"     perintah: {result['command']}")
            for step in result["steps"]:
                print(f"     - {step}")
            print(f"     alamat  : {service_url()}")
            return 0
        if args.start_type and not args.install:
            set_start_type(args.start_type)
            print(f"[OK] Tipe startup → {args.start_type}")
            return 0
        if args.start:
            result = start()
            print(f"[OK] Service dimulai → {result['url']}")
            return 0
        if args.stop:
            stop()
            print("[OK] Service dihentikan")
            return 0
        if args.remove:
            result = remove()
            print("[OK] Service dihapus" if result.get("removed") else "[i] Service belum terpasang")
            return 0
        if args.open:
            print(f"[OK] Membuka {open_app()['url']}")
            return 0
        if args.repair:
            out = repair(project, progress=lambda m: print(f"  [..] {m}"))
            for step in out["steps"]:
                print(f"  [OK] {step}")
            print(f"[OK] Selesai. Buka {service_url()}")
            return 0
        if args.log:
            print(read_log(project))
            return 0
        # default: status
        status = service_status()
        print(f"Service   : {SERVICE_NAME}")
        print(f"Terpasang : {'ya' if status['exists'] else 'belum'}")
        print(f"Kondisi   : {STATE_LABELS.get(status['state_code'] or 0, 'berhenti/tidak ada')}")
        print(f"Tipe      : {status['start_type']}")
        print(f"Alamat    : {status['url']}")
        return 0
    except Exception as exc:  # noqa: BLE001 - pesan sudah ramah
        print(f"[!] {exc}")
        return 1


# --------------------------------------------------------------------------- #
# GUI (tkinter)
# --------------------------------------------------------------------------- #
def gui() -> int:
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception:  # noqa: BLE001
        print("[!] tkinter tidak tersedia di Python ini.")
        print("    Di Windows, tkinter sudah ikut saat install Python dari python.org.")
        print("    Sementara ini pakai mode terminal:  python service_manager.py --status")
        return 2

    root = tk.Tk()
    root.title("Clipper — Manajer Service")
    root.geometry("560x520")
    root.minsize(520, 470)
    try:
        root.iconname("Clipper")
    except Exception:  # noqa: BLE001
        pass

    project = find_project()
    prefs = load_prefs()

    style = ttk.Style()
    try:
        style.theme_use("vista")
    except Exception:  # noqa: BLE001
        pass

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Clipper — Manajer Service",
              font=("Segoe UI", 15, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Atur Clipper sebagai service Windows tanpa jendela cmd.",
              foreground="#555").pack(anchor="w", pady=(0, 12))

    status_var = tk.StringVar(value="memeriksa…")
    status_box = ttk.LabelFrame(frame, text="Status", padding=10)
    status_box.pack(fill="x")
    ttk.Label(status_box, textvariable=status_var, justify="left").pack(anchor="w")

    project_var = tk.StringVar(value=str(project) if project else "(belum ditemukan)")
    proj_box = ttk.LabelFrame(frame, text="Folder Clipper", padding=10)
    proj_box.pack(fill="x", pady=(10, 0))
    ttk.Label(proj_box, textvariable=project_var, foreground="#555",
              wraplength=470).pack(anchor="w")

    def choose_project() -> None:
        nonlocal project
        from tkinter import filedialog

        folder = filedialog.askdirectory(title="Pilih folder Clipper (yang ada run.py)")
        if not folder:
            return
        candidate = Path(folder)
        if not looks_like_project(candidate):
            messagebox.showerror("Salah folder",
                                 "Folder itu tidak berisi run.py dan app/.\n"
                                 "Pilih folder hasil git clone Clipper.")
            return
        project = candidate.resolve()
        project_var.set(str(project))
        save_prefs(project_dir=str(project))
        refresh()

    ttk.Button(proj_box, text="Pilih folder…", command=choose_project).pack(anchor="w", pady=(8, 0))

    # ---- tipe startup ----
    type_var = tk.StringVar(value=prefs.get("start_type") or "auto")
    type_box = ttk.LabelFrame(frame, text="Tipe startup", padding=10)
    type_box.pack(fill="x", pady=(10, 0))
    for key, (_, _, label) in START_TYPES.items():
        ttk.Radiobutton(type_box, text=label, value=key, variable=type_var).pack(anchor="w")

    port_var = tk.StringVar(value=str(prefs.get("port") or DEFAULT_PORT))
    port_row = ttk.Frame(frame)
    port_row.pack(fill="x", pady=(10, 0))
    ttk.Label(port_row, text="Port:").pack(side="left")
    ttk.Entry(port_row, textvariable=port_var, width=8).pack(side="left", padx=(8, 0))
    ttk.Label(port_row, text="(default 8420)", foreground="#777").pack(side="left", padx=(8, 0))

    # ---- tombol aksi ----
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(14, 0))

    def guard(action, success_msg: str | None = None):
        def run() -> None:
            nonlocal project
            if not is_admin() and sys.platform.startswith("win"):
                if messagebox.askyesno(
                    "Butuh Administrator",
                    "Aksi ini perlu izin Administrator.\n\nJalankan ulang sebagai "
                    "Administrator sekarang?",
                ):
                    if relaunch_as_admin():
                        root.destroy()
                        return
                    messagebox.showerror("Gagal", "Izin Administrator ditolak.")
                return
            try:
                result = action()
                if success_msg:
                    messagebox.showinfo("Clipper", success_msg.format(**(result or {})))
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Gagal", str(exc))
            refresh()

        return run

    def do_install() -> dict[str, Any]:
        if project is None:
            raise RuntimeError("Folder Clipper belum dipilih.")
        try:
            port = int(port_var.get().strip() or DEFAULT_PORT)
        except ValueError as exc:
            raise RuntimeError("Port harus berupa angka.") from exc
        return install(project, type_var.get(), port)

    ttk.Button(buttons, text="Pasang / Update service",
               command=guard(do_install, "Service terpasang.\nMulai dengan tombol 'Start'.")) \
        .grid(row=0, column=0, sticky="ew", padx=2, pady=2)
    ttk.Button(buttons, text="Start",
               command=guard(start, "Service berjalan.\n{url}")) \
        .grid(row=0, column=1, sticky="ew", padx=2, pady=2)
    ttk.Button(buttons, text="Stop", command=guard(stop, "Service dihentikan.")) \
        .grid(row=1, column=0, sticky="ew", padx=2, pady=2)
    ttk.Button(buttons, text="Hapus service", command=guard(remove, "Service dihapus.")) \
        .grid(row=1, column=1, sticky="ew", padx=2, pady=2)
    ttk.Button(buttons, text="Terapkan tipe startup",
               command=guard(lambda: set_start_type(type_var.get()),
                             "Tipe startup diubah ke {start_type}.")) \
        .grid(row=2, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
    buttons.columnconfigure(0, weight=1)
    buttons.columnconfigure(1, weight=1)

    extra = ttk.Frame(frame)
    extra.pack(fill="x", pady=(10, 0))
    ttk.Button(extra, text="Buka Clipper", command=lambda: guard(open_app)()).pack(
        side="left", padx=2)

    def show_log() -> None:
        win = tk.Toplevel(root)
        win.title("Log service Clipper")
        win.geometry("720x420")
        text = tk.Text(win, wrap="none", font=("Consolas", 9))
        text.pack(fill="both", expand=True)
        text.insert("1.0", read_log(project))
        text.see("end")
        ttk.Button(win, text="Tutup", command=win.destroy).pack(pady=6)

    ttk.Button(extra, text="Lihat log", command=show_log).pack(side="left", padx=2)

    def do_repair() -> None:
        if project is None:
            messagebox.showerror("Gagal", "Folder Clipper belum dipilih.")
            return
        win = tk.Toplevel(root)
        win.title("Memperbaiki Clipper…")
        win.geometry("520x220")
        label = ttk.Label(win, text="Memeriksa…", wraplength=480, justify="left")
        label.pack(padx=14, pady=14, anchor="w")
        bar = ttk.Progressbar(win, mode="indeterminate")
        bar.pack(fill="x", padx=14)
        bar.start(12)

        def worker() -> None:
            try:
                out = repair(project, progress=lambda m: label.config(text=m))
                text = "\n".join(f"✓ {x}" for x in out["steps"])
                label.config(text=text)
            except Exception as exc:  # noqa: BLE001
                label.config(text=f"Dari sebagian:\n{exc}")
            finally:
                bar.stop()
                refresh()

        threading.Thread(target=worker, daemon=True).start()

    ttk.Button(extra, text="Perbaiki", command=do_repair).pack(side="left", padx=2)

    def refresh() -> None:
        status = service_status()
        if not status["exists"]:
            status_var.set("Belum terpasang.\nPilih tipe startup, lalu klik "
                           "'Pasang / Update service'.")
        elif service_path_stale():
            status_var.set("PERLU DIPERBAIKI: service masih menunjuk folder lama "
                           "(folder Clipper dipindah?).\nKlik tombol 'Perbaiki'.")
        else:
            label = {"auto": "Automatic (nyala saat boot)",
                     "manual": "Manual (start sendiri)",
                     "disabled": "Disabled",
                     "unknown": "tidak diketahui"}[status["start_type"]]
            status_var.set(
                f"{'Berjalan' if status['state'] == 'running' else 'Berhenti'}"
                f"  ·  tipe: {label}\nAlamat: {status['url']}"
            )
        try:
            type_var.set(service_status()["start_type"]
                         if service_status()["start_type"] in START_TYPES
                         else type_var.get())
        except Exception:  # noqa: BLE001
            pass

    refresh()
    root.mainloop()
    return 0


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        return cli(argv)
    if sys.platform.startswith("win") and not is_admin():
        # GUI tetap jalan; tombol yang butuh admin akan menawarkan elevasi.
        pass
    return gui()


if __name__ == "__main__":
    sys.exit(main())
