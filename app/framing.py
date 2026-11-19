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
import re
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

GAMEPLAY_STRONG_HINTS = (
    "gameplay", "walkthrough", "playthrough", "let's play", "lets play",
    "mabar", "push rank", "mobile legends", "mlbb", "valorant", "pubg",
    "free fire", "genshin", "minecraft", "roblox", "gta", "elden ring",
    "fortnite", "dota", "cs2", "csgo", "apex legends", "warzone",
    "call of duty", "resident evil", "horror game", "boss fight",
    "speedrun", "facecam", "windah", "miawaug", "jess no limit",
    "rrq", "evos", "onic", "steam", "ps5", "main game", "rank mythic",
)

GAMEPLAY_SUB_HINTS = {
    "gamer", "gaming", "streamer", "livestream", "npc", "respawn",
    "cooldown", "damage", "healer", "ulti", "turret", "mythic",
    "clutch", "headshot", "loot", "lobby", "matchmaking", "boss",
    "checkpoint", "gacha", "skin", "ranked", "noob", "pro player",
}

PODCAST_HINTS = (
    "podcast", "wawancara", "interview", "talkshow", "talk show",
    "close the door", "deddy corbuzier", "ceramah", "kajian", "ustadz",
    "motivasi", "seminar", "kuliah", "debat", "diskusi", "obrolan",
    "bintang tamu", "narasumber", "monolog",
)


def classify_content_context(context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Klasifikasi konteks video (gameplay vs podcast/pembicara vs topik umum)."""
    ctx = context or {}
    categories = [str(c).lower() for c in (ctx.get("categories") or []) if c]
    tags = [str(t).lower() for t in (ctx.get("tags") or []) if t]
    title = str(ctx.get("title") or ctx.get("job_title") or "").lower()
    uploader = str(ctx.get("uploader") or "").lower()
    description = str(ctx.get("description") or "").lower()[:600]
    transcript = str(ctx.get("transcript") or "").lower()[:1500]

    blob_meta = " ".join([title, uploader, " ".join(tags), description])
    blob_all = f"{blob_meta} {transcript}"

    gameplay_score = 0.0
    podcast_score = 0.0
    reasons: list[str] = []

    if any("gaming" in c or c == "game" for c in categories):
        gameplay_score += 0.65
        reasons.append("kategori Gaming")
    if any("people" in c or "education" in c or "news" in c for c in categories):
        podcast_score += 0.15

    strong_hits = [kw for kw in GAMEPLAY_STRONG_HINTS if kw in blob_all]
    if strong_hits:
        gameplay_score += min(0.65, 0.35 + 0.15 * len(strong_hits))
        reasons.append(f"kata kunci gameplay ({strong_hits[0]})")

    words = set(re.findall(r"[a-z0-9]+", blob_all))
    sub_hits = [w for w in GAMEPLAY_SUB_HINTS if w in words or w in blob_meta]
    if len(sub_hits) >= 2:
        gameplay_score += min(0.35, 0.12 * len(sub_hits))
    elif len(sub_hits) == 1:
        gameplay_score += 0.12

    pod_hits = [kw for kw in PODCAST_HINTS if kw in blob_all]
    if pod_hits:
        podcast_score += min(0.75, 0.4 + 0.18 * len(pod_hits))
        reasons.append(f"konteks {pod_hits[0]}")

    gameplay_score = _clamp(gameplay_score, 0.0, 1.0)
    podcast_score = _clamp(podcast_score, 0.0, 1.0)
    is_gameplay = gameplay_score >= 0.42 and gameplay_score > podcast_score
    return {
        "is_gameplay_hint": is_gameplay,
        "gameplay_score": round(gameplay_score, 3),
        "podcast_score": round(podcast_score, 3),
        "reasons": reasons[:3],
    }


def _corner_label(nx: float, ny: float) -> str:
    horiz = "kiri" if nx < 0.5 else "kanan"
    if ny < 0.38:
        return f"{horiz} atas"
    if ny > 0.58:
        return f"{horiz} bawah"
    return horiz


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
    # Batasi kedalaman ekspresi bersarang agar parser libavutil/eval.c ffmpeg tidak overflow
    while len(items) > 20:
        min_idx = min(
            range(1, len(items)),
            key=lambda i: abs(_f(items[i].get("x"), default) - _f(items[i - 1].get("x"), default)),
        )
        items.pop(min_idx)
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
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        use_grab = 1.0 < fps <= 120.0 and 0.05 <= step <= 2.0
        skip_count = max(0, int(round(step * fps)) - 1) if use_grab else 0
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
            if offset > duration + 1e-6:
                break
            if use_grab and skip_count > 0:
                grabbed = True
                for _ in range(skip_count):
                    if not capture.grab():
                        grabbed = False
                        break
                if not grabbed:
                    break
            else:
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
    candidates: list[tuple[Any, float, int]] = [
        (gray, 1.1, 5),
        (gray, 1.06, 3),
    ]
    # equalizeHist membantu video gelap/kontras rendah, hanya dijalankan bila perlu
    try:
        if float(gray.mean()) < 95.0 or float(gray.std()) < 45.0:
            candidates.append((cv2.equalizeHist(gray), 1.1, 4))
    except Exception:
        candidates.append((cv2.equalizeHist(gray), 1.1, 4))
    for source, scale, neighbors in candidates:
        boxes = cascade.detectMultiScale(
            source, scaleFactor=scale, minNeighbors=neighbors, minSize=(min_size, min_size)
        )
        if len(boxes):
            return [(x + w / 2.0, y + h / 2.0, float(w)) for (x, y, w, h) in boxes]
    return []


def _detect_edge_facecam(
    cv2: Any,
    frame: Any,
    cascade: Any,
    upscale: float = 1.75,
) -> list[tuple[float, float, float]]:
    """Deteksi wajah ukuran kecil pada pojok/tepi kiri & kanan (khusus facecam gamer).

    Pada video gameplay 16:9 yang diperkecil ke lebar 640px, kotak webcam host di
    pojok sering membuat ukuran wajah < 28px sehingga terlewat oleh ``detect_faces``
    standar. Fungsi ini memeriksa strip tepi kiri & kanan yang diperbesar 1.75×.
    """
    h, w = frame.shape[:2]
    if w < 120 or h < 90:
        return []
    strip_w = int(round(w * 0.36))
    results: list[tuple[float, float, float]] = []
    for x_offset, x_end in ((0, strip_w), (w - strip_w, w)):
        roi = frame[:, x_offset:x_end]
        if roi.size == 0:
            continue
        up_w = int(round(roi.shape[1] * upscale))
        up_h = int(round(roi.shape[0] * upscale))
        resized = cv2.resize(roi, (up_w, up_h))
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        for source, scale, neighbors in (
            (gray, 1.08, 4),
            (cv2.equalizeHist(gray), 1.08, 4),
        ):
            boxes = cascade.detectMultiScale(
                source, scaleFactor=scale, minNeighbors=neighbors, minSize=(24, 24)
            )
            if len(boxes):
                for bx, by, bw, bh in boxes:
                    cx = x_offset + (bx + bw / 2.0) / upscale
                    cy = (by + bh / 2.0) / upscale
                    fw = float(bw) / upscale
                    results.append((cx, cy, fw))
                break
    return results


def _evaluate_facecam(
    records: list[dict[str, float]],
    total_frames: int,
    moves: int,
    ctx_info: dict[str, Any],
) -> dict[str, Any] | None:
    """Tentukan apakah wajah yang terdeteksi adalah facecam host gameplay.

    Facecam gameplay memiliki ciri khas:
    - Posisi wajah berada di pojok/tepi layar (kiri/kanan/atas/bawah) dan relatif
      diam di dalam kotak webcam OBS (simpangan baku posisi kecil).
    - Ukuran wajah relatif kecil terhadap lebar layar 16:9 (<= 21%, atau <= 25%
      bila konteks video memang Gaming).
    """
    is_hint = bool(ctx_info.get("is_gameplay_hint"))
    gameplay_score = float(ctx_info.get("gameplay_score") or 0.0)
    podcast_score = float(ctx_info.get("podcast_score") or 0.0)

    if podcast_score >= 0.48 and gameplay_score < 0.35:
        return None

    # Saring kandidat wajah yang berada di zona facecam / berukuran kecil
    edge_records = [
        r for r in records
        if (r["nx"] <= 0.36 or r["nx"] >= 0.64 or r["ny"] <= 0.38 or r["ny"] >= 0.58)
        and r["nw"] <= (0.26 if is_hint else 0.20)
    ]
    pool = edge_records if len(edge_records) >= max(1, int(len(records) * 0.45)) else (
        records if is_hint else []
    )
    if not pool:
        if is_hint and gameplay_score >= 0.65 and not records:
            # Video jelas gameplay tapi wajah host terlalu kecil/tertutup headset:
            # default ke pojok kiri bawah standar streamer
            return {
                "x": 0.16,
                "y": 0.78,
                "w": 0.11,
                "scale": 0.32,
                "game_x": 0.55,
                "corner": "kiri bawah",
                "confidence": round(gameplay_score, 3),
            }
        return None

    # Kelompokkan berdasarkan sisi kiri vs kanan supaya 1 false-positive di sisi
    # seberang tidak menggeser titik tengah facecam
    left_pool = [r for r in pool if r["nx"] < 0.5]
    right_pool = [r for r in pool if r["nx"] >= 0.5]
    cluster = left_pool if len(left_pool) >= len(right_pool) else right_pool
    if not cluster:
        return None

    min_hits = 1 if is_hint else max(2, int(round(total_frames * 0.18)))
    if len(cluster) < min_hits:
        return None

    xs = sorted(r["nx"] for r in cluster)
    ys = sorted(r["ny"] for r in cluster)
    ws = sorted(r["nw"] for r in cluster)
    mid = len(cluster) // 2
    med_x, med_y, med_w = xs[mid], ys[mid], ws[mid]

    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    std_x = math.sqrt(sum((v - mean_x) ** 2 for v in xs) / len(xs))
    std_y = math.sqrt(sum((v - mean_y) ** 2 for v in ys) / len(ys))

    in_corner_or_edge = (
        med_x <= 0.35 or med_x >= 0.65 or med_y <= 0.36 or med_y >= 0.60
    )
    in_strict_corner = (
        (med_x <= 0.32 or med_x >= 0.68) and (med_y <= 0.38 or med_y >= 0.58)
    )
    is_pinned = std_x <= 0.075 and std_y <= 0.08
    is_small = med_w <= (0.25 if is_hint else 0.17)

    # Tanpa hint gaming, syarat visual harus ketat (pojok + ukuran kecil + diam + tidak pindah pembicara)
    if not is_hint:
        if not (in_strict_corner and is_pinned and is_small and moves <= 1):
            return None
    else:
        if not ((in_corner_or_edge or is_small) and std_x <= 0.11):
            return None

    cam_scale = _clamp(max(0.26, med_w * 2.85), 0.24, 0.44)
    game_x = 0.56 if med_x < 0.35 else (0.44 if med_x > 0.65 else 0.50)
    corner = _corner_label(med_x, med_y)
    return {
        "x": round(_clamp(med_x, 0.04, 0.96), 4),
        "y": round(_clamp(med_y, 0.06, 0.94), 4),
        "w": round(med_w, 4),
        "scale": round(cam_scale, 4),
        "game_x": round(game_x, 4),
        "corner": corner,
        "confidence": round(max(gameplay_score, len(cluster) / max(1, total_frames)), 3),
    }


def analyze(
    source_path: str | Path,
    start: float = 0.0,
    duration: float | None = None,
    sample_step: float = 0.4,
    on_progress: Callable[[float, str], None] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analisa satu klip → segmen fokus kamera (podcast/pembicara vs gameplay vs topik).

    Kembalian::

        {"ok": True, "kind": "speaker" | "gameplay" | "topic",
         "faces": 12, "coverage": 0.83, "moves": 2,
         "segments": [{"at": 0.0, "x": 0.31}, ...],
         "facecam": {...}, "reason": ""}
    """
    progress = on_progress or (lambda fraction, message: None)
    cv2 = _cv()
    path = Path(source_path)
    ctx_info = classify_content_context(context)
    if not path.exists():
        return {"ok": False, "kind": "topic", "reason": "File sumber tidak ditemukan", "segments": []}
    if cv2 is None or not available():
        return {"ok": False, "kind": "topic", "reason": "OpenCV belum terpasang (deteksi wajah nonaktif)", "segments": []}

    info = media.probe(path)
    length = _f(duration, 0.0) or _f(info.duration, 0.0)
    length = min(length, float(CLIP_MAX_SECONDS) + 1.0)
    if length <= 0:
        return {"ok": False, "kind": "topic", "reason": "Durasi klip tidak diketahui", "segments": []}

    cascade = _cascade()
    progress(0.05, "Membaca frame video…")
    frames = _read_frames(path, max(0.0, _f(start)), length, sample_step)
    if not frames:
        return {"ok": False, "kind": "topic", "reason": "Video tidak bisa dibaca", "segments": []}

    frame_h, frame_w = frames[0][1].shape[:2]
    source_w = int(info.width or 0) or int(frame_w)
    source_h = int(info.height or 0) or int(frame_h)
    scale_x = source_w / float(max(1, frame_w))
    scale_y = source_h / float(max(1, frame_h))
    is_landscape = source_w > source_h * 1.15

    samples: list[dict[str, Any]] = []
    face_records: list[dict[str, float]] = []
    detected = 0
    previous_x = 0.5
    for index, (offset, frame) in enumerate(frames):
        faces = detect_faces(cv2, frame, cascade)
        if not faces and is_landscape and (ctx_info["is_gameplay_hint"] or index % 3 == 0):
            faces = _detect_edge_facecam(cv2, frame, cascade)
        if faces:
            detected += 1
            # wajah dominan: paling besar, tapi diberi bonus kalau dekat posisi kamera sekarang
            def score(face: tuple[float, float, float]) -> float:
                center_x, _center_y, width = face
                norm = _clamp((center_x * scale_x) / max(1.0, source_w))
                return width * (1.35 if abs(norm - previous_x) < 0.22 else 1.0)

            best = max(faces, key=score)
            nx = _clamp((best[0] * scale_x) / max(1.0, source_w))
            ny = _clamp((best[1] * scale_y) / max(1.0, source_h))
            nw = _clamp((best[2] * scale_x) / max(1.0, source_w))
            face_records.append({"at": offset, "nx": nx, "ny": ny, "nw": nw})
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

    facecam = (
        _evaluate_facecam(face_records, len(frames), moves, ctx_info)
        if is_landscape
        else None
    )
    if facecam is not None:
        kind = "gameplay"
        ok = True
    elif bool(segments) and coverage >= 0.35 and len(segments) > 0:
        kind = "speaker"
        ok = True
    else:
        kind = "topic"
        ok = False

    result: dict[str, Any] = {
        "ok": ok,
        "kind": kind,
        "faces": detected,
        "coverage": round(coverage, 3),
        "segments": segments,
        "moves": moves,
        "start": round(max(0.0, _f(start)), 3),
        "duration": round(length, 3),
        "source_size": [source_w, source_h],
        "facecam": facecam or {},
        "context": ctx_info,
        "reason": "",
    }
    if kind == "gameplay" and facecam:
        result["reason"] = (
            f"Video gameplay terdeteksi (facecam host di {facecam.get('corner', 'pojok')}) — "
            "atas gameplay, bawah host"
        )
    elif not segments:
        result["reason"] = "Tidak ada wajah pembicara — fokus pada topik & hook video"
    elif coverage < 0.35:
        result["reason"] = (
            f"Wajah hanya terdeteksi di {coverage * 100:.0f}% frame — fokus pada topik & hook video"
        )
    progress(1.0, "Analisa pembicara selesai")
    return result


def summary(result: dict[str, Any] | None) -> str:
    """Kalimat singkat untuk ditampilkan di UI."""
    if not result:
        return "Belum dianalisa"
    if result.get("kind") == "gameplay" and result.get("facecam"):
        corner = (result.get("facecam") or {}).get("corner") or "pojok"
        return f"Mode Gameplay · atas game, bawah host ({corner})"
    if not result.get("ok"):
        return result.get("reason") or "Fokus topik & hook video (kamera di tengah)"
    moves = int(result.get("moves") or 0)
    faces = int(result.get("faces") or 0)
    if moves == 0:
        return f"Fokus pembicara di satu posisi ({faces} frame berisi wajah)"
    return f"Kamera mengikuti pembicara · {moves}× pindah · {faces} frame berisi wajah"


def opencv_installed() -> bool:
    return _cv() is not None
