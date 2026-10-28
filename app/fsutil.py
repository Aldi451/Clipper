"""Util file-system yang aman di Windows.

Windows mengunci file selama masih ada handle terbuka: antivirus, browser yang sedang
memutar video, atau proses ffmpeg yang baru selesai sering membuat `unlink`/`rmtree`
gagal sesaat dengan `PermissionError: [WinError 32]`. Helper di sini mencoba ulang
beberapa kali sebelum menyerah, supaya job tidak ikut gagal hanya karena file terkunci.
"""
from __future__ import annotations

import gc
import os
import stat
import time
from pathlib import Path

DEFAULT_RETRIES = 8
DEFAULT_DELAY = 0.2


def _force_writable(path: Path) -> None:
    try:
        if path.exists() and not os.access(path, os.W_OK):
            os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass


def safe_unlink(path: str | Path, retries: int = DEFAULT_RETRIES, delay: float = DEFAULT_DELAY) -> bool:
    """Hapus file; toleran terhadap file yang masih terkunci (Windows).

    Return True kalau file sudah tidak ada lagi.
    """
    target = Path(path)
    for attempt in range(max(1, retries)):
        try:
            target.unlink(missing_ok=True)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == 0:
                gc.collect()  # lepas handle Python yang mungkin masih menggantung
            if attempt == retries - 1:
                return False
            time.sleep(delay * (attempt + 1))
    return not target.exists()


def safe_rmtree(path: str | Path, retries: int = DEFAULT_RETRIES, delay: float = DEFAULT_DELAY) -> bool:
    """Hapus folder rekursif; toleran terhadap file terkunci (Windows)."""
    import shutil

    target = Path(path)
    if not target.exists():
        return True
    for attempt in range(max(1, retries)):
        try:
            shutil.rmtree(target, ignore_errors=False)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            # coba lepas atribut read-only lalu ulangi
            for child in target.rglob("*"):
                _force_writable(child)
            _force_writable(target)
            if attempt == retries - 1:
                shutil.rmtree(target, ignore_errors=True)
                return not target.exists()
            time.sleep(delay * (attempt + 1))
    return not target.exists()


def safe_replace(src: str | Path, dst: str | Path) -> bool:
    """Pindahkan/rename file dengan fallback copy kalau tujuan masih terkunci."""
    import shutil

    source, target = Path(src), Path(dst)
    for attempt in range(4):
        try:
            os.replace(source, target)
            return True
        except OSError:
            if attempt == 3:
                break
            time.sleep(DEFAULT_DELAY * (attempt + 1))
    try:
        shutil.copyfile(source, target)
        return True
    except OSError:
        return False


def temp_output_path(suffix: str = "", prefix: str = "clipper_") -> Path:
    """Path file sementara yang handle-nya sudah ditutup.

    `tempfile.mkstemp()` mengembalikan file descriptor; kalau fd itu tidak ditutup,
    Windows menganggap file masih dipakai sehingga proses lain (ffmpeg) gagal menulis
    dan penghapusan gagal dengan `WinError 32`.
    """
    import tempfile

    fd, name = tempfile.mkstemp(suffix=suffix, prefix=prefix)
    os.close(fd)
    return Path(name)
