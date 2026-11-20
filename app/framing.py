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

# Versi algoritma deteksi framing — dinaikkan agar klip lama otomatis dianalisa ulang saat render
DETECTOR_VERSION = 3

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
    "free fire", "genshin", "minecraft", "roblox", "gta", "grand theft auto",
    "elden ring", "fortnite", "dota", "cs2", "csgo", "apex legends", "warzone",
    "call of duty", "resident evil", "horror game", "boss fight",
    "speedrun", "facecam", "windah", "miawaug", "jess no limit",
    "rrq", "evos", "onic", "steam", "ps5", "ps4", "playstation", "xbox",
    "nintendo", "wolverine", "spider-man", "spiderman", "god of war",
    "cyberpunk", "red dead", "wukong", "black myth", "silent hill",
    "final fantasy", "monster hunter", "tekken", "ea fc", "efootball",
    "fifa", "share factory", "main game", "rank mythic", "console",
)

GAMEPLAY_SUB_HINTS = {
    "gamer", "gaming", "streamer", "livestream", "npc", "respawn",
    "cooldown", "damage", "healer", "ulti", "turret", "mythic",
    "clutch", "headshot", "loot", "lobby", "matchmaking", "boss",
    "checkpoint", "gacha", "skin", "ranked", "noob", "pro player",
    "controller", "dualsense", "joystick", "console", "dlc", "patch",
    "level", "quest", "mission", "cutscene", "trailer", "fps", "rpg",
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
    clip_title = str(ctx.get("clip_title") or ctx.get("hook") or "").lower()
    source_name = str(ctx.get("source_name") or "").lower()
    uploader = str(ctx.get("uploader") or "").lower()
    description = str(ctx.get("description") or "").lower()[:600]
    transcript = str(ctx.get("transcript") or "").lower()[:1500]

    blob_meta = " ".join([title, clip_title, source_name, uploader, " ".join(tags), description])
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
        gameplay_score += min(0.70, 0.42 + 0.14 * len(strong_hits))
        reasons.append(f"kata kunci gameplay ({strong_hits[0]})")

    words = set(re.findall(r"[a-z0-9]+", blob_all))
    sub_hits = [w for w in GAMEPLAY_SUB_HINTS if w in words or w in blob_meta]
    if len(sub_hits) >= 2:
        gameplay_score += min(0.40, 0.14 * len(sub_hits))
    elif len(sub_hits) == 1:
        gameplay_score += 0.15

    pod_hits = [kw for kw in PODCAST_HINTS if kw in blob_all]
    if pod_hits:
        podcast_score += min(0.75, 0.4 + 0.18 * len(pod_hits))
        reasons.append(f"konteks {pod_hits[0]}")

    gameplay_score = _clamp(gameplay_score, 0.0, 1.0)
    podcast_score = _clamp(podcast_score, 0.0, 1.0)
    is_gameplay = gameplay_score >= 0.40 and gameplay_score > podcast_score
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


def _extra_cascades() -> list[tuple[str, Any]]:
    """Muat cascade tambahan (alt2 & profile) untuk mendeteksi wajah gamer
    yang memakai kacamata, headset, mikrofon besar, atau menoleh ke monitor."""
    cv2 = _cv()
    if cv2 is None:
        return []
    out: list[tuple[str, Any]] = []
    for key, filename in (
        ("front", "haarcascade_frontalface_default.xml"),
        ("alt2", "haarcascade_frontalface_alt2.xml"),
        ("profile", "haarcascade_profileface.xml"),
    ):
        if key not in _CASCADE_CACHE:
            try:
                c = cv2.CascadeClassifier(cv2.data.haarcascades + filename)
                _CASCADE_CACHE[key] = c if not c.empty() else None
            except Exception:
                _CASCADE_CACHE[key] = None
        if _CASCADE_CACHE.get(key) is not None:
            out.append((key, _CASCADE_CACHE[key]))
    return out


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


def _face_live_activity(
    cv2: Any,
    frame: Any,
    curr_gray: Any,
    prev_gray: Any | None,
    cx: float,
    cy: float,
    fw: float,
) -> tuple[float, float, float]:
    """Ukur aktivitas bicara/gerak lokal & rasio warna kulit manusia pada kandidat wajah.

    Mengembalikan ``(motion_diff, skin_ratio, live_weight)``:
    - Wallpaper/poster/cover game statis (mis. gambar Lucia & Jason GTA VI di layar PS5)
      memiliki ``motion_diff < 1.35`` karena piksel wajahnya diam seperti lukisan,
      sehingga diberi ``live_weight`` sangat kecil (0.08) agar tidak membajak fokus.
    - Host manusia asli yang sedang berbicara memiliki gerakan mulut/kepala/mata
      lokal (``motion_diff >= 1.45``) dan rasio krominansi kulit manusia di ruang YCrCb,
      sehingga diberi ``live_weight`` tinggi (1.8 – 3.6).
    """
    h, w = curr_gray.shape[:2]
    r_in = max(8, int(round(fw * 0.45)))
    x1 = max(0, int(round(cx - r_in)))
    x2 = min(w, int(round(cx + r_in)))
    y1 = max(0, int(round(cy - r_in)))
    y2 = min(h, int(round(cy + r_in)))
    if x2 - x1 < 6 or y2 - y1 < 6:
        return 0.0, 0.0, 0.5

    # 1) Rasio warna kulit di ruang YCrCb (membantu membedakan wajah manusia vs ikon game)
    skin_ratio = 0.25
    try:
        roi_bgr = frame[y1:y2, x1:x2]
        ycrcb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycrcb, (35, 132, 77), (250, 178, 128))
        skin_ratio = float(cv2.countNonZero(mask)) / float(max(1, (x2 - x1) * (y2 - y1)))
    except Exception:
        skin_ratio = 0.25

    if prev_gray is None or prev_gray.shape != curr_gray.shape:
        base_w = 1.15 if 0.12 <= skin_ratio <= 0.88 else 0.75
        return 2.0, round(skin_ratio, 3), base_w

    # 2) Gerakan piksel lokal di dalam wajah dibanding cincin latar di sekitarnya
    r_out = max(14, int(round(fw * 0.95)))
    ox1 = max(0, int(round(cx - r_out)))
    ox2 = min(w, int(round(cx + r_out)))
    oy1 = max(0, int(round(cy - r_out)))
    oy2 = min(h, int(round(cy + r_out)))

    try:
        inner_diff_mat = cv2.absdiff(curr_gray[y1:y2, x1:x2], prev_gray[y1:y2, x1:x2])
        inner_diff = float(inner_diff_mat.mean()[0])
        outer_diff_mat = cv2.absdiff(curr_gray[oy1:oy2, ox1:ox2], prev_gray[oy1:oy2, ox1:ox2])
        outer_sum = float(outer_diff_mat.mean()[0]) * float((ox2 - ox1) * (oy2 - oy1))
        inner_sum = inner_diff * float((x2 - x1) * (y2 - y1))
        ring_area = max(1.0, float((ox2 - ox1) * (oy2 - oy1) - (x2 - x1) * (y2 - y1)))
        ring_diff = max(0.0, (outer_sum - inner_sum) / ring_area)
    except Exception:
        return 2.0, round(skin_ratio, 3), 1.0

    # Jika seluruh layar berganti mendadak (scene cut / scroll menu besar), jangan anggap bicara
    if ring_diff > 24.0 and inner_diff <= ring_diff * 1.12:
        motion = 0.4
    else:
        motion = inner_diff

    # Wajah poster/wallpaper statis (diam total)
    if motion < 1.30:
        live_weight = 0.08
    elif motion <= 32.0 and (motion >= ring_diff * 0.60 or motion >= 2.2):
        skin_bonus = 1.25 if 0.14 <= skin_ratio <= 0.88 else 0.85
        live_weight = (1.6 + min(2.0, motion / 3.2)) * skin_bonus
    else:
        live_weight = 0.55
    return round(motion, 3), round(skin_ratio, 3), round(live_weight, 3)


def _dedup_faces(faces: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Gabungkan deteksi wajah yang bertumpuk pada lokasi yang sama."""
    if len(faces) <= 1:
        return faces
    kept: list[tuple[float, float, float]] = []
    for cx, cy, fw in sorted(faces, key=lambda item: item[2], reverse=True):
        if any(
            abs(cx - kx) < max(fw, kw) * 0.55 and abs(cy - ky) < max(fw, kw) * 0.55
            for kx, ky, kw in kept
        ):
            continue
        kept.append((cx, cy, fw))
    return kept


def detect_faces(cv2: Any, frame: Any, cascade: Any) -> list[tuple[float, float, float]]:
    """Deteksi wajah pada satu frame → ``[(center_x, center_y, lebar)]`` piksel.

    Dipakai beberapa tingkat ketat + cascade alt2 agar host berkacamata/headset/mic
    tetap terdeteksi bahkan ketika ada gambar karakter game di layar.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    min_size = max(24, int(frame.shape[1] * 0.045))
    eq_gray = None
    try:
        if float(gray.mean()) < 105.0 or float(gray.std()) < 48.0:
            eq_gray = cv2.equalizeHist(gray)
    except Exception:
        eq_gray = cv2.equalizeHist(gray)

    found: list[tuple[float, float, float]] = []
    extra = dict(_extra_cascades())
    alt2 = extra.get("alt2")

    passes: list[tuple[Any, Any, float, int]] = [
        (cascade, gray, 1.09, 5),
        (cascade, gray, 1.06, 3),
    ]
    if alt2 is not None:
        passes.append((alt2, gray, 1.08, 4))
    if eq_gray is not None:
        passes.append((cascade, eq_gray, 1.08, 4))
        if alt2 is not None:
            passes.append((alt2, eq_gray, 1.08, 4))

    for clf, source, scale, neighbors in passes:
        boxes = clf.detectMultiScale(
            source, scaleFactor=scale, minNeighbors=neighbors, minSize=(min_size, min_size)
        )
        for x, y, w, h in boxes:
            found.append((x + w / 2.0, y + h / 2.0, float(w)))
        if len(found) >= 2:
            break
    return _dedup_faces(found)


def _detect_edge_facecam(
    cv2: Any,
    frame: Any,
    cascade: Any,
    upscale: float = 1.75,
) -> list[tuple[float, float, float]]:
    """Deteksi wajah ukuran kecil pada pojok/tepi kiri & kanan (khusus facecam gamer).

    Berbeda dari versi lama, fungsi ini memakai multi-cascade (frontal default,
    frontal alt2 untuk headset/mic/kacamata, dan profile untuk wajah menoleh ke
    monitor) serta dijalankan secara independen walaupun di tengah layar ada
    gambar karakter game besar (seperti artwork GTA VI di menu PS5).
    """
    h, w = frame.shape[:2]
    if w < 120 or h < 90:
        return []
    strip_w = int(round(w * 0.38))
    cascades = _extra_cascades() or [("front", cascade)]
    results: list[tuple[float, float, float]] = []

    for x_offset, x_end in ((0, strip_w), (w - strip_w, w)):
        roi = frame[:, x_offset:x_end]
        if roi.size == 0:
            continue
        up_w = int(round(roi.shape[1] * upscale))
        up_h = int(round(roi.shape[0] * upscale))
        resized = cv2.resize(roi, (up_w, up_h))
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        eq_gray = cv2.equalizeHist(gray)
        flipped_gray = None

        roi_hits: list[tuple[float, float, float]] = []
        for key, clf in cascades:
            for src in (gray, eq_gray):
                boxes = clf.detectMultiScale(
                    src, scaleFactor=1.08, minNeighbors=4, minSize=(24, 24)
                )
                for bx, by, bw, bh in boxes:
                    cx = x_offset + (bx + bw / 2.0) / upscale
                    cy = (by + bh / 2.0) / upscale
                    fw = float(bw) / upscale
                    roi_hits.append((cx, cy, fw))
                if roi_hits:
                    break
            if not roi_hits and key == "profile":
                if flipped_gray is None:
                    flipped_gray = cv2.flip(gray, 1)
                boxes = clf.detectMultiScale(
                    flipped_gray, scaleFactor=1.08, minNeighbors=4, minSize=(24, 24)
                )
                for bx, by, bw, bh in boxes:
                    orig_bx = up_w - (bx + bw)
                    cx = x_offset + (orig_bx + bw / 2.0) / upscale
                    cy = (by + bh / 2.0) / upscale
                    fw = float(bw) / upscale
                    roi_hits.append((cx, cy, fw))
            if roi_hits:
                results.extend(roi_hits)
                break
    return _dedup_faces(results)


def _detect_active_corner_webcam(
    cv2: Any,
    frames: list[tuple[float, Any]],
) -> dict[str, Any] | None:
    """Fallback visual: cari kotak webcam aktif di 4 pojok layar (kiri/kanan × atas/bawah).

    Berguna saat wajah YouTuber gamer tertutup mikrofon besar/headset/masker
    sehingga Haar cascade melewatkannya, tetapi di pojok layar terdapat kotak
    facecam dengan gerakan hidup + warna kulit manusia.
    """
    if len(frames) < 4:
        return None
    h, w = frames[0][1].shape[:2]
    if w < 160 or h < 90:
        return None

    # 4 zona pojok standar OBS streamer: (label, x1_r, x2_r, y1_r, y2_r, cx, cy, game_x)
    corners = [
        ("kiri bawah", 0.01, 0.30, 0.60, 0.97, 0.16, 0.78, 0.56),
        ("kiri atas", 0.01, 0.30, 0.03, 0.40, 0.16, 0.22, 0.56),
        ("kanan bawah", 0.70, 0.99, 0.60, 0.97, 0.84, 0.78, 0.44),
        ("kanan atas", 0.70, 0.99, 0.03, 0.40, 0.84, 0.22, 0.44),
    ]
    step = max(1, len(frames) // 25)
    sampled = [frames[i][1] for i in range(0, len(frames), step)]
    if len(sampled) < 3:
        return None

    best_corner: dict[str, Any] | None = None
    best_score = 0.0

    for label, xr1, xr2, yr1, yr2, def_cx, def_cy, game_x in corners:
        x1, x2 = int(round(w * xr1)), int(round(w * xr2))
        y1, y2 = int(round(h * yr1)), int(round(h * yr2))
        if x2 - x1 < 24 or y2 - y1 < 20:
            continue

        active_count = 0
        skin_Editing_scores: list[float] = []
        motion_scores: list[float] = []
        weighted_xs: list[float] = []
        weighted_ys: list[float] = []
        prev_roi_gray = None

        for frm in sampled:
            roi = frm[y1:y2, x1:x2]
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            ycrcb = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
            skin_mask = cv2.inRange(ycrcb, (40, 133, 77), (245, 177, 127))
            skin_ratio = float(cv2.countNonZero(skin_mask)) / float(max(1, gray.size))

            if prev_roi_gray is not None:
                diff = cv2.absdiff(gray, prev_roi_gray)
                # Fokus gerakan pada piksel kulit/orang di dalam pojok
                skin_diff = cv2.bitwise_and(diff, diff, mask=skin_mask)
                m_all = float(diff.mean()[0])
                m_skin = (
                    float(skin_diff.sum()) / float(max(1, cv2.countNonZero(skin_mask)))
                    if cv2.countNonZero(skin_mask) > 25
                    else 0.0
                )
                if 0.04 <= skin_ratio <= 0.55 and 1.4 <= m_all <= 22.0 and m_skin >= 1.6:
                    active_count += 1
                    skin_Editing_scores.append(skin_ratio)
                    motion_scores.append(m_skin)
                    moments = cv2.moments(skin_mask, True)
                    if moments["m00"] > 10:
                        cx_norm = (x1 + moments["m10"] / moments["m00"]) / float(w)
                        cy_norm = (y1 + moments["m01"] / moments["m00"]) / float(h)
                        weighted_xs.append(cx_norm)
                        weighted_ys.append(cy_norm)
            prev_roi_gray = gray

        active_ratio = active_count / float(max(1, len(sampled) - 1))
        if active_ratio < 0.30 or not motion_scores:
            continue

        # Pastikan titik tengah kulit di pojok ini stabil (tidak lari-lari seperti efek ledakan game)
        if len(weighted_xs) >= 3:
            mx = sum(weighted_xs) / len(weighted_xs)
            my = sum(weighted_ys) / len(weighted_ys)
            sx = math.sqrt(sum((v - mx) ** 2 for v in weighted_xs) / len(weighted_xs))
            sy = math.sqrt(sum((v - my) ** 2 for v in weighted_ys) / len(weighted_ys))
            if sx > 0.055 or sy > 0.065:
                continue
            final_cx = _clamp(mx, 0.06, 0.94)
            final_cy = _clamp(my, 0.08, 0.92)
        else:
            final_cx, final_cy = def_cx, def_cy

        score = active_ratio * (1.0 + min(1.5, (sum(motion_scores) / len(motion_scores)) / 6.0))
        if score > best_score:
            best_score = score
            best_corner = {
                "x": round(final_cx, 4),
                "y": round(final_cy, 4),
                "w": 0.095,
                "scale": 0.25,
                "game_x": round(game_x, 4),
                "corner": label,
                "confidence": round(_clamp(score, 0.45, 0.95), 3),
            }
    return best_corner


def _evaluate_facecam(
    records: list[dict[str, float]],
    total_frames: int,
    moves: int,
    ctx_info: dict[str, Any],
    fallback_corner: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Tentukan apakah wajah yang terdeteksi adalah facecam host gameplay.

    Menggunakan pengelompokan spasial (spatial clustering) pada kandidat wajah
    di pojok/tepi layar + filter aktivitas hidup (``live``), sehingga gambar
    karakter game besar di layar (seperti Lucia & Jason GTA VI di PS5) tidak
    akan menggagalkan deteksi facecam YouTuber gamer di pojok layar.
    """
    is_hint = bool(ctx_info.get("is_gameplay_hint"))
    gameplay_score = float(ctx_info.get("gameplay_score") or 0.0)
    podcast_score = float(ctx_info.get("podcast_score") or 0.0)

    if podcast_score >= 0.48 and gameplay_score < 0.35:
        return None

    max_w = 0.34 if is_hint else 0.22
    candidates = [
        r for r in records
        if (r["nx"] <= 0.38 or r["nx"] >= 0.62 or r["ny"] <= 0.38 or r["ny"] >= 0.56)
        and 0.025 <= r["nw"] <= max_w
    ]

    # Bentuk cluster spasial di sekitar setiap kandidat (radius dx<=0.085, dy<=0.095)
    best_cluster: list[dict[str, float]] = []
    best_cluster_score = -1.0
    for seed in candidates:
        sx, sy = seed["nx"], seed["ny"]
        members = [
            r for r in candidates
            if abs(r["nx"] - sx) <= 0.085 and abs(r["ny"] - sy) <= 0.095
        ]
        if not members:
            continue
        # Hitung jumlah frame unik dan aktivitas hidup (bukan poster/ikon diam)
        unique_times = {round(r["at"], 2) for r in members}
        hits = len(unique_times)
        live_hits = sum(1 for r in members if float(r.get("motion", 2.0)) >= 1.35)
        avg_live = sum(float(r.get("live", 1.0)) for r in members) / len(members)
        edge_hits = sum(1 for r in members if r.get("from_edge"))

        # Tolak cluster yang 100% diam seperti ikon/wallpaper statis (mis. Wolverine / Lucia & Jason GTA VI)
        if total_frames >= 4 and live_hits == 0 and avg_live < 0.35:
            continue

        med_x = sorted(r["nx"] for r in members)[len(members) // 2]
        med_y = sorted(r["ny"] for r in members)[len(members) // 2]
        med_w = sorted(r["nw"] for r in members)[len(members) // 2]

        in_corner = (med_x <= 0.34 or med_x >= 0.66) and (med_y <= 0.40 or med_y >= 0.56)
        corner_bonus = 1.45 if in_corner else 1.05
        size_bonus = 1.25 if med_w <= 0.16 else 1.0
        edge_bonus = 1.15 if edge_hits > 0 else 1.0

        c_score = (hits + 1.8 * live_hits) * avg_live * corner_bonus * size_bonus * edge_bonus
        if c_score > best_cluster_score:
            best_cluster_score = c_score
            best_cluster = members

    min_hits = 1 if is_hint else max(2, int(round(total_frames * 0.14)))
    unique_cluster_hits = len({round(r["at"], 2) for r in best_cluster})

    if unique_cluster_hits < min_hits:
        if fallback_corner is not None and (is_hint or fallback_corner.get("confidence", 0.0) >= 0.62):
            return fallback_corner
        if is_hint and gameplay_score >= 0.60:
            return fallback_corner or {
                "x": 0.16,
                "y": 0.78,
                "w": 0.10,
                "scale": 0.25,
                "game_x": 0.56,
                "corner": "kiri bawah",
                "confidence": round(gameplay_score, 3),
            }
        return None

    xs = sorted(r["nx"] for r in best_cluster)
    ys = sorted(r["ny"] for r in best_cluster)
    ws = sorted(r["nw"] for r in best_cluster)
    motions = sorted(float(r.get("motion", 2.0)) for r in best_cluster)
    mid = len(best_cluster) // 2
    med_x, med_y, med_w = xs[mid], ys[mid], ws[mid]
    med_motion = motions[mid]

    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    std_x = math.sqrt(sum((v - mean_x) ** 2 for v in xs) / len(xs))
    std_y = math.sqrt(sum((v - mean_y) ** 2 for v in ys) / len(ys))

    in_corner_or_edge = (
        med_x <= 0.38 or med_x >= 0.62 or med_y <= 0.38 or med_y >= 0.56
    )
    in_strict_corner = (
        (med_x <= 0.35 or med_x >= 0.65) and (med_y <= 0.40 or med_y >= 0.56)
    )
    is_pinned = std_x <= 0.075 and std_y <= 0.085
    is_small = med_w <= (0.34 if is_hint else 0.21)

    if not is_hint:
        # Tanpa hint judul gaming: wajib di pojok/tepi, stabil, ukuran kecil, dan bukan gambar statis
        if not (in_strict_corner and is_pinned and is_small and med_motion >= 1.30):
            return fallback_corner if (fallback_corner and fallback_corner.get("confidence", 0) >= 0.72) else None
    else:
        if not ((in_corner_or_edge or is_small) and std_x <= 0.11):
            return fallback_corner

    # Kalibrasi zoom facecam (2.35x lebar wajah agar fokus pas ke wajah & mic host di panel bawah)
    cam_scale = _clamp(max(0.20, med_w * 2.35), 0.19, 0.38)
    game_x = 0.56 if med_x < 0.35 else (0.44 if med_x > 0.65 else 0.50)
    corner = _corner_label(med_x, med_y)
    return {
        "x": round(_clamp(med_x, 0.04, 0.96), 4),
        "y": round(_clamp(med_y, 0.06, 0.94), 4),
        "w": round(med_w, 4),
        "scale": round(cam_scale, 4),
        "game_x": round(game_x, 4),
        "corner": corner,
        "confidence": round(max(gameplay_score, unique_cluster_hits / max(1, total_frames)), 3),
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
         "detector_v": 3, "faces": 12, "coverage": 0.83, "moves": 2,
         "segments": [{"at": 0.0, "x": 0.31}, ...],
         "facecam": {...}, "reason": ""}
    """
    progress = on_progress or (lambda fraction, message: None)
    cv2 = _cv()
    path = Path(source_path)
    merged_ctx = dict(context or {})
    if not merged_ctx.get("source_name"):
        merged_ctx["source_name"] = path.stem
    ctx_info = classify_content_context(merged_ctx)
    if not path.exists():
        return {"ok": False, "kind": "topic", "detector_v": DETECTOR_VERSION,
                "reason": "File sumber tidak ditemukan", "segments": []}
    if cv2 is None or not available():
        return {"ok": False, "kind": "topic", "detector_v": DETECTOR_VERSION,
                "reason": "OpenCV belum terpasang (deteksi wajah nonaktif)", "segments": []}

    info = media.probe(path)
    length = _f(duration, 0.0) or _f(info.duration, 0.0)
    length = min(length, float(CLIP_MAX_SECONDS) + 1.0)
    if length <= 0:
        return {"ok": False, "kind": "topic", "detector_v": DETECTOR_VERSION,
                "reason": "Durasi klip tidak diketahui", "segments": []}

    cascade = _cascade()
    progress(0.05, "Membaca frame video…")
    frames = _read_frames(path, max(0.0, _f(start)), length, sample_step)
    if not frames:
        return {"ok": False, "kind": "topic", "detector_v": DETECTOR_VERSION,
                "reason": "Video tidak bisa dibaca", "segments": []}

    frame_h, frame_w = frames[0][1].shape[:2]
    source_w = int(info.width or 0) or int(frame_w)
    source_h = int(info.height or 0) or int(frame_h)
    scale_x = source_w / float(max(1, frame_w))
    scale_y = source_h / float(max(1, frame_h))
    is_landscape = source_w > source_h * 1.15

    samples: list[dict[str, Any]] = []
    all_face_records: list[dict[str, float]] = []
    detected = 0
    live_speaker_frames = 0
    previous_x = 0.5
    prev_gray: Any | None = None
    edge_stride = 1 if (ctx_info["is_gameplay_hint"] or len(frames) <= 75) else 2

    for index, (offset, frame) in enumerate(frames):
        curr_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        full_faces = detect_faces(cv2, frame, cascade)
        edge_faces: list[tuple[float, float, float]] = []
        if is_landscape and (index % edge_stride == 0):
            edge_faces = _detect_edge_facecam(cv2, frame, cascade)

        # Gabungkan deteksi frame utuh & deteksi pojok tanpa membuang wajah kecil
        combined_tagged: list[tuple[float, float, float, bool]] = [
            (cx, cy, fw, False) for (cx, cy, fw) in full_faces
        ]
        for ecx, ecy, efw in edge_faces:
            if not any(
                abs(ecx - cx) < max(efw, fw) * 0.55 and abs(ecy - cy) < max(efw, fw) * 0.55
                for cx, cy, fw, _ in combined_tagged
            ):
                combined_tagged.append((ecx, ecy, efw, True))

        if combined_tagged:
            scored_faces: list[dict[str, Any]] = []
            for cx, cy, fw, from_edge in combined_tagged:
                motion, skin_ratio, live_w = _face_live_activity(
                    cv2, frame, curr_gray, prev_gray, cx, cy, fw
                )
                nx = _clamp((cx * scale_x) / max(1.0, source_w))
                ny = _clamp((cy * scale_y) / max(1.0, source_h))
                nw = _clamp((fw * scale_x) / max(1.0, source_w))
                rec = {
                    "at": offset,
                    "cx": cx,
                    "cy": cy,
                    "fw": fw,
                    "nx": nx,
                    "ny": ny,
                    "nw": nw,
                    "motion": motion,
                    "skin": skin_ratio,
                    "live": live_w,
                    "from_edge": 1.0 if from_edge else 0.0,
                }
                all_face_records.append(rec)
                scored_faces.append(rec)

            # Untuk tracking pembicara utama (mode speaker/podcast), prioritaskan wajah
            # manusia yang benar-benar bergerak/berbicara (live_w tinggi), BUKAN gambar
            # poster/wallpaper statis di layar (seperti artwork GTA VI di menu PS5).
            def speaker_score(item: dict[str, Any]) -> float:
                continuity = 1.30 if abs(item["nx"] - previous_x) < 0.22 else 1.0
                return float(item["fw"]) * float(item["live"]) * continuity

            best = max(scored_faces, key=speaker_score)
            # Hanya masukkan ke sampel fokus kamera bila bukan gambar statis murni
            if best["live"] >= 0.35 or prev_gray is None:
                detected += 1
                if best["motion"] >= 1.40:
                    live_speaker_frames += 1
                focus = focus_from_face(best["cx"] * scale_x, source_w, source_h)
                previous_x = focus
                samples.append({"at": offset, "x": round(focus, 4)})

        prev_gray = curr_gray
        if index % 25 == 0:
            progress(0.05 + 0.8 * index / max(1, len(frames)),
                     f"Mendeteksi wajah & aktivitas bicara {index}/{len(frames)}…")

    coverage = detected / max(1, len(frames))
    live_ratio = live_speaker_frames / max(1, detected) if detected else 0.0
    progress(0.9, "Menyusun pergerakan kamera…")
    segments = plan_segments(samples)
    moves = max(0, len(segments) - 1)

    fallback_corner = (
        _detect_active_corner_webcam(cv2, frames)
        if is_landscape
        else None
    )
    facecam = (
        _evaluate_facecam(all_face_records, len(frames), moves, ctx_info, fallback_corner=fallback_corner)
        if is_landscape
        else None
    )

    if facecam is not None:
        kind = "gameplay"
        ok = True
    elif bool(segments) and coverage >= 0.35 and live_ratio >= 0.22 and len(segments) > 0:
        kind = "speaker"
        ok = True
    else:
        # Jika hanya ada wajah statis di wallpaper/poster (live_ratio < 0.22), jangan crop ke poster!
        kind = "topic"
        ok = False
        segments = []
        moves = 0

    result: dict[str, Any] = {
        "ok": ok,
        "kind": kind,
        "detector_v": DETECTOR_VERSION,
        "faces": detected,
        "coverage": round(coverage, 3),
        "live_ratio": round(live_ratio, 3),
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
        result["reason"] = "Tidak ada wajah pembicara aktif — fokus pada topik & hook video"
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
