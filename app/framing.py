"""Auto-framing podcast: kamera mengikuti pembicara yang sedang tampil.

Masalahnya: dengan layout **crop** (klip vertikal 9:16 dari video 16:9), crop
lama memakai satu titik fokus tetap di tengah. Kalau dua orang duduk berdampingan
— situasi paling umum di podcast — pembicara yang di kiri/kanan sering terpotong,
sementara bagian tengah (meja, kursi kosong) yang justru ditampilkan.

Solusi di modul ini:
1. **Deteksi wajah lokal** memakai Haar cascade bawaan OpenCV (tanpa model
   unduhan tambahan, tanpa API, jalan offline).
2. Setiap ~0,4 detik posisi wajah dicatat, lalu dipilih **wajah dominan** (paling
   besar, diutamakan dekat posisi kamera sebelumnya supaya tidak lompat-lompat).
3. Sampel dihaluskan (median 3 + *deadband*) lalu dikelompokkan menjadi
   **segmen** pendek: "dari detik 0–2,4 fokus di 0,32; 2,4–5,1 fokus di 0,61".
4. Segmen diterjemahkan menjadi **ekspresi `crop` ffmpeg berbasis waktu** dengan
   transisi pan halus (±0,45 dtk), jadi kamera bergeser seperti operator, bukan
   berpindah mendadak.

Prinsip aman:
* Kalau wajah terdeteksi kurang dari 35% sampel (video tanpa wajah, animasi,
  screen recording), hasilnya ``ok=False`` dan pemanggil kembali ke fokus tetap.
* Tidak ada berkas sumber yang diubah; tidak ada unggahan ke mana pun.
* Fungsi inti (perencanaan segmen & ekspresi) murni perhitungan, sehingga bisa
  diuji tanpa video dan tanpa kamera.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Iterable

from . import media
from .config import CLIP_MAX_SECONDS

# Rentang fokus yang aman: kamera tidak pernah mentok total ke tepi frame
FOCUS_MIN = 0.02
FOCUS_MAX = 0.98
# Kemiringan pan (detik) saat kamera pindah pembicara
PAN_SECONDS = 0.45
# Perubahan fokus di bawah ini diabaikan (mencegah kamera "gemetar")
DEADBAND = 0.07
# Segmen lebih pendek dari ini digabung ke tetangganya
MIN_SEGMENT = 1.1


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


# --------------------------------------------------------------------------- #
# ketersediaan deteksi
# --------------------------------------------------------------------------- #
def _cv() -> Any | None:
    """Impor OpenCV secara malas (kalau tidak ada, fitur ini mati dengan sopan)."""
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:  # noqa: BLE001
        return None


def available() -> bool:
    cv2 = _cv()
    if cv2 is None or not hasattr(cv2, "CascadeClassifier"):
        return False
    try:
        return not _cascade().empty()
    except Exception:  # noqa: BLE001
        return False


_CASCADE_CACHE: dict[str, Any] = {}


def _cascade() -> Any:
    cv2 = _cv()
    if cv2 is None:
        raise RuntimeError("OpenCV tidak terpasang")
    if "front" not in _CASCADE_CACHE:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _CASCADE_CACHE["front"] = cv2.CascadeClassifier(path)
    return _CASCADE_CACHE["front"]


# --------------------------------------------------------------------------- #
# perencanaan fokus (murni, bisa diuji tanpa video)
# --------------------------------------------------------------------------- #
def focus_from_face(face_center: float, source_w: int, source_h: int,
                    target_ratio: float = 9 / 16) -> float:
    """Ubah posisi tengah wajah (piksel) menjadi rasio fokus crop 0..1.

    Saat layout *crop*, video diperbesar sampai menutup kanvas lalu dipotong
    selebar ``source_h * 9/16``. Rasio 0 = crop menempel kiri, 1 = menempel kanan.
    """
    if source_w <= 0 or source_h <= 0:
        return 0.5
    crop_w = source_h * target_ratio
    if crop_w >= source_w:  # sumber sudah lebih sempit dari 9:16 → tidak ada ruang geser
        return 0.5
    span = source_w - crop_w
    return _clamp((face_center - crop_w / 2.0) / span, FOCUS_MIN, FOCUS_MAX)


def smooth_focus(values: Iterable[float], window: int = 3) -> list[float]:
    """Median filter sederhana — membuang lonjakan satu-dari-sekian frame."""
    data = [_clamp(_f(v, 0.5)) for v in values]
    if len(data) <= 2 or window < 3:
        return data
    half = window // 2
    out: list[float] = []
    for i in range(len(data)):
        chunk = sorted(data[max(0, i - half): min(len(data), i + half + 1)])
        out.append(chunk[len(chunk) // 2])
    return out


def plan_segments(
    samples: list[dict[str, Any]],
    deadband: float = DEADBAND,
    min_segment: float = MIN_SEGMENT,
) -> list[dict[str, Any]]:
    """Kelompokkan sampel fokus menjadi segmen kamera yang stabil.

    ``samples`` = ``[{"at": detik, "x": 0..1}]`` (sudah relatif ke awal klip).
    Kembalian: ``[{"at": 0.0, "x": 0.32}, {"at": 2.4, "x": 0.61}, ...]``.
    """
    if not samples:
        return []
    order = sorted(samples, key=lambda item: _f(item.get("at")))
    times = [_f(item.get("at")) for item in order]
    values = smooth_focus([_f(item.get("x"), 0.5) for item in order])

    segments: list[dict[str, Any]] = [{"at": round(max(0.0, times[0]), 3), "x": round(values[0], 4)}]
    for time_value, focus in zip(times[1:], values[1:]):
        last = segments[-1]
        if abs(focus - last["x"]) > deadband and (time_value - last["at"]) >= min_segment:
            segments.append({"at": round(time_value, 3), "x": round(focus, 4)})
        else:
            # tetap perbarui dengan rata-rata bergerak supaya halus, tanpa segmen baru
            shift = min(0.35, 0.5 / max(1.0, len(segments)))
            last["x"] = round(_clamp(last["x"] * (1 - shift) + focus * shift), 4)

    # gabungkan segmen yang terlalu pendek (mis. satu kata "eee" bikin kamera lari)
    pruned: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        is_last = index == len(segments) - 1
        length = (_f(segments[index + 1]["at"]) - segment["at"]) if not is_last else 1e9
        if length < min_segment and pruned:
            continue
        pruned.append(segment)
    return pruned


def crop_expression(segments: list[dict[str, Any]], pan: float = PAN_SECONDS,
                    default: float = 0.5) -> str:
    """Ekspresi ``x=`` untuk filter crop ffmpeg, berpindah halus antar segmen.

    Bentuknya: di dalam segmen kamera **diam** di posisi segmen itu, lalu selama
    ``pan`` detik setelah pergantian kamera bergeser mulus ke posisi berikutnya::

        if(lt(t,6.45),
            if(lt(t,6.00), 0.1457, 0.1457+(0.7086)*((t-6.00)/0.45)),
            0.8543)

    Catatan penting (bug yang pernah terjadi): nilai segmen *berikutnya* tidak
    boleh dipakai sebelum waktunya. Karena itu rantainya disusun dari segmen
    terakhir mundur ke depan, dan setiap blok hanya berlaku sampai
    ``waktu_segmen_berikutnya + pan``.
    """
    items = [seg for seg in (segments or []) if _f(seg.get("at"), -1) >= 0]
    if not items:
        return f"{_clamp(default):.4f}"
    items = sorted(items, key=lambda seg: _f(seg.get("at")))
    if len(items) == 1:
        return f"{_clamp(_f(items[0].get('x'), default)):.4f}"

    times = [_f(seg.get("at")) for seg in items]
    values = [_clamp(_f(seg.get("x"), default)) for seg in items]

    expr = f"{values[-1]:.4f}"
    for index in range(len(items) - 2, -1, -1):
        current = values[index]
        following = values[index + 1]
        change_at = times[index + 1]
        delta = following - current
        ramp = (
            f"{current:.4f}"
            if abs(delta) < 1e-4
            else f"({current:.4f}+({delta:.4f})*((t-{change_at:.3f})/{pan:.3f}))"
        )
        expr = (
            f"if(lt(t\,{change_at + pan:.3f})\,"
            f"if(lt(t\,{change_at:.3f})\,{current:.4f}\,{ramp})\,"
            f"{expr})"
        )
    return expr


def crop_x_filter(segments: list[dict[str, Any]], width: int, measure: str = "(iw-{w})",
                  default: float = 0.5) -> str:
    """Potongan ``x='…'`` siap tempel untuk filter crop."""
    expression = crop_expression(segments, default=default)
    if "t" not in expression:
        return f"x='{measure.format(w=width)}*{expression}'"
    return f"x='{measure.format(w=width)}*{expression}'"


def shift_segments(segments: list[dict[str, Any]], start: float, duration: float) -> list[dict[str, Any]]:
    """Ambil segmen yang beririsan dengan [start, start+duration] dan geser t=0.

    Render memotong sumber lebih dulu (``-ss start``), jadi waktu di filter ffmpeg
    dimulai dari nol pada segmen klip.
    """
    if not segments:
        return []
    end = start + duration
    picked: list[dict[str, Any]] = []
    previous = None
    for segment in sorted(segments, key=lambda seg: _f(seg.get("at"))):
        at = _f(segment.get("at"))
        if at <= start:
            previous = segment
            continue
        if at >= end:
            break
        picked.append({"at": round(at - start, 3), "x": round(_f(segment.get("x"), 0.5), 4)})
    if previous is not None:
        picked.insert(0, {"at": 0.0, "x": round(_f(previous.get("x"), 0.5), 4)})
    elif picked and picked[0]["at"] > 0.35:
        picked.insert(0, {"at": 0.0, "x": picked[0]["x"]})
    return picked


# --------------------------------------------------------------------------- #
# deteksi pada video
# --------------------------------------------------------------------------- #
def _read_frames(path: Path, start: float, duration: float, step: float,
                 max_width: int = 640) -> list[tuple[float, Any]]:
    """Ambil frame pada interval ``step`` detik (relatif ke awal klip)."""
    cv2 = _cv()
    if cv2 is None:
        return []
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return []
    frames: list[tuple[float, Any]] = []
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start) * 1000.0)
        offset = 0.0
        while offset <= duration + 1e-6:
            ok, frame = capture.read()
            if not ok:
                break
            if frame is not None and frame.size:
                height, width = frame.shape[:2]
                if width > max_width:
                    scale = max_width / float(width)
                    frame = cv2.resize(frame, (max_width, int(height * scale)))
                frames.append((round(offset, 3), frame))
            offset += step
            # lompat langsung ke posisi berikutnya (jauh lebih cepat dari membaca semua frame)
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + offset) * 1000.0)
    finally:
        capture.release()
    return frames


def detect_faces(cv2: Any, frame: Any, cascade: Any) -> list[tuple[float, float, float]]:
    """Deteksi wajah pada satu frame → ``[(center_x, center_y, lebar)]`` piksel.

    Dipakai beberapa tingkat ketat: Haar yang terlalu ketat sering melewatkan
    wajah yang agak miring / setengah profil, sedangkan yang terlalu longgar
    memunculkan "wajah palsu" di kursi dan lampu. Jadi: coba ketat dulu, kalau
    tidak ada hasil baru dilonggarkan.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    min_size = max(28, int(frame.shape[1] * 0.05))
    # (gambar, skala, minNeighbors) — coba yang paling ketat dulu.
    # equalizeHist membantu video gelap, tapi bisa juga menghilangkan wajah yang
    # sudah kontras, jadi keduanya dicoba.
    candidates = (
        (gray, 1.1, 6), (gray, 1.1, 4), (gray, 1.05, 3),
        (cv2.equalizeHist(gray), 1.1, 4),
    )
    for source, scale, neighbors in candidates:
        boxes = cascade.detectMultiScale(
            source, scaleFactor=scale, minNeighbors=neighbors, minSize=(min_size, min_size)
        )
        if len(boxes):
            return [(x + w / 2.0, y + h / 2.0, float(w)) for (x, y, w, h) in boxes]
    return []


def analyze(
    source_path: str | Path,
    start: float = 0.0,
    duration: float | None = None,
    sample_step: float = 0.4,
    on_progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Analisa satu klip → segmen fokus kamera.

    Kembalian::

        {"ok": True, "faces": 12, "coverage": 0.83, "moves": 2,
         "segments": [{"at": 0.0, "x": 0.31}, ...], "reason": ""}
    """
    progress = on_progress or (lambda fraction, message: None)
    cv2 = _cv()
    path = Path(source_path)
    if not path.exists():
        return {"ok": False, "reason": "File sumber tidak ditemukan", "segments": []}
    if cv2 is None or not available():
        return {"ok": False, "reason": "OpenCV belum terpasang (deteksi wajah nonaktif)", "segments": []}

    info = media.probe(path)
    length = _f(duration, 0.0) or _f(info.duration, 0.0)
    length = min(length, float(CLIP_MAX_SECONDS) + 1.0)
    if length <= 0:
        return {"ok": False, "reason": "Durasi klip tidak diketahui", "segments": []}

    cascade = _cascade()
    progress(0.05, "Membaca frame video…")
    frames = _read_frames(path, max(0.0, _f(start)), length, sample_step)
    if not frames:
        return {"ok": False, "reason": "Video tidak bisa dibaca", "segments": []}

    source_w = int(info.width or 0) or int(frames[0][1].shape[1])
    source_h = int(info.height or 0) or int(frames[0][1].shape[0])
    scale_x = source_w / float(frames[0][1].shape[1])

    samples: list[dict[str, Any]] = []
    detected = 0
    previous_x = 0.5
    for index, (offset, frame) in enumerate(frames):
        faces = detect_faces(cv2, frame, cascade)
        if faces:
            detected += 1
            # wajah dominan: paling besar, tapi diberi bonus kalau dekat posisi kamera sekarang
            def score(face: tuple[float, float, float]) -> float:
                center_x, _center_y, width = face
                norm = _clamp((center_x * scale_x) / max(1.0, source_w))
                return width * (1.35 if abs(norm - previous_x) < 0.22 else 1.0)

            best = max(faces, key=score)
            focus = focus_from_face(best[0] * scale_x, source_w, source_h)
            previous_x = focus
            samples.append({"at": offset, "x": round(focus, 4)})
        if index % 25 == 0:
            progress(0.05 + 0.8 * index / max(1, len(frames)),
                     f"Mendeteksi wajah {index}/{len(frames)}…")

    coverage = detected / max(1, len(frames))
    progress(0.9, "Menyusun pergerakan kamera…")
    segments = plan_segments(samples)
    moves = max(0, len(segments) - 1)
    result = {
        "ok": bool(segments) and coverage >= 0.35 and len(segments) > 0,
        "faces": detected,
        "coverage": round(coverage, 3),
        "segments": segments,
        "moves": moves,
        "start": round(max(0.0, _f(start)), 3),
        "duration": round(length, 3),
        "source_size": [source_w, source_h],
        "reason": "",
    }
    if not segments:
        result["reason"] = "Tidak ada wajah yang terdeteksi — kamera tetap di tengah"
    elif coverage < 0.35:
        result["reason"] = (
            f"Wajah hanya terdeteksi di {coverage * 100:.0f}% frame — kamera tetap di tengah"
        )
    progress(1.0, "Analisa pembicara selesai")
    return result


def summary(result: dict[str, Any] | None) -> str:
    """Kalimat singkat untuk ditampilkan di UI."""
    if not result:
        return "Belum dianalisa"
    if not result.get("ok"):
        return result.get("reason") or "Kamera tetap di tengah"
    moves = int(result.get("moves") or 0)
    faces = int(result.get("faces") or 0)
    if moves == 0:
        return f"Pembicara terkunci di satu posisi ({faces} frame berisi wajah)"
    return f"Kamera mengikuti pembicara · {moves}× pindah · {faces} frame berisi wajah"


def opencv_installed() -> bool:
    return _cv() is not None
