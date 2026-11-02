"""Perbaikan otomatis setelah folder Clipper dipindah (mis. C: → D:).

Kenapa perlu: `.venv` menyimpan path absolut. Begitu folder dipindah,
`python.exe` di dalam venv masih ada tapi paket-paketnya tidak bisa di-import,
dan Windows service / autostart masih menunjuk path lama. Gejalanya: Clipper
"blank" atau langsung tertutup tanpa pesan.

Modul ini dipakai oleh `repair.bat`, `python run.py --repair`, dan GUI
`service_manager.py`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_FILES = ("run.py", "requirements.txt", "web/index.html", "app/server.py")
SERVICE_NAME = "Clipper"
TASK_NAME = "Clipper (autostart)"


# --------------------------------------------------------------------------- #
# util
# --------------------------------------------------------------------------- #
def is_windows() -> bool:
    return sys.platform.startswith("win")


def log(message: str = "") -> None:
    print(message, flush=True)


def venv_python(project: Path = ROOT) -> Path | None:
    for rel in ("Scripts/python.exe", "bin/python3", "bin/python"):
        candidate = project / ".venv" / rel
        if candidate.exists():
            return candidate
    return None


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 1800) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd or ROOT), capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, f"perintah tidak ditemukan: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "perintah terlalu lama (timeout)"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


# --------------------------------------------------------------------------- #
# 1. file project
# --------------------------------------------------------------------------- #
def check_files(project: Path = ROOT) -> list[str]:
    """Kembalikan daftar file/folder penting yang hilang."""
    missing = [name for name in REQUIRED_FILES if not (project / name).exists()]
    if not (project / "app").is_dir():
        missing.append("app/")
    return missing


# --------------------------------------------------------------------------- #
# 2. venv sehat atau tidak
# --------------------------------------------------------------------------- #
def venv_healthy(python: Path | None = None, project: Path | None = None) -> tuple[bool, str]:
    """Cek venv benar-benar bisa dipakai (bukan hanya file python.exe ada)."""
    python = python or venv_python(project or ROOT)
    if python is None:
        return False, ".venv belum ada"
    code, out = run([str(python), "-c", "import sys; print(sys.version.split()[0])"], timeout=120)
    if code != 0:
        return False, (out.strip().splitlines() or ["python.exe di .venv tidak bisa jalan"])[-1][:160]
    if "Traceback" in out:
        return False, out.strip().splitlines()[-1][:160]
    code, out = run([str(python), "-c", "import fastapi, uvicorn"], timeout=180)
    if code != 0:
        last = (out.strip().splitlines() or [""])[-1]
        return False, f"dependensi tidak bisa di-import: {last[:120]}"
    return True, out.strip() or "OK"


def venv_origin(project: Path = ROOT) -> str:
    """Path asal venv (dari pyvenv.cfg) — untuk mendeteksi folder yang dipindah."""
    cfg = project / ".venv" / "pyvenv.cfg"
    try:
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().lower().startswith("command"):
                return line.split("=", 1)[-1].strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def venv_moved(project: Path = ROOT) -> bool:
    """True kalau pyvenv.cfg menunjukkan venv dibuat di folder lain."""
    origin = venv_origin(project)
    if not origin:
        return False
    normalized = lambda p: re.sub(r"[\\/]+", "/", p).strip('"').lower()  # noqa: E731
    return normalized(str(project)) not in normalized(origin)


def rebuild_venv(project: Path = ROOT) -> tuple[bool, str]:
    """Hapus .venv lalu buat ulang + pasang dependensi."""
    from app.fsutil import safe_rmtree

    target = project / ".venv"
    if target.exists():
        safe_rmtree(target)
        if target.exists():
            return False, (
                "Gagal menghapus .venv (mungkin dipakai proses lain).\n"
                "Tutup Clipper/service, lalu jalankan lagi."
            )
    launcher = sys.executable
    if not launcher:
        return False, "Python tidak ditemukan"
    code, out = run([launcher, "-m", "venv", str(target)], timeout=600)
    if code != 0:
        return False, f"gagal membuat venv: {out.strip()[-300:]}"
    python = venv_python(project)
    if python is None:
        return False, "venv dibuat tapi python.exe tidak ditemukan"
    run([str(python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"], timeout=900)
    code, out = run([str(python), "-m", "pip", "install", "-r", str(project / "requirements.txt")],
                    timeout=3600)
    if code != 0:
        return False, f"gagal memasang dependensi: {out.strip()[-400:]}"
    return True, f"venv baru siap: {python}"


# --------------------------------------------------------------------------- #
# 3. service & autostart yang menunjuk path lama
# --------------------------------------------------------------------------- #
def _sc(*args: str) -> tuple[int, str]:
    if not is_windows():
        return 127, "bukan Windows"
    return run(["sc.exe", *args], timeout=120)


def service_info() -> dict[str, Any]:
    """Status service + path binary-nya (untuk mendeteksi path lama)."""
    code, out = _sc("query", SERVICE_NAME)
    if code != 0 or "SERVICE_NAME" not in out.upper():
        return {"exists": False}
    _, qc = _sc("qc", SERVICE_NAME)
    bin_line = ""
    start_line = ""
    for line in qc.splitlines():
        upper = line.upper()
        if "BINARY_PATH_NAME" in upper:
            bin_line = line.split(":", 1)[-1].strip()
        elif "START_TYPE" in upper:
            start_line = line.split(":", 1)[-1].strip()
    start = "auto"
    if "DEMAND" in start_line.upper():
        start = "manual"
    elif "DISABLED" in start_line.upper():
        start = "disabled"
    return {"exists": True, "bin_path": bin_line, "start_type": start, "raw": qc.strip()[:400]}


def _extract_paths(bin_path: str) -> list[Path]:
    return [Path(p) for p in re.findall(r'"([^"]+)"', bin_path or "")]


def service_stale() -> bool:
    """True kalau service ada tapi menunjuk file yang sudah tidak ada."""
    info = service_info()
    if not info.get("exists"):
        return False
    paths = _extract_paths(info.get("bin_path", ""))
    if not paths:
        return True
    return any(not path.exists() for path in paths)


def repoint_service(project: Path = ROOT) -> tuple[bool, str]:
    """Pasang ulang service dengan path baru, tipe startup dipertahankan."""
    info = service_info()
    if not info.get("exists"):
        return False, "service belum terpasang"
    python = venv_python(project)
    if python is None:
        return False, "venv belum siap — perbaiki venv dulu"
    start = info.get("start_type", "auto")
    sc_start = {"auto": "auto", "manual": "demand", "disabled": "disabled"}[start]
    command = f'"{python}" "{project / "run.py"}" --run-service'

    if "RUNNING" in info.get("raw", "").upper():
        _sc("stop", SERVICE_NAME)
    _sc("delete", SERVICE_NAME)
    code, out = _sc("create", SERVICE_NAME, "binPath=", command, "start=", sc_start,
                    "DisplayName=", "Clipper - Auto Clip to Shorts", "obj=", "LocalSystem")
    if code != 0:
        return False, f"gagal memasang ulang service (butuh Administrator): {out.strip()[:200]}"
    _sc("description", SERVICE_NAME,
        "Clipper berjalan di latar belakang tanpa jendela cmd. Buka http://localhost:8420")
    if start != "disabled":
        _sc("failure", SERVICE_NAME, "reset=", "86400",
            "actions=", "restart/60000/restart/120000/restart/300000")
    return True, f"service diarahkan ke {command} (start={sc_start})"


def task_exists() -> bool:
    if not is_windows():
        return False
    code, _ = run(["schtasks", "/Query", "/TN", TASK_NAME], timeout=60)
    return code == 0


def task_stale() -> bool:
    """Autostart Task Scheduler menunjuk path yang sudah hilang?"""
    if not task_exists():
        return False
    code, out = run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"], timeout=90)
    if code != 0:
        return False
    for line in out.splitlines():
        if "task to run" in line.lower() or "tugas yang dijalankan" in line.lower():
            target = line.split(":", 1)[-1].strip().strip('"')
            return not Path(target).exists()
    return False


def repoint_task(project: Path = ROOT) -> tuple[bool, str]:
    """Buat ulang autostart Task Scheduler dengan path baru."""
    python = venv_python(project)
    if python is None:
        return False, "venv belum siap"
    pythonw = python.with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else python
    command = f'"{exe}" "{project / "run.py"}"'
    code, out = run(["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "ONLOGON",
                     "/TR", command, "/RL", "LIMITED"], timeout=120)
    if code != 0:
        return False, f"gagal memperbarui autostart: {out.strip()[:200]}"
    return True, f"autostart diperbarui: {command}"


# --------------------------------------------------------------------------- #
# 3b. path lama di database & settings (folder dipindah)
# --------------------------------------------------------------------------- #
PATH_COLUMNS = {
    "jobs": ("source_path",),
    "clips": ("file", "thumb"),
}


def _db_path(project: Path = ROOT) -> Path:
    data = Path(os.environ.get("CLIPPER_DATA_DIR") or (project / "data"))
    return data / "clipper.db"


def stored_paths(project: Path = ROOT, with_column: bool = False) -> list:
    """Semua path absolut yang tersimpan di DB.

    `with_column=True` mengembalikan `(tabel, kolom, path)` agar pemanggil bisa
    membedakan media utama (video) dari thumbnail.
    """
    import sqlite3

    db_file = _db_path(project)
    if not db_file.exists():
        return []
    found: list = []
    try:
        conn = sqlite3.connect(str(db_file))
        try:
            for table, columns in PATH_COLUMNS.items():
                for column in columns:
                    try:
                        rows = conn.execute(
                            f"SELECT {column} FROM {table} WHERE {column} != ''"
                        ).fetchall()
                    except sqlite3.Error:
                        continue
                    for row in rows:
                        if row and row[0]:
                            found.append((table, column, str(row[0])) if with_column else str(row[0]))
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
    return found


def data_folder_report(project: Path = ROOT) -> dict[str, Any]:
    """Ringkasan folder data yang dipakai Clipper + berapa path di DB yang rusak.

    `broken_paths`    : semua path tersimpan yang filenya tidak ada di lokasi itu
    `broken_media`    : dari jumlah itu, yang berupa video (paling penting)
    `resolvable_paths`: yang filenya ternyata ADA di folder data sekarang
                        (artinya tinggal path-nya dirapikan, bukan filenya hilang)
    """
    from app.paths import resolve_media_path

    data_dir = Path(os.environ.get("CLIPPER_DATA_DIR") or (project / "data"))
    rows = stored_paths(project, with_column=True)
    stored = [item[2] for item in rows]
    broken_rows = [item for item in rows if not Path(item[2]).expanduser().exists()]
    broken_media = sum(1 for table, column, _ in broken_rows if column != "thumb")
    resolvable = 0
    for _, _, value in broken_rows[:60]:
        if resolve_media_path(value, (data_dir / "work",)) is not None:
            resolvable += 1
    return {
        "data_dir": str(data_dir),
        "exists": data_dir.exists(),
        "env_override": bool(os.environ.get("CLIPPER_DATA_DIR")),
        "stored_paths": len(stored),
        "broken_paths": len(broken_rows),
        "broken_media": broken_media,
        "resolvable_paths": resolvable,
        "sample_broken": [item[2] for item in broken_rows[:3]],
    }


def relocate_paths(project: Path = ROOT) -> tuple[bool, str]:
    """Tulis ulang path absolut lama di database ke folder data sekarang.

    Database menyimpan path absolut (`C:\\Clipper\\data\\clips\\x.mp4`). Setelah
    folder dipindah, path itu tidak ada walau file-nya ikut pindah. Fungsi ini
    mengganti prefix lama dengan folder data yang sekarang.
    """
    import sqlite3

    from app.paths import old_roots_from_paths, relocate_string

    data_dir = Path(os.environ.get("CLIPPER_DATA_DIR") or (project / "data"))
    stored = stored_paths(project)
    roots = old_roots_from_paths(stored, data_dir)
    if not roots:
        return True, "path di database sudah menunjuk folder benar"

    db_file = _db_path(project)
    changed = 0
    try:
        conn = sqlite3.connect(str(db_file))
        try:
            for table, columns in PATH_COLUMNS.items():
                for column in columns:
                    rows = conn.execute(
                        f"SELECT rowid, {column} FROM {table} WHERE {column} != ''"
                    ).fetchall()
                    for rowid, value in rows:
                        new_value = relocate_string(str(value), roots, data_dir)
                        if new_value != value:
                            conn.execute(
                                f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (new_value, rowid)
                            )
                            changed += 1
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"gagal memperbarui database: {exc}"

    if not changed:
        return False, (f"path lama terdeteksi ({', '.join(roots[:3])}) tapi tidak ada yang "
                       f"bisa ditulis ulang otomatis")
    return True, (f"path di database diperbarui: {changed} baris "
                  f"(root lama: {', '.join(roots[:3])})")


# --------------------------------------------------------------------------- #
# 4. orkestrasi
# --------------------------------------------------------------------------- #
def detect(project: Path = ROOT) -> dict[str, Any]:
    """Ringkasan masalah tanpa memperbaiki apa pun."""
    missing = check_files(project)
    python = venv_python(project)
    healthy, reason = venv_healthy(python, project)
    return {
        "project": str(project),
        "missing_files": missing,
        "venv_exists": python is not None,
        "venv_healthy": healthy,
        "venv_reason": reason,
        "venv_origin": venv_origin(project),
        "venv_moved": venv_moved(project),
        "service": service_info(),
        "service_stale": service_stale(),
        "task_exists": task_exists(),
        "task_stale": task_stale(),
        "needs_repair": bool(missing or not healthy or service_stale() or task_stale()),
        "data": data_folder_report(project),
    }


def repair(project: Path = ROOT, on_step: Callable[[str], None] | None = None,
           reinstall_service: bool = True) -> dict[str, Any]:
    """Perbaiki semuanya: file → venv → service → autostart."""
    say = on_step or log
    report: dict[str, Any] = {"steps": [], "ok": True}

    def step(message: str, success: bool = True) -> None:
        report["steps"].append((message, success))
        say(("  [OK]  " if success else "  [!]   ") + message)
        if not success:
            report["ok"] = False

    # 1. file project
    missing = check_files(project)
    if missing:
        step("file penting hilang: " + ", ".join(missing), False)
        say("        Folder tampaknya belum tersalin lengkap (atau diblokir antivirus).")
        say("        Salin ulang folder Clipper, atau jalankan: git clone <repo> Clipper")
        return report
    step(f"folder project lengkap: {project}")

    # 2. venv
    python = venv_python(project)
    healthy, reason = venv_healthy(python, project)
    if healthy:
        step("venv sehat")
    else:
        moved = venv_moved(project)
        say(f"        penyebab: {reason}" + (" (venv dibuat di folder lama)" if moved else ""))
        if python is not None:
            step("membangun ulang .venv (path lama tidak valid)")
        else:
            step(".venv belum ada — membuat baru")
        ok, message = rebuild_venv(project)
        step(message, ok)
        if not ok:
            return report

    # 3. service Windows
    info = service_info()
    if not info.get("exists"):
        say("        service: belum terpasang (dilewati)")
    elif not reinstall_service:
        say("        service: dilewati")
    else:
        if service_stale():
            ok, message = repoint_service(project)
            step(message, ok)
        else:
            step("service sudah menunjuk folder yang benar")
        say(f"        tipe startup: {info.get('start_type')}")

    # 4. autostart Task Scheduler
    if task_exists():
        if task_stale():
            ok, message = repoint_task(project)
            step(message, ok)
        else:
            step("autostart sudah menunjuk folder yang benar")

    # 5. folder data + path lama di database
    try:
        from app.config import ensure_dirs

        ensure_dirs()
        info_data = data_folder_report(project)
        step(f"folder data: {info_data['data_dir']}")
        if info_data["env_override"]:
            say("        (dipaksa oleh variabel lingkungan CLIPPER_DATA_DIR)")
        if info_data["broken_paths"]:
            say(f"        {info_data['broken_paths']} dari {info_data['stored_paths']} path "
                f"di database menunjuk lokasi lama → diperbarui")
        ok_relocate, message = relocate_paths(project)
        step(message, ok_relocate)
        after = data_folder_report(project)
        if after["broken_media"]:
            contoh = Path(after["sample_broken"][0]).name if after["sample_broken"] else "-"
            step(f"{after['broken_media']} file video masih tidak ditemukan (contoh: {contoh})", False)
            if after["resolvable_paths"]:
                say(f"        {after['resolvable_paths']} file ada di folder data tapi path-nya "
                    f"belum bisa dirapikan — jalankan repair.bat sekali lagi")
            else:
                say("        Cek `dir \"%s\"` — pastikan isinya ikut tersalin ke drive baru."
                    % after["data_dir"])
        elif after["broken_paths"]:
            step(f"{after['broken_paths']} thumbnail hilang (tidak masalah, dibuat ulang saat render)",
                 True)
    except Exception as exc:  # noqa: BLE001
        step(f"folder data bermasalah: {exc}", False)

    return report


def save_last_report(report: dict[str, Any], project: Path = ROOT) -> None:
    try:
        data = Path(os.environ.get("CLIPPER_DATA_DIR") or (project / "data"))
        data.mkdir(parents=True, exist_ok=True)
        (data / "last_repair.json").write_text(
            json.dumps({"project": str(project),
                        "steps": [{"message": m, "ok": ok} for m, ok in report.get("steps", [])]},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass
