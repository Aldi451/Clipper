"""Resolusi path media yang tahan terhadap folder project yang dipindah.

Database menyimpan **path absolut** (mis. `C:\\Clipper\\data\\clips\\clip_x.mp4`).
Kalau folder Clipper dipindah ke drive lain, path lama tidak ada lagi walau file-nya
sebenarnya ikut pindah. Modul ini mencarinya kembali berdasarkan nama file di folder
data yang sekarang, sehingga aplikasi tetap "melihat" klip lama tanpa perlu repair.

Kalau file benar-benar tidak ditemukan, dikembalikan None supaya pemanggil bisa
memberi pesan yang jelas (bukan menampilkan halaman kosong).
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import ASSET_DIR, CLIP_DIR, DATA_DIR, MODEL_DIR, UPLOAD_DIR, WORK_DIR

# folder yang mungkin menyimpan file yang dirujuk (urut dari paling sering)
SEARCH_DIRS: tuple[Path, ...] = (CLIP_DIR, UPLOAD_DIR, WORK_DIR, ASSET_DIR, DATA_DIR)


def resolve_media_path(value: str | Path | None, extra_dirs: tuple[Path, ...] = ()) -> Path | None:
    """Kembalikan path yang benar-benar ada, atau None.

    Urutan: path apa adanya → folder data saat ini (cocok nama file) →
    `extra_dirs` (mis. folder kerja job tertentu).
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    candidate = Path(raw)
    try:
        if candidate.exists():
            return candidate
    except OSError:
        pass

    # path bisa berformat Windows (`C:\\...`) walau dijalankan di sistem lain:
    # ambil nama file dengan menormalkan kedua jenis pemisah.
    name = raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if not name:
        return None
    for folder in (*SEARCH_DIRS, *extra_dirs):
        try:
            found = folder / name
            if found.exists():
                return found
        except OSError:
            continue
        # file clip disimpan di subfolder per job pada sebagian versi
        try:
            for found in folder.glob(f"*/{name}"):
                if found.exists():
                    return found
        except OSError:
            continue
    return None


def media_exists(value: str | Path | None, extra_dirs: tuple[Path, ...] = ()) -> bool:
    return resolve_media_path(value, extra_dirs) is not None


def relocate_string(value: str, old_roots: list[str], new_root: Path) -> str:
    """Ganti prefix path lama dengan root data yang sekarang (untuk repair DB)."""
    if not value:
        return value
    text = value
    normalized = text.replace("\\", "/").lower()
    for old in old_roots:
        if not old:
            continue
        root = old.replace("\\", "/").rstrip("/")
        if not root:
            continue
        if normalized.startswith(root.lower() + "/") or normalized == root.lower():
            rest = text.replace("\\", "/")[len(root):].lstrip("/")
            # buang segmen "data" yang dobel: C:\Clipper\data + data\clips → data/clips
            parts = [p for p in rest.replace("\\", "/").split("/") if p]
            if parts and parts[0].lower() == new_root.name.lower():
                parts = parts[1:]
            return str(new_root.joinpath(*parts)) if parts else str(new_root)
    return value


def old_roots_from_paths(values: list[str], current_root: Path) -> list[str]:
    """Tebak root lama dari path yang tersimpan (semua yang sebelum folder 'data')."""
    roots: list[str] = []
    current = str(current_root).replace("\\", "/").lower().rstrip("/")
    also = current.rsplit("/", 1)[0] if "/" in current else current
    # penanda folder yang selalu ada di dalam folder data
    markers = ("/data/", "/clips/", "/uploads/", "/work/", "/assets/")
    for raw in values:
        if not raw:
            continue
        normalized = str(raw).replace("\\", "/")
        lowered = normalized.lower()
        indexes = [lowered.find(m) for m in markers if lowered.find(m) > 0]
        if not indexes:
            continue
        root = normalized[: min(indexes)]
        if not root:
            continue
        if root.lower().rstrip("/") in (current, also):
            continue
        if root not in roots:
            roots.append(root)
    return roots


def data_summary() -> dict[str, object]:
    """Ringkasan isi folder data (dipakai /api/health & --doctor)."""
    def count(folder: Path, pattern: str) -> int:
        try:
            return sum(1 for _ in folder.glob(pattern))
        except OSError:
            return 0

    return {
        "data_dir": str(DATA_DIR),
        "env_override": bool(os.environ.get("CLIPPER_DATA_DIR")),
        "uploads": count(UPLOAD_DIR, "*"),
        "clips": count(CLIP_DIR, "*.mp4"),
        "models": count(MODEL_DIR, "**/model.bin") if MODEL_DIR.exists() else 0,
    }
