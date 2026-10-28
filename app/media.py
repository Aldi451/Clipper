"""Helper pemanggilan ffmpeg: probe media, ekstrak audio, energy, run dengan progress."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .config import ffmpeg_exe, ffprobe_exe
from .fsutil import safe_rmtree

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+).*?, (\d{2,5})x(\d{2,5})")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*?: Audio: (\w+).*? (\d+) Hz, (\w+)")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")


class FFmpegError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    sample_rate: int = 0
    has_audio: bool = False
    has_video: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width


def _run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def probe(path: str | Path) -> MediaInfo:
    """Ambil metadata media. Pakai ffprobe kalau ada, kalau tidak parse stderr ffmpeg."""
    path = str(path)
    probe_bin = ffprobe_exe()
    if probe_bin:
        proc = _run(
            [
                probe_bin, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", path,
            ],
            timeout=120,
        )
        if proc.returncode == 0:
            try:
                data = json.loads(proc.stdout.decode("utf-8", "replace"))
                return _info_from_ffprobe(data)
            except Exception:
                pass

    # fallback: parse output `ffmpeg -i`
    proc = _run([ffmpeg_exe(), "-hide_banner", "-i", path], timeout=120)
    text = proc.stderr.decode("utf-8", "replace")
    info = MediaInfo(raw={"stderr": text[-4000:]})
    m = _DUR_RE.search(text)
    if m:
        h, mi, s = m.groups()
        info.duration = int(h) * 3600 + int(mi) * 60 + float(s)
    vm = _VIDEO_RE.search(text)
    if vm:
        info.has_video = True
        info.video_codec = vm.group(1)
        info.width, info.height = int(vm.group(2)), int(vm.group(3))
        fm = _FPS_RE.search(text)
        if fm:
            info.fps = float(fm.group(1))
    am = _AUDIO_RE.search(text)
    if am:
        info.has_audio = True
        info.audio_codec = am.group(1)
        info.sample_rate = int(am.group(2))
    if not info.duration and proc.returncode != 0 and "No such file" in text:
        raise FFmpegError(f"File tidak bisa dibaca: {path}")
    return info


def _info_from_ffprobe(data: dict) -> MediaInfo:
    info = MediaInfo(raw={})
    fmt = data.get("format") or {}
    try:
        info.duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        info.duration = 0.0
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and not info.has_video:
            info.has_video = True
            info.video_codec = stream.get("codec_name") or ""
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            try:
                info.fps = eval(stream.get("avg_frame_rate") or "0/1") or 0.0  # noqa: S307
            except Exception:
                info.fps = 0.0
            if not info.duration and stream.get("duration"):
                try:
                    info.duration = float(stream["duration"])
                except (TypeError, ValueError):
                    pass
        elif stream.get("codec_type") == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = stream.get("codec_name") or ""
            info.sample_rate = int(stream.get("sample_rate") or 0)
    return info


def extract_audio(src: str | Path, dst: str | Path, sample_rate: int = 16000) -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", str(dst),
    ]
    proc = _run(cmd)
    if proc.returncode != 0 or not dst.exists():
        raise FFmpegError(proc.stderr.decode("utf-8", "replace")[-1500:])
    return dst


def audio_energy(src: str | Path, window: float = 0.5) -> list[tuple[float, float]]:
    """RMS level (dBFS) per window detik, dipakai untuk deteksi bagian ramai/hening."""
    samples = max(400, int(16000 * window))
    result: list[tuple[float, float]] = []
    # Catatan Windows: jangan pakai fd dari mkstemp() — handle yang masih terbuka
    # membuat ffmpeg gagal menulis dan `unlink` gagal dengan WinError 32.
    tempdir = Path(tempfile.mkdtemp(prefix="clipper_energy_"))
    tmp = tempdir / "astats.txt"
    text = ""
    try:
        cmd = [
            ffmpeg_exe(), "-hide_banner", "-nostats", "-i", str(src), "-vn",
            "-af", f"asetnsamples={samples},astats=metadata=1:reset=1,ametadata=print:file={tmp.as_posix()}",
            "-f", "null", "-",
        ]
        _run(cmd)
        if tmp.exists():
            text = tmp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    finally:
        safe_rmtree(tempdir)
    # Satu frame astats mencetak RMS per channel DAN "Overall". Pakai Overall saja
    # supaya timeline tidak dobel; kalau tidak ada, ambil channel pertama.
    current: float | None = None
    overall: list[tuple[float, float]] = []
    per_channel = result

    def _value(raw: str) -> float | None:
        try:
            value = float(raw)
        except ValueError:
            return None
        if value != value or value <= -120:  # NaN / senyap total
            return -90.0
        return value

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([0-9.]+)", line)
            current = float(m.group(1)) if m else None
        elif "RMS_level=" in line and current is not None:
            value = _value(line.split("=", 1)[1])
            if value is None:
                continue
            if ".Overall.RMS_level=" in line:
                overall.append((current, value))
            else:
                per_channel.append((current, value))
    return overall or per_channel


def run_with_progress(
    cmd: list[str],
    total_seconds: float,
    on_progress: Callable[[float], None] | None = None,
    cwd: str | Path | None = None,
    timeout: float | None = None,
) -> None:
    """Jalankan ffmpeg dengan `-progress pipe:1` dan laporkan persentase."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        text=True,
        bufsize=1,
    )
    tail: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms=") and on_progress and total_seconds > 0:
                try:
                    done = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                on_progress(max(0.0, min(1.0, done / total_seconds)))
        proc.wait(timeout=timeout)
    finally:
        if proc.stderr is not None:
            tail.append(proc.stderr.read() or "")
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        raise FFmpegError(("".join(tail))[-2000:] or "ffmpeg gagal")


def thumb(src: str | Path, at: float, dst: str | Path, width: int = 540) -> Path:
    dst = Path(dst)
    cmd = [
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, at):.3f}", "-i", str(src), "-frames:v", "1",
        "-vf", f"scale={width}:-2", "-q:v", "3", str(dst),
    ]
    _run(cmd)
    return dst


def concat_quote(path: Path) -> str:
    return "file '" + str(path).replace("'", "'\\''") + "'"


def iter_files(paths: Iterable[Path]) -> list[Path]:
    return [p for p in paths if p.exists()]
