"""Video editor engine: spec JSON → render ffmpeg (timeline, teks, subtitle, template, 4K).

Semua fitur editor dideskripsikan sebagai satu dict `spec` yang disimpan di clip,
sehingga UI bisa edit bebas dan server bisa render ulang kapan saja.

Struktur spec (semua opsional, ada default):
{
  "layout": "fit" | "crop" | "gameplay" | "split" | "black",
  "background": {"type": "blur"|"gradient"|"color", "blur": 30, "color1": "#0b0b12",
                 "color2": "#7c5cff", "darken": 0.12},
  "crop_focus": 0.5,
  "split": {"left_x": 0.28, "left_y": 0.42, "right_x": 0.72, "right_y": 0.42, "split": 0.5},
  "punch_in": true,
  "segments": [{"start": 12.0, "end": 20.5, "speed": 1.0}, ...],
  "captions": {"enabled": true, "word_level": true, "words_per_line": 3, "font": "Inter",
               "size": 86, "uppercase": true, "color": "#FFFFFF", "highlight": "#FFE02B",
               "stroke": 9, "stroke_color": "#101010", "y": 0.76, "mode": "caption"|"plain"},
  "subtitles": [{"start":0.0,"end":1.8,"text":"halo semua","hidden":false,
                 "words":[{"start":0.0,"end":0.4,"text":"halo"}, ...]}],
  "texts": [{"id":"t1","text":"HOOK","start":0,"end":5,"x":0.5,"y":0.22,"size":80,
             "color":"#FFFFFF","box":false,"box_color":"#000000","box_opacity":0.55,
             "bold":true,"uppercase":true,"rotation":0,"anim":"pop"|"fade"|"none",
             "stroke":8,"stroke_color":"#101010","align":"center"}],
  "logo": {"path":"","x":0.5,"y":0.93,"width":0.22,"opacity":1.0,"start":0,"end":0},
  "output": {"height":1920,"fps":30,"crf":20,"sharpen":true,"preset":"","normalize":true,
             "fade_in":0.0,"fade_out":0.0},
  "template": "podcast-blur"
}
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from . import framing as framing_module
from . import speech
from . import media
from .cancellation import OperationCancelled
from .config import CLIP_DIR, WORK_DIR, ffmpeg_exe, load_settings
from .fsutil import safe_replace, safe_rmtree

CANVAS_W = 1080
CANVAS_H = 1920

ProgressFn = __import__("typing").Callable[[float, str], None]


class EditorError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# template & gaya siap pakai
# --------------------------------------------------------------------------- #
CAPTION_STYLES: dict[str, dict[str, Any]] = {
    "hormozi-gold": {
        "label": "Hormozi Gold",
        "size": 92, "uppercase": True, "color": "#FFFFFF", "highlight": "#FFD700",
        "stroke": 10, "stroke_color": "#050508", "y": 0.76, "words_per_line": 3,
    },
    "mrbeast-neon": {
        "label": "MrBeast Neon",
        "size": 98, "uppercase": True, "color": "#FFFFFF", "highlight": "#00F0FF",
        "stroke": 11, "stroke_color": "#000000", "y": 0.74, "words_per_line": 3,
    },
    "storyteller": {
        "label": "Storyteller",
        "size": 78, "uppercase": False, "color": "#FFFFFF", "highlight": "#FFE36E",
        "stroke": 8, "stroke_color": "#08080C", "y": 0.78, "words_per_line": 4,
    },
    "viral-fire": {
        "label": "Viral Fire",
        "size": 92, "uppercase": True, "color": "#FFFFFF", "highlight": "#FF4D1C",
        "stroke": 10, "stroke_color": "#080302", "y": 0.75, "words_per_line": 3,
    },
    "karaoke-yellow": {
        "label": "Karaoke Kuning",
        "size": 88, "uppercase": True, "color": "#FFFFFF", "highlight": "#FFE02B",
        "stroke": 9, "stroke_color": "#0B0B10", "y": 0.76, "words_per_line": 3,
    },
    "mrbeast": {
        "label": "MrBeast Bold",
        "size": 104, "uppercase": True, "color": "#FFFFFF", "highlight": "#FF2D55",
        "stroke": 12, "stroke_color": "#000000", "y": 0.72, "words_per_line": 2,
    },
    "tiktok-neon": {
        "label": "TikTok Neon",
        "size": 84, "uppercase": True, "color": "#E9F7FF", "highlight": "#22D3EE",
        "stroke": 8, "stroke_color": "#0A1B22", "y": 0.78, "words_per_line": 3,
    },
    "clean-white": {
        "label": "Clean Minimal",
        "size": 68, "uppercase": False, "color": "#FFFFFF", "highlight": "#FFFFFF",
        "stroke": 5, "stroke_color": "#000000", "y": 0.84, "words_per_line": 6,
    },
    "podcast-box": {
        "label": "Podcast Box",
        "size": 74, "uppercase": False, "color": "#FFFFFF", "highlight": "#FFD166",
        "stroke": 6, "stroke_color": "#000000", "y": 0.8, "words_per_line": 4,
    },
    "cinema-green": {
        "label": "Cinema Green",
        "size": 82, "uppercase": True, "color": "#F4FFF8", "highlight": "#34D399",
        "stroke": 9, "stroke_color": "#04140D", "y": 0.86, "words_per_line": 3,
    },
}

TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "hormozi-gold", "name": "Hormozi Gold", "desc": "Crop penuh + subtitle emas Hormozi",
        "accent": ["#f59e0b", "#ffd700"],
        "layout": "crop", "background": {"type": "color", "color1": "#080910"},
        "captions": "hormozi-gold",
    },
    {
        "id": "podcast-split", "name": "Podcast 2 Pembicara",
        "desc": "Atas Pembicara 1, Bawah Pembicara 2 + subtitle tengah",
        "accent": ["#6366f1", "#ffd700"],
        "layout": "split", "background": {"type": "color", "color1": "#080910"},
        "captions": "hormozi-gold",
    },
    {
        "id": "podcast-blur", "name": "Podcast Blur", "desc": "Video utuh + background blur",
        "accent": ["#1f2937", "#7c5cff"],
        "layout": "fit", "background": {"type": "blur", "blur": 32, "darken": 0.14},
        "captions": "karaoke-yellow",
    },
    {
        "id": "mrbeast-crop", "name": "MrBeast Neon", "desc": "Crop penuh, caption neon & tebal",
        "accent": ["#ff2d55", "#00f0ff"],
        "layout": "crop", "background": {"type": "color", "color1": "#000000"},
        "captions": "mrbeast-neon",
    },
    {
        "id": "neon-tiktok", "name": "Neon TikTok", "desc": "Crop + highlight cyan",
        "accent": ["#0ea5e9", "#22d3ee"],
        "layout": "crop", "background": {"type": "color", "color1": "#05070d"},
        "captions": "tiktok-neon",
    },
    {
        "id": "gameplay-split", "name": "Streamer Gameplay",
        "desc": "Atas gameplay, bawah facecam host + caption di tengah",
        "accent": ["#7c5cff", "#22d3ee"],
        "layout": "gameplay", "background": {"type": "color", "color1": "#080910"},
        "captions": "hormozi-gold",
    },
    {
        "id": "gradient-studio", "name": "Gradient Studio", "desc": "Video utuh + background gradien",
        "accent": ["#7c5cff", "#22d3ee"],
        "layout": "fit", "background": {"type": "gradient", "color1": "#160f2e", "color2": "#7c5cff"},
        "captions": "storyteller",
    },
    {
        "id": "clean-vlog", "name": "Clean Vlog", "desc": "Minimalis, caption kecil rapi",
        "accent": ["#334155", "#e2e8f0"],
        "layout": "fit", "background": {"type": "blur", "blur": 22, "darken": 0.2},
        "captions": "clean-white",
    },
    {
        "id": "cinema-black", "name": "Cinema", "desc": "Letterbox hitam, caption hijau",
        "accent": ["#0b3d2e", "#34d399"],
        "layout": "black", "background": {"type": "color", "color1": "#000000"},
        "captions": "cinema-green",
    },
]

RESOLUTIONS = [
    {"id": "1080p", "label": "1080p (Full HD)", "height": 1920},
    {"id": "1440p", "label": "1440p (2K)", "height": 2560},
    {"id": "4k", "label": "4K (2160×3840)", "height": 3840},
]

MAX_SEGMENTS = 40
ANIMS = ("none", "fade", "pop")

# Tiga gaya hook overlay. Semua memakai kata yang benar-benar diucapkan
# (lihat app/speech.py) supaya teks di layar == suara.
HOOK_STYLES: dict[str, dict[str, Any]] = {
    "capsule": {
        "label": "Kapsul",
        "desc": "Satu baris tebal di kotak gelap, muncul sekali dengan animasi pop. Paling aman & paling mudah dibaca.",
        "size": 78, "uppercase": True, "anim": "pop",
        "box": True, "box_color": "#000000", "box_opacity": 0.55,
        "color": "#FFFFFF", "stroke": 8, "stroke_color": "#0B0B10", "y": 0.18,
    },
    "highlight": {
        "label": "Sorot kata",
        "desc": "Semua kata tampil, kata yang sedang diucapkan menyala kuning. Sinkron per kata dengan suara.",
        "size": 82, "uppercase": True, "anim": "pop",
        "box": False, "box_color": "#000000", "box_opacity": 0.5,
        "color": "#FFFFFF", "stroke": 10, "stroke_color": "#0B0B10", "y": 0.18,
    },
    "typewriter": {
        "label": "Ketik",
        "desc": "Kata muncul satu per satu mengikuti ucapan, seperti sedang diketik. Paling dramatis.",
        "size": 84, "uppercase": True, "anim": "fade",
        "box": True, "box_color": "#000000", "box_opacity": 0.45,
        "color": "#FFFFFF", "stroke": 10, "stroke_color": "#0B0B10", "y": 0.18,
    },
}


def hook_styles_payload() -> list[dict[str, Any]]:
    """Daftar gaya hook untuk UI (id, label, penjelasan)."""
    return [{"id": key, **{k: v for k, v in value.items()}} for key, value in HOOK_STYLES.items()]


def template_by_id(template_id: str) -> dict[str, Any] | None:
    return next((t for t in TEMPLATES if t["id"] == template_id), None)


def templates_payload() -> dict[str, Any]:
    return {
        "templates": TEMPLATES,
        "caption_styles": [{"id": k, **v} for k, v in CAPTION_STYLES.items()],
        "resolutions": RESOLUTIONS,
        "hook_styles": hook_styles_payload(),
        "canvas": {"w": CANVAS_W, "h": CANVAS_H},
        "max_segments": MAX_SEGMENTS,
    }


# --------------------------------------------------------------------------- #
# helper kecil
# --------------------------------------------------------------------------- #
def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _flag(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _hex(value: Any, default: str = "#FFFFFF") -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.upper()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", text):
        return "#" + "".join(ch * 2 for ch in text[1:]).upper()
    return default


_ASS_COLOR_CACHE: dict[tuple[str, int], str] = {}


def ass_color(hex_color: str, alpha: int = 0) -> str:
    key = (hex_color, alpha)
    if key in _ASS_COLOR_CACHE:
        return _ASS_COLOR_CACHE[key]
    value = _hex(hex_color).lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    result = f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"
    _ASS_COLOR_CACHE[key] = result
    return result


def ass_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:d}:{minutes:02d}:{secs:05.2f}"


def _escape(text: str) -> str:
    text = str(text or "").replace("\\", "\\\\").replace("{", "(").replace("}", ")")
    text = text.replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _ass_header(font: str, styles: Iterable[str]) -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {CANVAS_W}\n"
        f"PlayResY: {CANVAS_H}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + "".join(styles)
        + "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


# --------------------------------------------------------------------------- #
# segmen & pemetaan waktu
# --------------------------------------------------------------------------- #
def normalize_segments(
    raw: Any,
    clip: dict[str, Any],
    source_duration: float,
) -> list[dict[str, Any]]:
    """Validasi segmen: urut waktu sumber, tidak tumpang tindih, tidak di luar durasi."""
    clip_start = _clamp(_f(clip.get("start")), 0.0, max(0.0, source_duration - 0.5))
    clip_duration = max(1.0, _f(clip.get("duration"), 30.0))
    default_end = min(source_duration or clip_start + clip_duration, clip_start + clip_duration)
    segments: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw[:MAX_SEGMENTS]:
            if not isinstance(item, dict):
                continue
            start = _clamp(_f(item.get("start"), clip_start), 0.0, max(0.0, source_duration - 0.2))
            end = _clamp(_f(item.get("end"), start + 5), start + 0.2, source_duration or start + 5)
            speed = _clamp(_f(item.get("speed"), 1.0), 0.5, 2.0)
            if end - start < 0.2:
                continue
            segments.append({"start": round(start, 3), "end": round(end, 3), "speed": round(speed, 3)})
    if not segments:
        segments = [{"start": round(clip_start, 3), "end": round(default_end, 3), "speed": 1.0}]
    segments.sort(key=lambda s: s["start"])
    return segments


def segment_out_durations(segments: list[dict[str, Any]]) -> list[float]:
    return [max(0.05, (s["end"] - s["start"]) / max(0.5, s["speed"])) for s in segments]


def spec_duration(spec: dict[str, Any]) -> float:
    return round(sum(segment_out_durations(spec.get("segments") or [])), 3)


def with_out_ranges(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor = 0.0
    for seg, out_duration in zip(segments, segment_out_durations(segments)):
        result.append({**seg, "out_start": round(cursor, 3), "out_end": round(cursor + out_duration, 3)})
        cursor += out_duration
    return result


def src_to_out(seconds: float, segments: list[dict[str, Any]]) -> float | None:
    """Waktu sumber → waktu output (setelah trim & speed). None kalau di luar segmen."""
    for seg in with_out_ranges(segments):
        if seg["start"] <= seconds <= seg["end"]:
            return round(seg["out_start"] + (seconds - seg["start"]) / max(0.5, seg["speed"]), 3)
    return None


def out_to_src(seconds: float, segments: list[dict[str, Any]]) -> float | None:
    for seg in with_out_ranges(segments):
        if seg["out_start"] <= seconds <= seg["out_end"]:
            return round(seg["start"] + (seconds - seg["out_start"]) * max(0.5, seg["speed"]), 3)
    return None


# --------------------------------------------------------------------------- #
# subtitle otomatis
# --------------------------------------------------------------------------- #
def _words_from_transcript(transcript: dict[str, Any] | None,
                           segments: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Kata dari transkrip. Kalau ``segments`` dikirim, waktunya = waktu output."""
    if not transcript:
        return []
    if segments:
        return speech.words_in_output(transcript, segments)
    words: list[dict[str, Any]] = []
    for seg in transcript.get("segments") or []:
        seg_words = seg.get("words") or []
        if seg_words:
            for word in seg_words:
                text = (word.get("text") or "").strip()
                if text:
                    words.append({
                        "start": _f(word.get("start")),
                        "end": _f(word.get("end"), _f(word.get("start")) + 0.3),
                        "text": text,
                        "prob": _f(word.get("prob"), 1.0),
                    })
            continue
        tokens = (seg.get("text") or "").split()
        if not tokens:
            continue
        start, end = _f(seg.get("start")), _f(seg.get("end"))
        step = max(0.08, (end - start) / max(1, len(tokens)))
        for i, token in enumerate(tokens):
            words.append({"start": start + i * step, "end": start + (i + 1) * step,
                          "text": token, "prob": 0.5})
    words.sort(key=lambda w: w["start"])
    return words


def redistribute_words(text: str, start: float, end: float) -> list[dict[str, Any]]:
    """Bagi waktu baris caption ke kata-katanya (dipakai saat teks diedit user).

    Proporsional panjang kata, jadi kata panjang dapat waktu lebih lama —
    cukup akurat untuk karaoke dan tidak pernah menampilkan kata lama.
    """
    tokens = [tok for tok in (text or "").split() if tok.strip()]
    if not tokens:
        return []
    span = max(0.3, _f(end) - _f(start))
    weights = [max(2, len(tok)) for tok in tokens]
    total = sum(weights) or 1
    cursor = _f(start)
    words: list[dict[str, Any]] = []
    for token, weight in zip(tokens, weights):
        step = span * weight / total
        words.append({"text": token, "start": round(cursor, 3), "end": round(cursor + step, 3),
                      "prob": 0.8})
        cursor += step
    return words


def generate_subtitles(
    transcript: dict[str, Any] | None,
    segments: list[dict[str, Any]],
    words_per_line: int = 3,
    word_level: bool = True,
) -> list[dict[str, Any]]:
    """Buat baris subtitle di timeline OUTPUT dari transkrip + segmen aktif.

    Waktu kata dihitung sekali di ``speech.words_in_output`` (trim + speed),
    jadi caption, hook overlay, dan audio memakai sumber waktu yang sama —
    tidak ada lagi teks yang muncul sebelum/sesudah kata diucapkan.
    """
    mapped = _words_from_transcript(transcript, segments)
    if not mapped:
        return []

    lines: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    per_line = max(1, min(12, int(words_per_line or 3)))
    for i, word in enumerate(mapped):
        current.append(word)
        next_word = mapped[i + 1] if i + 1 < len(mapped) else None
        gap = (next_word["start"] - word["end"]) if next_word else 999
        ends_sentence = bool(re.search(r"[.!?…]$", word["text"]))
        if len(current) >= per_line or gap > 0.7 or ends_sentence or next_word is None:
            text = " ".join(w["text"] for w in current).strip()
            if text:
                line = {
                    "start": round(current[0]["start"], 3),
                    "end": round(current[-1]["end"] + 0.12, 3),
                    "text": text,
                    "hidden": False,
                    # kata paling tidak yakin di baris ini → UI bisa menandai
                    "conf": round(min(_f(w.get("prob"), 1.0) for w in current), 3),
                }
                if word_level:
                    line["words"] = [
                        {"start": round(w["start"], 3), "end": round(w["end"], 3),
                         "text": w["text"], "prob": round(_f(w.get("prob"), 1.0), 3)}
                        for w in current
                    ]
                lines.append(line)
            current = []
    return lines


def subtitles_to_srt(subtitles: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    index = 1
    for line in subtitles:
        if line.get("hidden"):
            continue
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, _f(line.get("start")))
        end = max(start + 0.2, _f(line.get("end"), start + 1.0))
        rows.append(f"{index}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{text}\n")
        index += 1
    return "\n".join(rows)


def _srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d},{ms:03d}"


def _clip_text_transcript(clip: dict[str, Any], source_duration: float = 0.0) -> dict[str, Any] | None:
    """Fallback word timing dari teks kandidat AI bila transkrip project tidak tersedia."""
    meta = clip.get("meta") if isinstance(clip.get("meta"), dict) else {}
    text = str(meta.get("transcript") or "").strip()
    tokens = text.split()
    if not tokens:
        return None
    start = max(0.0, _f(clip.get("start")))
    end = start + max(0.2, _f(clip.get("duration"), 30.0))
    if source_duration > 0:
        end = min(end, source_duration)
    if end - start < 0.2:
        return None
    step = (end - start) / len(tokens)
    words = [
        {
            "start": round(start + index * step, 3),
            "end": round(start + (index + 1) * step, 3),
            "text": token,
            "prob": 0.4,
        }
        for index, token in enumerate(tokens)
    ]
    return {
        "language": str(meta.get("language") or ""),
        "segments": [{"start": start, "end": end, "text": text, "words": words}],
    }


# --------------------------------------------------------------------------- #
# hook overlay: teks harus sama dengan yang diucapkan
# --------------------------------------------------------------------------- #
def build_hook_text(
    clip: dict[str, Any],
    transcript: dict[str, Any] | None,
    segments: list[dict[str, Any]],
    settings: dict[str, Any] | None = None,
    *,
    style: str | None = None,
    source: str | None = None,
    seconds: float | None = None,
) -> dict[str, Any] | None:
    """Bangun satu item teks "hook" yang sinkron dengan ucapan.

    ``source = "speech"`` (default) memakai kata pembuka yang benar-benar
    diucapkan, jadi mustahil muncul kata lain (mis. video bilang "saya" tapi
    teks "ada"). ``source = "title"`` memakai hook/judul clip, tapi waktunya
    ditempelkan ke kata yang cocok di transkrip supaya highlight tetap pas.
    """
    settings = settings or load_settings()
    hook = (clip.get("hook") or clip.get("title") or "").strip()
    style_id = style if style in HOOK_STYLES else str(settings.get("hook_style") or "highlight")
    if style_id not in HOOK_STYLES:
        style_id = "highlight"
    preset = HOOK_STYLES[style_id]

    total = spec_duration({"segments": segments}) or 0.0
    length = min(6.0, max(1.5, _f(seconds, _f(settings.get("hook_seconds"), 4.5))))
    if total:
        length = min(length, max(0.8, total))

    spoken = speech.words_in_output(transcript, segments)
    window = [w for w in spoken if w["start"] <= length + 0.4]
    spoken_hook = speech.hook_from_speech(window, max_words=int(settings.get("hook_words") or 12))

    want = source if source in ("speech", "title") else ""
    if not want:
        want = "speech" if spoken_hook["text"] else "title"

    text = ""
    words: list[dict[str, Any]] = []
    if want == "speech" and spoken_hook["text"]:
        text = spoken_hook["text"]
        words = [{"text": w["text"], "start": w["start"], "end": w["end"]}
                 for w in spoken_hook["words"]]
    elif hook:
        text = hook[:90]
        words = speech.align_words(text, window, start=0.0, end=length)
        words = [{"text": w["text"], "start": w["start"], "end": w["end"]} for w in words]
    else:
        return None

    if not text:
        return None
    start = round(max(0.0, _f(words[0]["start"])) if words else 0.0, 3)
    end = round(max(start + 1.0, min(length, _f(words[-1]["end"]) if words else length)), 3)
    entry: dict[str, Any] = {
        "id": "hook",
        "text": text,
        "hook_style": style_id,
        "hook_source": want,
        "start": start,
        "end": end,
        "x": 0.5,
        "y": _clamp(_f(settings.get("hook_y"), preset["y"]), 0.03, 0.97),
        "size": int(_clamp(_f(preset["size"]), 18, 160)),
        "color": preset["color"],
        "stroke": preset["stroke"],
        "stroke_color": preset["stroke_color"],
        "box": bool(preset["box"]),
        "box_color": preset["box_color"],
        "box_opacity": preset["box_opacity"],
        "bold": True,
        "uppercase": bool(preset["uppercase"]),
        "rotation": 0,
        "anim": preset["anim"],
        "align": "center",
    }
    if words:
        # tanda baca akhir kalimat tidak perlu ikut menyala di overlay hook
        last = words[-1]
        trimmed = str(last.get("text") or "").rstrip(" ,;:.!?…")
        if trimmed and trimmed != last.get("text"):
            words[-1] = {**last, "text": trimmed}
        entry["words"] = [w for w in words if str(w.get("text") or "").strip()]
        if entry["words"]:
            entry["text"] = entry["text"].rstrip(" ,;:.!?…") or entry["text"]
    return entry


def hook_report(
    clip: dict[str, Any],
    transcript: dict[str, Any] | None,
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bandingkan hook clip dengan ucapan: cocok atau tidak, plus alternatifnya."""
    spoken = speech.words_in_output(transcript, segments)
    info = speech.summarize(clip.get("hook") or clip.get("title") or "", spoken)
    info["has_transcript"] = bool(spoken)
    return info


# --------------------------------------------------------------------------- #
# spec default & normalisasi
# --------------------------------------------------------------------------- #
def default_spec(
    clip: dict[str, Any],
    settings: dict[str, Any] | None = None,
    transcript: dict[str, Any] | None = None,
    source_duration: float = 0.0,
) -> dict[str, Any]:
    settings = settings or load_settings()
    clip_meta = clip.get("meta") if isinstance(clip.get("meta"), dict) else {}
    if not transcript or not (transcript.get("segments") or []):
        transcript = _clip_text_transcript(clip, source_duration)
    segments = normalize_segments(
        [{"start": clip.get("start"), "end": _f(clip.get("start")) + _f(clip.get("duration"), 30),
          "speed": 1.0}],
        clip, source_duration,
    )
    focus = clip_meta.get("focus")
    framing_raw = clip_meta.get("framing") if isinstance(clip_meta.get("framing"), dict) else {}
    framing_kind = str(framing_raw.get("kind") or "")
    gameplay_spec = _gameplay_from_meta(framing_raw)
    split_spec = _split_from_meta(framing_raw)

    layout = str(settings.get("layout") or "fit")
    if settings.get("auto_framing", True):
        if framing_kind == "gameplay":
            layout = "gameplay"
        elif framing_kind == "split":
            layout = "split"
        elif framing_kind == "speaker" and framing_raw.get("ok") and layout not in ("black", "gameplay", "split"):
            layout = "crop"

    background = {"type": "blur", "blur": 30, "color1": "#0B0B12", "color2": "#7C5CFF", "darken": 0.12}
    if layout in ("crop", "gameplay", "split"):
        background["type"] = "color"

    default_style_id = str(settings.get("caption_style") or "karaoke-yellow")
    if default_style_id not in CAPTION_STYLES:
        default_style_id = "karaoke-yellow"
    caption_style = dict(CAPTION_STYLES[default_style_id])
    if layout == "gameplay":
        default_cap_y = round(gameplay_spec["split"], 2)
    elif layout == "split":
        default_cap_y = round(split_spec["split"], 2)
    else:
        default_cap_y = float(caption_style.get("y") or 0.76)

    captions = {
        "enabled": bool(settings.get("captions", True)) and clip_meta.get("captions") is not False,
        "word_level": True,
        "words_per_line": _i(settings.get("caption_words"), _i(caption_style.get("words_per_line"), 3)),
        "font": str(settings.get("caption_font") or "Inter"),
        "size": _i(caption_style["size"], 86),
        "uppercase": bool(settings.get("caption_uppercase", caption_style.get("uppercase", True))),
        "color": _hex(settings.get("caption_color"), caption_style.get("color", "#FFFFFF")),
        "highlight": _hex(settings.get("caption_highlight"), caption_style.get("highlight", "#FFE02B")),
        "stroke": _i(caption_style.get("stroke"), 9),
        "stroke_color": _hex(caption_style.get("stroke_color"), "#0B0B10"),
        "y": default_cap_y,
        "mode": "caption",
        "style": default_style_id,
        "mute_during_hook": True,
    }
    subtitles = generate_subtitles(
        transcript, segments, captions["words_per_line"], captions["word_level"]
    ) if captions["enabled"] else []
    if captions["enabled"] and not subtitles:
        fallback_transcript = _clip_text_transcript(clip, source_duration)
        if fallback_transcript:
            subtitles = generate_subtitles(
                fallback_transcript, segments, captions["words_per_line"], captions["word_level"]
            )
            if subtitles:
                transcript = fallback_transcript

    texts: list[dict[str, Any]] = []
    hook = (clip.get("hook") or "").strip()
    is_ai_clip = clip_meta.get("origin") == "ai" and not clip_meta.get("manual", False)
    show_hook_overlay = (
        bool(settings.get("ai_hook_overlay", True))
        if is_ai_clip
        else bool(settings.get("hook_overlay"))
    )
    if show_hook_overlay and hook:
        entry = build_hook_text(
            clip, transcript, segments, settings,
            source="title" if is_ai_clip else None,
        )
        if entry:
            texts.append(entry)

    export_height = _i(settings.get("export_height"), 1920)
    if export_height <= 2000:
        export_height = 1920
    elif export_height <= 3000:
        export_height = 2560
    else:
        export_height = 3840
    output = {
        "height": export_height,
        "width": int(round(export_height * 9 / 16 / 2) * 2),
        "fps": int(settings.get("fps") or 30),
        "crf": int(settings.get("crf") or 20),
        "sharpen": bool(settings.get("export_sharpen", True)),
        "normalize": bool(settings.get("normalize_audio", True)),
        "fade_in": 0.0,
        "fade_out": 0.0,
        "preset": "",
    }
    spec = {
        "version": 1,
        "framing_v": int(framing_raw.get("detector_v") or 0),
        "layout": layout if layout in ("fit", "crop", "gameplay", "split", "black") else "fit",
        "punch_in": bool(settings.get("punch_in", True)),
        "background": background,
        "crop_focus": _clamp(_f(focus, _f(settings.get("crop_focus"), 0.5)), 0.0, 1.0),
        "framing": _framing_from_meta(framing_raw),
        "gameplay": gameplay_spec,
        "split": split_spec,
        "segments": segments,
        "captions": captions,
        "subtitles": subtitles,
        "texts": texts,
        "logo": {"path": "", "x": 0.5, "y": 0.93, "width": 0.22, "opacity": 1.0,
                 "start": 0.0, "end": 0.0},
        "output": output,
        "template": "",
    }
    default_template = str(settings.get("default_template") or "")
    if default_template and template_by_id(default_template):
        spec = apply_template(spec, default_template)
        if settings.get("auto_framing", True):
            if framing_kind == "gameplay":
                spec["layout"] = "gameplay"
                spec["captions"]["y"] = round(gameplay_spec["split"], 2)
            elif framing_kind == "split":
                spec["layout"] = "split"
                spec["captions"]["y"] = round(split_spec["split"], 2)
        spec["output"] = output
    return spec


def _split_from_meta(raw: Any) -> dict[str, Any]:
    """Ambil koordinat 2 pembicara dari meta framing untuk layout Podcast Split."""
    sp = (raw.get("split") if isinstance(raw, dict) else None) or {}
    if not isinstance(sp, dict):
        sp = {}
    left_x = _clamp(_f(sp.get("left_x"), 0.28), 0.08, 0.48)
    left_y = _clamp(_f(sp.get("left_y"), 0.42), 0.12, 0.88)
    right_x = _clamp(_f(sp.get("right_x"), 0.72), 0.52, 0.92)
    right_y = _clamp(_f(sp.get("right_y"), 0.42), 0.12, 0.88)
    split = _clamp(_f(sp.get("split"), 0.50), 0.42, 0.58)
    return {
        "left_x": round(left_x, 4),
        "left_y": round(left_y, 4),
        "right_x": round(right_x, 4),
        "right_y": round(right_y, 4),
        "split": round(split, 3),
    }


def _split_from_spec(raw: Any, base: Any) -> dict[str, Any]:
    default = base if isinstance(base, dict) else _split_from_meta({})
    if not isinstance(raw, dict):
        return dict(default)
    return {
        "left_x": round(_clamp(_f(raw.get("left_x"), _f(default.get("left_x"), 0.28)), 0.08, 0.48), 4),
        "left_y": round(_clamp(_f(raw.get("left_y"), _f(default.get("left_y"), 0.42)), 0.12, 0.88), 4),
        "right_x": round(_clamp(_f(raw.get("right_x"), _f(default.get("right_x"), 0.72)), 0.52, 0.92), 4),
        "right_y": round(_clamp(_f(raw.get("right_y"), _f(default.get("right_y"), 0.42)), 0.12, 0.88), 4),
        "split": round(_clamp(_f(raw.get("split"), _f(default.get("split"), 0.50)), 0.42, 0.58), 3),
    }


def _gameplay_from_meta(raw: Any) -> dict[str, Any]:
    """Ambil koordinat facecam host dari meta framing untuk layout gameplay."""
    fc = (raw.get("facecam") if isinstance(raw, dict) else None) or {}
    if not isinstance(fc, dict):
        fc = {}
    cam_x = _clamp(_f(fc.get("x"), 0.16), 0.04, 0.96)
    cam_y = _clamp(_f(fc.get("y"), 0.78), 0.06, 0.94)
    cam_scale = _clamp(_f(fc.get("scale"), 0.32), 0.18, 0.65)
    default_game_x = 0.56 if cam_x < 0.35 else (0.44 if cam_x > 0.65 else 0.50)
    game_x = _clamp(_f(fc.get("game_x"), default_game_x), 0.0, 1.0)
    split = _clamp(_f(fc.get("split"), 0.62), 0.45, 0.75)
    corner = str(fc.get("corner") or ("kiri bawah" if cam_x < 0.5 else "kanan bawah"))[:32]
    return {
        "cam_x": round(cam_x, 4),
        "cam_y": round(cam_y, 4),
        "cam_scale": round(cam_scale, 4),
        "game_x": round(game_x, 4),
        "split": round(split, 3),
        "corner": corner,
    }


def _gameplay_from_spec(raw: Any, base: Any) -> dict[str, Any]:
    default = base if isinstance(base, dict) else _gameplay_from_meta({})
    if not isinstance(raw, dict):
        return dict(default)
    cam_x = _clamp(_f(raw.get("cam_x"), _f(default.get("cam_x"), 0.16)), 0.04, 0.96)
    cam_y = _clamp(_f(raw.get("cam_y"), _f(default.get("cam_y"), 0.78)), 0.06, 0.94)
    cam_scale = _clamp(_f(raw.get("cam_scale"), _f(default.get("cam_scale"), 0.32)), 0.18, 0.65)
    game_x = _clamp(_f(raw.get("game_x"), _f(default.get("game_x"), 0.5)), 0.0, 1.0)
    split = _clamp(_f(raw.get("split"), _f(default.get("split"), 0.62)), 0.45, 0.75)
    corner = str(raw.get("corner") or default.get("corner") or "")[:32]
    return {
        "cam_x": round(cam_x, 4),
        "cam_y": round(cam_y, 4),
        "cam_scale": round(cam_scale, 4),
        "game_x": round(game_x, 4),
        "split": round(split, 3),
        "corner": corner,
    }


def _framing_from_meta(raw: Any) -> dict[str, Any]:
    """Ambil hasil analisa pembicara dari meta clip jadi bagian spec."""
    if not isinstance(raw, dict) or not raw.get("segments") or not raw.get("ok"):
        return {"mode": "center", "segments": [], "moves": 0, "faces": 0, "coverage": 0.0}
    segments = []
    for item in raw.get("segments") or []:
        if not isinstance(item, dict):
            continue
        try:
            at = max(0.0, float(item.get("at")))
            x = _clamp(float(item.get("x")), 0.0, 1.0)
        except (TypeError, ValueError):
            continue
        segments.append({"at": round(at, 3), "x": round(x, 4)})
    if not segments:
        return {"mode": "center", "segments": [], "moves": 0, "faces": 0, "coverage": 0.0}
    return {
        "mode": "auto",
        "segments": sorted(segments, key=lambda seg: seg["at"])[:400],
        "moves": max(0, len(segments) - 1),
        "faces": int(raw.get("faces") or 0),
        "coverage": round(_clamp(_f(raw.get("coverage"), 0.0), 0.0, 1.0), 3),
    }


def _framing_from_spec(raw: Any, base: Any) -> dict[str, Any]:
    """Validasi bagian ``framing`` dari client (bisa juga untuk mematikan)."""
    default = base if isinstance(base, dict) else {"mode": "center", "segments": [], "moves": 0,
                                                   "faces": 0, "coverage": 0.0}
    if not isinstance(raw, dict):
        return dict(default)
    mode = "auto" if raw.get("mode") == "auto" else "center"
    segments = []
    for item in (raw.get("segments") or [])[:400]:
        if not isinstance(item, dict):
            continue
        try:
            segments.append({"at": round(max(0.0, _f(item.get("at"))), 3),
                             "x": round(_clamp(_f(item.get("x"), 0.5), 0.0, 1.0), 4)})
        except (TypeError, ValueError):
            continue
    segments.sort(key=lambda seg: seg["at"])
    if mode == "auto" and not segments:
        mode = "center"
    return {
        "mode": mode,
        "segments": segments,
        "moves": max(0, len(segments) - 1),
        "faces": int(raw.get("faces") or default.get("faces") or 0),
        "coverage": round(_clamp(_f(raw.get("coverage"), _f(default.get("coverage"), 0.0)), 0.0, 1.0), 3),
    }


def _existing_spec(clip: dict[str, Any]) -> dict[str, Any]:
    raw = clip.get("spec") or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def normalize_spec(
    spec: dict[str, Any] | None,
    clip: dict[str, Any],
    settings: dict[str, Any] | None = None,
    source_duration: float = 0.0,
    keep_subtitles: bool = True,
) -> dict[str, Any]:
    """Bersihkan & lengkapi spec dari client (bounding semua nilai)."""
    settings = settings or load_settings()
    base = default_spec(clip, settings, None, source_duration)
    if not isinstance(spec, dict):
        return base

    result = dict(base)
    result["framing_v"] = int(spec.get("framing_v") or base.get("framing_v") or 0)
    result["layout"] = (
        spec.get("layout")
        if spec.get("layout") in ("fit", "crop", "gameplay", "split", "black")
        else base["layout"]
    )
    result["punch_in"] = _flag(spec.get("punch_in"), base.get("punch_in", True))
    result["crop_focus"] = _clamp(_f(spec.get("crop_focus"), base["crop_focus"]), 0.0, 1.0)
    result["framing"] = _framing_from_spec(spec.get("framing"), base.get("framing"))
    result["gameplay"] = _gameplay_from_spec(spec.get("gameplay"), base.get("gameplay"))
    result["split"] = _split_from_spec(spec.get("split"), base.get("split"))
    result["template"] = str(spec.get("template") or "")[:40]
    result["segments"] = normalize_segments(spec.get("segments"), clip, source_duration)

    background = spec.get("background") if isinstance(spec.get("background"), dict) else {}
    result["background"] = {
        "type": background.get("type") if background.get("type") in ("blur", "gradient", "color") else "blur",
        "blur": _clamp(_f(background.get("blur"), 30), 0, 60),
        "color1": _hex(background.get("color1"), "#0B0B12"),
        "color2": _hex(background.get("color2"), "#7C5CFF"),
        "darken": _clamp(_f(background.get("darken"), 0.12), 0.0, 0.7),
    }

    captions = spec.get("captions") if isinstance(spec.get("captions"), dict) else {}
    merged_captions = dict(base["captions"])
    merged_captions.update({
        "enabled": _flag(captions.get("enabled"), base["captions"]["enabled"]),
        "word_level": _flag(captions.get("word_level"), True),
        "words_per_line": int(_clamp(_f(captions.get("words_per_line"), 3), 1, 12)),
        "font": str(captions.get("font") or base["captions"]["font"])[:60],
        "size": int(_clamp(_f(captions.get("size"), base["captions"]["size"]), 30, 150)),
        "uppercase": _flag(captions.get("uppercase"), True),
        "color": _hex(captions.get("color"), base["captions"]["color"]),
        "highlight": _hex(captions.get("highlight"), base["captions"]["highlight"]),
        "stroke": int(_clamp(_f(captions.get("stroke"), 9), 0, 20)),
        "stroke_color": _hex(captions.get("stroke_color"), "#0B0B10"),
        "y": _clamp(_f(captions.get("y"), base["captions"]["y"]), 0.05, 0.97),
        "mode": captions.get("mode") if captions.get("mode") in ("caption", "plain", "box") else "caption",
        "style": str(captions.get("style") or base["captions"].get("style") or "custom")[:40],
        "mute_during_hook": _flag(captions.get("mute_during_hook"), True),
    })
    result["captions"] = merged_captions

    subtitles: list[dict[str, Any]] = []
    if keep_subtitles and isinstance(spec.get("subtitles"), list):
        total = spec_duration(result) + 2.0
        for line in spec["subtitles"][:400]:
            if not isinstance(line, dict):
                continue
            text = str(line.get("text") or "").strip()[:300]
            start = _clamp(_f(line.get("start")), 0.0, total)
            end = _clamp(_f(line.get("end"), start + 1.5), start + 0.15, total)
            if not text:
                continue
            entry: dict[str, Any] = {
                "start": round(start, 3), "end": round(end, 3), "text": text,
                "hidden": _flag(line.get("hidden"), False),
            }
            words = line.get("words")
            if isinstance(words, list) and words:
                cleaned: list[dict[str, Any]] = []
                for word in words[:20]:
                    if not isinstance(word, dict):
                        continue
                    wtext = str(word.get("text") or "").strip()[:60]
                    if not wtext:
                        continue
                    wstart = _clamp(_f(word.get("start"), start), start, end)
                    wend = _clamp(_f(word.get("end"), wstart + 0.2), wstart + 0.05, end)
                    cleaned.append({"start": round(wstart, 3), "end": round(wend, 3), "text": wtext,
                                    "prob": round(_f(word.get("prob"), 1.0), 3)})
                if cleaned:
                    entry["words"] = cleaned
            conf = line.get("conf")
            if conf is not None:
                entry["conf"] = round(_clamp(_f(conf), 0.0, 1.0), 3)
            subtitles.append(entry)
        subtitles.sort(key=lambda s: s["start"])
    elif keep_subtitles:
        # spec tidak mengirim subtitle → pertahankan yang tersimpan di clip
        previous = _existing_spec(clip)
        subtitles = previous.get("subtitles") or base["subtitles"] or []
    else:
        subtitles = []
    result["subtitles"] = subtitles

    texts: list[dict[str, Any]] = []
    duration = spec_duration(result)
    if isinstance(spec.get("texts"), list):
        for idx, item in enumerate(spec["texts"][:20]):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()[:180]
            if not text:
                continue
            start = _clamp(_f(item.get("start"), 0.0), 0.0, max(0.1, duration))
            end = _clamp(_f(item.get("end"), start + 3.0), start + 0.2, max(start + 0.2, duration))
            entry: dict[str, Any] = {
                "id": str(item.get("id") or f"t{idx + 1}")[:24],
                "text": text,
                "start": round(start, 3),
                "end": round(end, 3),
                "x": _clamp(_f(item.get("x"), 0.5), 0.02, 0.98),
                "y": _clamp(_f(item.get("y"), 0.2), 0.03, 0.97),
                "size": int(_clamp(_f(item.get("size"), 80), 18, 160)),
                "color": _hex(item.get("color"), "#FFFFFF"),
                "stroke": int(_clamp(_f(item.get("stroke"), 8), 0, 20)),
                "stroke_color": _hex(item.get("stroke_color"), "#0B0B10"),
                "box": _flag(item.get("box"), False),
                "box_color": _hex(item.get("box_color"), "#000000"),
                "box_opacity": _clamp(_f(item.get("box_opacity"), 0.55), 0.0, 1.0),
                "bold": _flag(item.get("bold"), True),
                "uppercase": _flag(item.get("uppercase"), True),
                "rotation": _clamp(_f(item.get("rotation"), 0), -45, 45),
                "anim": item.get("anim") if item.get("anim") in ANIMS else "fade",
                "align": "center",
            }
            # gaya hook (3 pilihan) + waktu ucapannya, hanya untuk teks hook
            style = item.get("hook_style")
            if style in HOOK_STYLES:
                entry["hook_style"] = style
            words_raw = item.get("words")
            if isinstance(words_raw, list) and words_raw:
                words_clean: list[dict[str, Any]] = []
                for word in words_raw[:40]:
                    if not isinstance(word, dict):
                        continue
                    wtext = str(word.get("text") or "").strip()[:60]
                    if not wtext:
                        continue
                    wstart = _clamp(_f(word.get("start"), start), start, end)
                    wend = _clamp(_f(word.get("end"), wstart + 0.2), wstart + 0.05, end)
                    words_clean.append({"text": wtext, "start": round(wstart, 3),
                                        "end": round(wend, 3)})
                if words_clean:
                    entry["words"] = words_clean
            source = item.get("hook_source")
            if source in ("speech", "title"):
                entry["hook_source"] = source
            texts.append(entry)
    result["texts"] = texts

    logo = spec.get("logo") if isinstance(spec.get("logo"), dict) else {}
    logo_path = str(logo.get("path") or "")
    if logo_path and not Path(logo_path).exists():
        logo_path = ""
    result["logo"] = {
        "path": logo_path,
        "x": _clamp(_f(logo.get("x"), 0.5), 0.0, 1.0),
        "y": _clamp(_f(logo.get("y"), 0.93), 0.0, 1.0),
        "width": _clamp(_f(logo.get("width"), 0.22), 0.05, 1.0),
        "opacity": _clamp(_f(logo.get("opacity"), 1.0), 0.05, 1.0),
        "start": max(0.0, _f(logo.get("start"), 0.0)),
        "end": max(0.0, _f(logo.get("end"), 0.0)),
    }

    output = spec.get("output") if isinstance(spec.get("output"), dict) else {}
    height = _i(output.get("height"), 1920)
    if height <= 2000:
        height = 1920
    elif height <= 3000:
        height = 2560
    else:
        height = 3840
    fps = _i(output.get("fps"), 30)
    result["output"] = {
        "height": height,
        "width": int(round(height * 9 / 16 / 2) * 2),
        "fps": fps if fps in (24, 25, 30, 50, 60) else 30,
        "crf": int(_clamp(_f(output.get("crf"), 20), 14, 32)),
        "sharpen": _flag(output.get("sharpen"), True),
        "normalize": _flag(output.get("normalize"), True),
        "fade_in": _clamp(_f(output.get("fade_in"), 0.0), 0.0, 3.0),
        "fade_out": _clamp(_f(output.get("fade_out"), 0.0), 0.0, 3.0),
        "preset": "",
    }
    return result


def apply_template(spec: dict[str, Any], template_id: str) -> dict[str, Any]:
    """Terapkan template ke spec (tanpa mengubah segmen & subtitle)."""
    template = template_by_id(template_id)
    if not template:
        return spec
    style_id = template.get("captions") or "karaoke-yellow"
    style = CAPTION_STYLES.get(style_id, CAPTION_STYLES["karaoke-yellow"])
    spec = json.loads(json.dumps(spec))
    spec["template"] = template_id
    spec["layout"] = template["layout"]
    spec["background"] = {**spec.get("background", {}), **template.get("background", {})}
    captions = spec.setdefault("captions", {})
    if template["layout"] == "gameplay":
        cap_y = round(_f((spec.get("gameplay") or {}).get("split"), 0.62), 2)
    elif template["layout"] == "split":
        cap_y = round(_f((spec.get("split") or {}).get("split"), 0.50), 2)
    else:
        cap_y = style["y"]
    captions.update({
        "size": style["size"],
        "uppercase": style["uppercase"],
        "color": style["color"],
        "highlight": style["highlight"],
        "stroke": style["stroke"],
        "stroke_color": style["stroke_color"],
        "y": cap_y,
        "words_per_line": style["words_per_line"],
        "style": style_id,
        "mode": "caption",
    })
    return spec


# --------------------------------------------------------------------------- #
# ASS: subtitle & overlay teks
# --------------------------------------------------------------------------- #
def _snap_word_times(words: list[dict[str, Any]], start: float, end: float) -> list[tuple[float, float]]:
    """Timing kata yang rapat & berurutan supaya caption tidak hilang-muncul.

    - setiap kata dibatasi ke rentang barisnya,
    - akhir kata = awal kata berikutnya (tanpa celah),
    - kata terakhir berakhir di akhir baris,
    - overlap 30 ms supaya pergantian kata mulus (tidak ada frame kosong).
    """
    starts: list[float] = []
    for word in words:
        value = _f(word.get("start"), start)
        starts.append(min(max(value, start), end))
    for i in range(1, len(starts)):
        starts[i] = max(starts[i], starts[i - 1])
    result: list[tuple[float, float]] = []
    for i, wstart in enumerate(starts):
        wend = starts[i + 1] if i + 1 < len(starts) else end
        if wend <= wstart:
            wend = min(end, wstart + 0.12)
        if wend <= wstart:
            continue
        result.append((wstart, min(end + 0.5, wend + 0.03)))
    return result


def build_captions_ass(
    subtitles: list[dict[str, Any]],
    captions: dict[str, Any],
    out_path: str | Path,
) -> bool:
    lines: list[str] = []
    font = str(captions.get("font") or "Inter")
    size = int(captions.get("size") or 86)
    uppercase = bool(captions.get("uppercase"))
    color = ass_color(captions.get("color") or "#FFFFFF")
    highlight = ass_color(captions.get("highlight") or "#FFE02B")
    stroke = int(captions.get("stroke") or 9)
    stroke_color = _hex(captions.get("stroke_color"), "#0B0B10")
    y = _clamp(_f(captions.get("y"), 0.76), 0.05, 0.97)
    pos_y = int(round(y * CANVAS_H))
    word_level = bool(captions.get("word_level", True))
    mode = captions.get("mode") or "caption"

    if mode == "box":
        border_style, back, outline, shadow = 3, ass_color("#000000", 0x40), stroke, 0
    elif mode == "plain":
        border_style, back, outline, shadow = 1, ass_color("#000000", 0x80), stroke, 0
    else:
        border_style, back, outline, shadow = 1, ass_color("#000000", 0x80), stroke, 3

    style_lines = [
        f"Style: Cap,{font},{size},{color},&H000000FF,{ass_color(stroke_color)},{back},"
        f"-1,0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,60,60,60,1\n"
    ]

    for line in subtitles:
        if line.get("hidden"):
            continue
        start = max(0.0, _f(line.get("start")))
        end = max(start + 0.15, _f(line.get("end"), start + 1.0))
        words = line.get("words") or []
        # posisi selalu sama; fade HANYA di awal baris supaya caption tidak
        # berkedip setiap kali kata berganti.
        head = f"{{\\an2\\pos({CANVAS_W // 2},{pos_y})\\fad(70,0)}}"
        head_plain = f"{{\\an2\\pos({CANVAS_W // 2},{pos_y})}}"

        if not word_level or not words:
            text = _escape(line.get("text") or "")
            if uppercase:
                text = text.upper()
            if not text:
                continue
            lines.append(
                f"Dialogue: 0,{ass_ts(start)},{ass_ts(end)},Cap,,0,0,0,,{head}{{\\c{color}}}{text}"
            )
            continue

        for idx, (wstart, wend) in enumerate(_snap_word_times(words, start, end)):
            parts: list[str] = []
            for j, other in enumerate(words):
                chunk = _escape(other.get("text") or "")
                if uppercase:
                    chunk = chunk.upper()
                if not chunk:
                    continue
                if j == idx:
                    # kata yang sedang diucapkan: warna highlight + sedikit lebih besar
                    parts.append(
                        f"{{\\c{highlight}\\fscx106\\fscy106}}{chunk}{{\\fscx100\\fscy100}}"
                    )
                else:
                    # kata lain memakai warna dasar eksplisit; jangan pakai \\r karena
                    # itu membuang \\pos sehingga caption melompat/berkedip
                    parts.append(f"{{\\c{color}}}{chunk}")
            if not parts:
                continue
            prefix = head if idx == 0 else head_plain
            lines.append(
                f"Dialogue: 0,{ass_ts(wstart)},{ass_ts(wend)},Cap,,0,0,0,,{prefix}" + " ".join(parts)
            )

    Path(out_path).write_text(
        _ass_header(font, style_lines) + "\n".join(lines) + "\n", encoding="utf-8"
    )
    return bool(lines)


def build_overlays_ass(texts: list[dict[str, Any]], out_path: str | Path, font: str = "Inter") -> bool:
    lines: list[str] = []
    styles: list[str] = [
        f"Style: Txt,{font},80,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,8,4,5,0,0,0,1\n",
        f"Style: TxtBox,{font},80,&H00FFFFFF,&H000000FF,&H00101010,&H60000000,"
        f"-1,0,0,0,100,100,0,0,3,26,0,5,0,0,0,1\n",
    ]
    for item in texts:
        text = _escape(item.get("text") or "")
        if not text:
            continue
        if item.get("uppercase"):
            text = text.upper()
        style_id = item.get("hook_style")
        start = max(0.0, _f(item.get("start")))
        end = max(start + 0.2, _f(item.get("end"), start + 3.0))
        x = int(round(_clamp(_f(item.get("x"), 0.5), 0.0, 1.0) * CANVAS_W))
        y = int(round(_clamp(_f(item.get("y"), 0.2), 0.0, 1.0) * CANVAS_H))
        size = int(_clamp(_f(item.get("size"), 80), 10, 200))
        rotation = _clamp(_f(item.get("rotation")), -45, 45)
        bold = -1 if item.get("bold", True) else 0
        style = "TxtBox" if item.get("box") else "Txt"
        overrides = [
            f"\\an5\\pos({x},{y})",
            f"\\fs{size}",
            f"\\c{ass_color(item.get('color') or '#FFFFFF')}",
            f"\\bord{int(_clamp(_f(item.get('stroke'), 8), 0, 20))}",
            f"\\3c{ass_color(item.get('stroke_color') or '#0B0B10')}",
            f"\\b{bold}",
        ]
        if rotation:
            overrides.append(f"\\frz{rotation:.1f}")
        if style == "TxtBox":
            overrides.append(f"\\4c{ass_color(item.get('box_color') or '#000000')}")
            overrides.append(
                f"\\4a&H{int(round((1 - _clamp(_f(item.get('box_opacity'), 0.55), 0.0, 1.0)) * 255)):02X}&"
            )
        anim = item.get("anim") or "fade"
        if anim == "pop":
            overrides.append("\\fad(120,180)\\fscx55\\fscy55\\t(0,180,\\fscx100\\fscy100)")
        elif anim == "fade":
            overrides.append("\\fad(200,250)")
        words = item.get("words") or []
        if style_id in ("highlight", "typewriter") and len(words) >= 2:
            lines.extend(_hook_word_lines(item, words, start, end, style, overrides, text,
                                          uppercase=bool(item.get("uppercase"))))
            continue
        lines.append(
            f"Dialogue: 0,{ass_ts(start)},{ass_ts(end)},{style},,0,0,0,,{{{''.join(overrides)}}}{text}"
        )
    Path(out_path).write_text(_ass_header(font, styles) + "\n".join(lines) + "\n", encoding="utf-8")
    return bool(lines)


def _hook_word_lines(
    item: dict[str, Any],
    words: list[dict[str, Any]],
    start: float,
    end: float,
    style: str,
    overrides: list[str],
    full_text: str,
    *,
    uppercase: bool,
) -> list[str]:
    """Event ASS per kata untuk gaya "sorot kata" dan "ketik".

    Semua event memakai ``\pos`` yang sama supaya teks tidak melompat
    (penyebab caption "berkedip"), dan fade hanya dipakai sekali di event
    pertama per kata — bukan di setiap perubahan.
    """
    base = "".join(o for o in overrides if not o.startswith(("\\fad", "\\fscx", "\\t(")))
    highlight = ass_color(item.get("highlight_color") or "#FFE02B")
    color = ass_color(item.get("color") or "#FFFFFF")
    timing = _snap_word_times(words, start, end)
    chunks = [_escape(w.get("text") or "") for w in words]
    if uppercase:
        chunks = [chunk.upper() for chunk in chunks]
    events: list[str] = []
    wants_pop = (item.get("anim") or "pop") == "pop"
    pop = "\\fad(90,0)\\fscx80\\fscy80\\t(0,120,\\fscx100\\fscy100)" if wants_pop else "\\fad(90,0)"

    style_id = item.get("hook_style")
    for idx, (wstart, wend) in enumerate(timing):
        if idx >= len(chunks):
            break
        # kata yang sedang diucapkan "pop" sekali (animasi di dalam event itu
        # saja) supaya kata lain tidak ikut berkedip.
        current = (
            f"{{\\c{highlight}\\fscx114\\fscy114\\t(0,110,\\fscx100\\fscy100)}}"
            f"{chunks[idx]}"
        )
        if style_id == "typewriter":
            parts = [f"{{\\c{color}}}{chunk}" for chunk in chunks[:idx]]
            parts.append(current)
            body = " ".join(parts)
        else:
            parts = []
            for j, chunk in enumerate(chunks):
                if not chunk:
                    continue
                if j == idx:
                    parts.append(current)
                elif j < idx:
                    parts.append(f"{{\\c{color}}}{chunk}")
                else:
                    parts.append(f"{{\\c{color}\\alpha&H60&}}{chunk}{{\\alpha&H00&}}")
            body = " ".join(parts)
        intro = pop if idx == 0 else ""
        ends = min(end, wend)
        if ends <= wstart:
            continue
        events.append(
            f"Dialogue: 0,{ass_ts(wstart)},{ass_ts(ends)},{style},,0,0,0,,"
            f"{{{base}{intro}}}{body}"
        )
    if not events:
        events.append(
            f"Dialogue: 0,{ass_ts(start)},{ass_ts(end)},{style},,0,0,0,,"
            f"{{{''.join(overrides)}}}{full_text}"
        )
    # sisa waktu setelah kata terakhir: teks penuh tetap tampil sampai item.end
    last_end = timing[-1][1] if timing else start
    if style_id == "highlight" and last_end < end - 0.05:
        body = " ".join(f"{{\\c{color}}}{chunk}" for chunk in chunks)
        events.append(
            f"Dialogue: 0,{ass_ts(last_end)},{ass_ts(end)},{style},,0,0,0,,{{{base}}}{body}"
        )
    return events


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def _punch_in_intervals(spec: dict[str, Any], seg_out_start: float, seg_duration: float) -> list[tuple[float, float]]:
    """Rencanakan interval Punch-In Zoom relatif ke timeline output segmen.

    Subtitle disimpan dalam waktu output (setelah trim dan perubahan speed),
    sedangkan ``segment.start`` memakai waktu sumber. ``seg_out_start`` menjaga
    momen penekanan tetap sejajar saat klip berisi beberapa segmen.

    - Zoom pembuka saat hook awal (detik 0.15 s/d 2.35)
    - Zoom penekanan suara pada baris subtitle yang mengandung tanda seru/tanya
      atau kata kunci penegas, berjarak minimal 6 detik agar tidak memusingkan mata.
    """
    if not _flag(spec.get("punch_in"), True) or seg_duration < 4.0:
        return []
    intervals: list[tuple[float, float]] = [(0.15, min(2.35, seg_duration * 0.40))]
    last_end = intervals[0][1]
    subs = spec.get("subtitles") if isinstance(spec.get("subtitles"), list) else []
    emphasis_re = re.compile(
        r"(\!|\?|rahasia|ternyata|wajib|jangan|bahaya|gila|parah|penting|fakta|bocor|uang|cuan|juta|miliar)",
        re.IGNORECASE,
    )
    for line in subs:
        if not isinstance(line, dict) or line.get("hidden"):
            continue
        st = _f(line.get("start")) - seg_out_start
        en = _f(line.get("end")) - seg_out_start
        text = str(line.get("text") or "")
        if st < last_end + 5.5 or st <= 0.5 or st >= seg_duration - 1.2:
            continue
        if emphasis_re.search(text) or len(intervals) == 1 and st >= 6.5:
            dur = _clamp(en - st, 1.4, 2.3)
            intervals.append((round(st, 2), round(min(seg_duration - 0.3, st + dur), 2)))
            last_end = st + dur
            if len(intervals) >= 4:
                break
    return intervals


def _layout_chain(layout: str, focus: float, background: dict[str, Any], fps: int,
                  width: int = CANVAS_W, height: int = CANVAS_H,
                  framing: list[dict[str, Any]] | None = None,
                  gameplay: dict[str, Any] | None = None,
                  split_spec: dict[str, Any] | None = None,
                  punch_intervals: list[tuple[float, float]] | None = None) -> str:
    """Filter chain yang menghasilkan [vbase] berukuran canvas (1080x1920)."""
    focus = _clamp(focus, 0.0, 1.0)
    bg_type = background.get("type", "blur")
    darken = _clamp(_f(background.get("darken"), 0.12), 0.0, 0.7)
    fit_fg = (
        f"[0:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease:"
        f"force_divisible_by=2,setsar=1[fgs]"
    )

    if layout == "split":
        # Mode Podcast 2 Pembicara (Stacked Split — OpenShorts split_layout.py):
        # Bagian ATAS menampilkan Pembicara 1 (kiri), bagian BAWAH menampilkan Pembicara 2 (kanan).
        sp = split_spec if isinstance(split_spec, dict) else {}
        split_ratio = _clamp(_f(sp.get("split"), 0.50), 0.42, 0.58)
        top_h = max(300, min(height - 300, int(round(height * split_ratio / 2) * 2)))
        bot_h = height - top_h
        left_x = _clamp(_f(sp.get("left_x"), 0.28), 0.08, 0.48)
        left_y = _clamp(_f(sp.get("left_y"), 0.42), 0.12, 0.88)
        right_x = _clamp(_f(sp.get("right_x"), 0.72), 0.52, 0.92)
        right_y = _clamp(_f(sp.get("right_y"), 0.42), 0.12, 0.88)
        return (
            f"[0:v]fps={fps},split=2[topsrc][botsrc];"
            f"[topsrc]scale={width}:{top_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{top_h}:x='clip((iw*{left_x:.4f})-({width}/2),0,iw-{width})':"
            f"y='clip((ih*{left_y:.4f})-({top_h}*0.44),0,ih-{top_h})',setsar=1[vtop];"
            f"[botsrc]scale={width}:{bot_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{bot_h}:x='clip((iw*{right_x:.4f})-({width}/2),0,iw-{width})':"
            f"y='clip((ih*{right_y:.4f})-({bot_h}*0.44),0,ih-{bot_h})',setsar=1[vbot];"
            f"[vtop][vbot]vstack=inputs=2,"
            f"drawbox=x=0:y={top_h - 3}:w={width}:h=6:color=0xFFD700@0.90:t=fill,setsar=1[vbase]"
        )

    if layout == "gameplay":
        # Mode Gameplay (Streamer / YouTuber Gamer):
        # Bagian ATAS menampilkan gameplay, bagian BAWAH menampilkan facecam host.
        gp = gameplay if isinstance(gameplay, dict) else {}
        split_ratio = _clamp(_f(gp.get("split"), 0.62), 0.45, 0.75)
        top_h = max(200, min(height - 200, int(round(height * split_ratio / 2) * 2)))
        bot_h = height - top_h
        game_x = _clamp(_f(gp.get("game_x"), 0.5), 0.0, 1.0)
        cam_x = _clamp(_f(gp.get("cam_x"), 0.16), 0.04, 0.96)
        cam_y = _clamp(_f(gp.get("cam_y"), 0.78), 0.06, 0.94)
        cam_scale = _clamp(_f(gp.get("cam_scale"), 0.26), 0.18, 0.65)
        norm_w, norm_h = 1920, 1080
        crop_w = max(160, min(norm_w, int(round(norm_w * cam_scale / 2) * 2)))
        crop_h = max(120, min(norm_h, int(round(crop_w * bot_h / max(1, width) / 2) * 2)))
        crop_x = int(round(_clamp(cam_x * norm_w - crop_w / 2.0, 0.0, float(norm_w - crop_w))))
        # Geser sedikit ke bawah (0.465 vs 0.50) agar dagu & mikrofon host masuk proporsional
        crop_y = int(round(_clamp(cam_y * norm_h - crop_h * 0.465, 0.0, float(norm_h - crop_h))))
        return (
            f"[0:v]fps={fps},split=2[topsrc][botsrc];"
            f"[topsrc]scale={width}:{top_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{top_h}:x='(iw-{width})*{game_x:.4f}':y='(ih-{top_h})/2',setsar=1[vtop];"
            f"[botsrc]scale={norm_w}:{norm_h}:force_original_aspect_ratio=increase,"
            f"crop={norm_w}:{norm_h},crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
            f"scale={width}:{bot_h}:flags=lanczos,setsar=1[vbot];"
            f"[vtop][vbot]vstack=inputs=2,"
            f"drawbox=x=0:y={top_h - 3}:w={width}:h=6:color=0x00D2FF@0.92:t=fill,setsar=1[vbase]"
        )

    if layout == "crop":
        # Framing otomatis: kamera mengikuti pembicara (ekspresi berbasis waktu).
        # Tanpa framing: satu titik fokus tetap seperti sebelumnya.
        x_expr = framing_module.crop_expression(framing, default=focus) if framing else f"{focus:.3f}"
        base_crop = (
            f"[0:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:x='(iw-{width})*({x_expr})':y='(ih-{height})/2',setsar=1"
        )
        if punch_intervals:
            # Dynamic Punch-In Zoom (1.11x crop tengah saat hook awal & penekanan kata)
            cond = "+".join(f"between(t,{st:.2f},{en:.2f})" for st, en in punch_intervals)
            pw = int(round(width / 1.11 / 2) * 2)
            ph = int(round(height / 1.11 / 2) * 2)
            px = (width - pw) // 2
            py = int(round((height - ph) * 0.38))
            return (
                f"{base_crop},split=2[vnorm][vpunch];"
                f"[vpunch]crop={pw}:{ph}:{px}:{py},scale={width}:{height}:flags=lanczos,setsar=1[vzoomed];"
                f"[vnorm][vzoomed]overlay=0:0:enable='gt({cond},0)',setsar=1[vbase]"
            )
        return f"{base_crop}[vbase]"

    if layout == "black":
        return (
            f"[0:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease:"
            f"force_divisible_by=2,setsar=1,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[vbase]"
        )

    if bg_type == "gradient":
        color1 = _hex(background.get("color1"), "#160F2E")[1:]
        color2 = _hex(background.get("color2"), "#7C5CFF")[1:]
        bg = (
            f"gradients=s={width}x{height}:c0=0x{color1}:c1=0x{color2}:"
            f"x0=0:y0=0:x1={width}:y1={height}:speed=0.004,format=yuv420p[bgb]"
        )
        return f"{bg};{fit_fg};[bgb][fgs]overlay=(W-w)/2:(H-h)/2:shortest=1[vbase]"

    if bg_type == "color":
        color1 = _hex(background.get("color1"), "#0B0B12")[1:]
        bg = f"color=c=0x{color1}:s={width}x{height}:r={fps}[bgb]"
        return f"{bg};{fit_fg};[bgb][fgs]overlay=(W-w)/2:(H-h)/2:shortest=1[vbase]"

    # default: blur background (video utuh di tengah)
    blur = _clamp(_f(background.get("blur"), 30), 0, 60)
    return (
        f"[0:v]fps={fps},split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma={blur:.1f},"
        f"eq=brightness=-{darken:.2f}:saturation=1.12,setsar=1[bgb];"
        f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease:"
        f"force_divisible_by=2,setsar=1[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[vbase]"
    )


def _visual_input(
    source_path: Path,
    info: media.MediaInfo | None,
    start: float,
    duration: float,
    workdir: Path,
    fps: int,
    width: int,
    height: int,
    background: dict[str, Any] | None = None,
    force_still: bool = False,
) -> tuple[list[str], str]:
    """Siapkan input video untuk satu segmen.

    Tiga kemungkinan sumber:
    1. video normal          → potong langsung dari file sumber (video = input 0, audio = input 0)
    2. gambar statis / cover → frame dijadikan PNG lalu di-loop, audio dari file sumber (input 1)
    3. audio saja            → kanvas gradien sebagai video, audio dari file sumber (input 1)

    Tanpa penanganan ini, ffmpeg hanya menghasilkan suara (atau gagal) sehingga
    klip jadi "audio saja tanpa gambar".
    """
    if not force_still and (info is None or info.has_real_video):
        return (
            ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source_path)],
            "0",
        )

    audio_in = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source_path)]
    cover: Path | None = None
    if info.has_video:
        cover = workdir / "cover.jpg"
        if not cover.exists() or cover.stat().st_size < 256:
            # frame terakhir yang masih ada (video bisa lebih pendek dari audio)
            at = 0.0
            if force_still and info.duration:
                at = max(0.0, min(start, info.duration - 0.5))
            try:
                media.thumb(source_path, at, cover, width=max(1080, min(2160, info.width or 1080)))
            except Exception:
                cover = None
            if cover is not None and (not cover.exists() or cover.stat().st_size < 256):
                cover = None
    if cover and cover.exists() and cover.stat().st_size > 256:
        # gambar statis: loop frame-nya selama durasi segmen
        return (["-loop", "1", "-framerate", str(fps), "-i", str(cover)] + audio_in, "1")

    # audio saja: kanvas gradien senada template (bukan hitam mati) supaya caption terbaca
    background = background or {}
    color1 = _hex(background.get("color1"), "#160F2E")[1:]
    color2 = _hex(background.get("color2"), "#7C5CFF")[1:]
    canvas = (
        f"gradients=s={width}x{height}:c0=0x{color1}:c1=0x{color2}:"
        f"x0=0:y0=0:x1={width}:y1={height}:speed=0.004:r={fps}"
    )
    return (["-f", "lavfi", "-i", canvas] + audio_in, "1")


def _render_segment(
    source_path: Path,
    segment: dict[str, Any],
    spec: dict[str, Any],
    out_path: Path,
    workdir: Path,
    info: media.MediaInfo | None = None,
    output_start: float = 0.0,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    fps = int(spec["output"]["fps"])
    layout = spec["layout"]
    speed = _clamp(_f(segment.get("speed"), 1.0), 0.5, 2.0)
    start = _f(segment.get("start"))
    duration = max(0.2, _f(segment.get("end")) - start)
    out_duration = max(0.1, duration / speed)

    framing_spec = spec.get("framing") if isinstance(spec.get("framing"), dict) else {}
    framing_segments: list[dict[str, Any]] = []
    if framing_spec.get("mode") == "auto":
        framing_segments = framing_module.shift_segments(
            framing_spec.get("segments") or [], start, duration
        )
    punch_intervals = _punch_in_intervals(spec, output_start, out_duration) if layout == "crop" else []
    chain = _layout_chain(
        layout, _f(spec.get("crop_focus"), 0.5), spec["background"], fps,
        framing=framing_segments,
        gameplay=spec.get("gameplay") if isinstance(spec.get("gameplay"), dict) else None,
        split_spec=spec.get("split") if isinstance(spec.get("split"), dict) else None,
        punch_intervals=punch_intervals,
    )
    if speed != 1.0:
        chain += f";[vbase]setpts=PTS/{speed:.4f}[vseg]"
        last = "vseg"
    else:
        last = "vbase"

    def build_and_run(force_still: bool) -> None:
        if cancel_check and cancel_check():
            raise OperationCancelled("Render dibatalkan")
        inputs, audio_idx = _visual_input(
            source_path, info, start, duration, workdir, fps, CANVAS_W, CANVAS_H,
            spec.get("background"), force_still=force_still,
        )
        cmd = [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-nostats",
            *inputs,
            "-filter_complex", chain, "-map", f"[{last}]", "-map", f"{audio_idx}:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
            "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps * 2),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-t", f"{out_duration:.3f}", "-progress", "pipe:1", str(out_path),
        ]
        if speed != 1.0:
            cmd.insert(-1, "-af")
            cmd.insert(-1, f"atempo={speed:.4f},aresample=48000")
        media.run_with_progress(cmd, out_duration, None, cwd=workdir, cancel_check=cancel_check)

    build_and_run(force_still=False)
    if cancel_check and cancel_check():
        raise OperationCancelled("Render dibatalkan")
    # Kalau hasilnya ternyata tanpa gambar (video sumber habis / hanya audio),
    # render ulang memakai frame statis / kanvas gradien supaya ada video-nya.
    has_video = bool(out_path.exists() and out_path.stat().st_size > 512 and media.probe(out_path).has_video)
    if cancel_check and cancel_check():
        raise OperationCancelled("Render dibatalkan")
    if not has_video:
        build_and_run(force_still=True)


def render_spec(
    clip: dict[str, Any],
    spec: dict[str, Any],
    source_path: str | Path,
    source_info: media.MediaInfo,
    on_progress: ProgressFn | None = None,
    settings: dict[str, Any] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    before_commit: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Render spec editor → file mp4 (bisa 1080p/2K/4K) tanpa watermark."""
    settings = settings or load_settings()
    source_path = Path(source_path)
    clip_id = clip["id"]
    workdir = WORK_DIR / clip["job_id"] / "render" / clip_id
    workdir.mkdir(parents=True, exist_ok=True)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CLIP_DIR / f"{clip_id}.mp4"
    thumb_path = CLIP_DIR / f"{clip_id}.jpg"
    # Render ke workdir dulu, baru dipindah: di Windows file hasil lama bisa
    # terkunci (sedang diputar di browser) sehingga ffmpeg gagal menimpanya.
    render_path = workdir / f"{clip_id}.mp4"

    def check_cancelled() -> None:
        if cancel_check and cancel_check():
            raise OperationCancelled("Render dibatalkan")

    def report(fraction: float, message: str) -> None:
        check_cancelled()
        if on_progress:
            on_progress(_clamp(fraction, 0.0, 1.0), message)

    segments = spec["segments"]
    total_out = spec_duration(spec)
    if total_out < 0.5:
        raise EditorError("Durasi clip terlalu pendek")
    output = spec["output"]
    width, height = int(output["width"]), int(output["height"])
    fps = int(output["fps"])
    is_upscale = height > CANVAS_H

    report(0.02, "Menyiapkan segmen…")

    # ---------- pass 1: potong & sesuaikan tiap segmen ----------
    piece_paths: list[Path] = []
    for idx, (segment, out_range) in enumerate(zip(segments, with_out_ranges(segments))):
        piece = workdir / f"seg_{idx:02d}.mp4"
        report(0.03 + 0.55 * (idx / max(1, len(segments))), f"Memproses segmen {idx + 1}/{len(segments)}")
        _render_segment(
            source_path, segment, spec, piece, workdir, source_info,
            output_start=float(out_range["out_start"]),
            cancel_check=cancel_check,
        )
        check_cancelled()
        if not piece.exists() or piece.stat().st_size < 512:
            raise EditorError(f"Segmen {idx + 1} gagal dirender")
        piece_info = media.probe(piece)
        check_cancelled()
        if not piece_info.has_video:
            raise EditorError(
                f"Segmen {idx + 1} keluar tanpa gambar (hanya suara). "
                "Sumbernya kemungkinan audio saja atau gambarnya statis."
            )
        piece_paths.append(piece)

    report(0.6, "Menggabungkan segmen…")
    if len(piece_paths) == 1:
        base_path = piece_paths[0]
    else:
        list_file = workdir / "concat.txt"
        list_file.write_text(
            # as_posix(): Windows butuh garis miring depan, backslash = escape di concat demuxer
            "\n".join("file '" + p.resolve().as_posix().replace("'", "'\\''") + "'" for p in piece_paths),
            encoding="utf-8",
        )
        base_path = workdir / "base.mp4"
        media.run_with_progress(
            [
                ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-nostats",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", str(base_path),
            ],
            total_out, None, cwd=workdir, cancel_check=cancel_check,
        )
        if not base_path.exists():
            raise EditorError("Gagal menggabungkan segmen")

    # ---------- pass 2: subtitle, teks, logo, upscale ----------
    report(0.65, "Menyiapkan caption & teks…")
    captions = spec["captions"]
    font = str(captions.get("font") or "Inter")
    ass_paths: list[str] = []
    if captions.get("enabled") and spec.get("subtitles"):
        subtitle_lines = spec["subtitles"]
        hook_item = next((t for t in (spec.get("texts") or [])
                          if isinstance(t, dict) and t.get("id") == "hook"), None)
        if hook_item and _flag(captions.get("mute_during_hook"), True):
            # Selama hook tampil di atas, baris caption yang isinya sama tidak
            # diulang di bawah (layar bersih, teks tidak dobel).
            cutoff = _f(hook_item.get("end"))
            subtitle_lines = [
                line for line in subtitle_lines
                if _f(line.get("start")) >= cutoff - 0.05 or line.get("hidden")
            ]
        if build_captions_ass(subtitle_lines, captions, workdir / "captions.ass"):
            ass_paths.append("captions.ass")
    if spec.get("texts") and build_overlays_ass(spec["texts"], workdir / "overlays.ass", font):
        ass_paths.append("overlays.ass")

    chains: list[str] = []
    filters: list[str] = []
    if height != CANVAS_H:
        filters.append(f"scale={width}:{height}:flags=lanczos")
        if output.get("sharpen", True):
            filters.append("unsharp=5:5:0.55:5:5:0.0")
        filters.append("format=yuv420p")
    fade_in = _f(output.get("fade_in"))
    fade_out = _f(output.get("fade_out"))
    if fade_in > 0:
        filters.append(f"fade=t=in:st=0:d={fade_in:.2f}")
    if fade_out > 0:
        start_fade = max(0.0, total_out - fade_out - 0.05)
        filters.append(f"fade=t=out:st={start_fade:.2f}:d={fade_out:.2f}")

    chains.append("[0:v]" + (",".join(filters) if filters else "null") + "[v0]")
    last = "v0"
    for i, ass_name in enumerate(ass_paths):
        label = f"v{len(chains)}"
        chains.append(f"[{last}]ass={ass_name}[{label}]")
        last = label

    logo = spec.get("logo") or {}
    logo_path = logo.get("path") or ""
    inputs: list[str] = ["-i", str(base_path)]
    if logo_path and Path(logo_path).exists():
        inputs += ["-i", str(logo_path)]
        logo_w = max(24, int(_clamp(_f(logo.get("width"), 0.22), 0.05, 1.0) * width))
        opacity = _clamp(_f(logo.get("opacity"), 1.0), 0.05, 1.0)
        x_expr = f"(W-w)*{_clamp(_f(logo.get('x'), 0.5), 0.0, 1.0):.3f}"
        y_expr = f"(H-h)*{_clamp(_f(logo.get('y'), 0.93), 0.0, 1.0):.3f}"
        enable = ""
        if _f(logo.get("end")) > 0:
            enable = f":enable='between(t,{_f(logo.get('start')):.2f},{_f(logo.get('end')):.2f})'"
        chains.append(
            f"[1:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={opacity:.2f}[lg]"
        )
        label = f"v{len(chains)}"
        chains.append(f"[{last}][lg]overlay={x_expr}:{y_expr}{enable}[{label}]")
        last = label

    chains.append(f"[{last}]format=yuv420p[vout]")
    filter_complex = ";".join(chains)

    audio_filters: list[str] = []
    if output.get("normalize", True):
        audio_filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    audio_filters.append("aresample=48000")

    level = "4.2"
    if height >= 3840:
        level = "5.2"
    elif height >= 2560:
        level = "5.1"
    preset = "veryfast" if height <= 1920 else "faster"

    cmd: list[str] = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-nostats"]
    cmd += inputs
    cmd += ["-filter_complex", filter_complex, "-map", "[vout]"]
    if source_info.has_audio:
        cmd += ["-map", "0:a?", "-af", ",".join(audio_filters),
                "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000"]
    cmd += [
        "-c:v", "libx264", "-preset", preset, "-crf", str(int(output["crf"])),
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", level,
        "-r", str(fps), "-g", str(fps * 2),
        "-movflags", "+faststart", "-t", f"{total_out:.3f}",
    ]
    if is_upscale:
        cmd += ["-maxrate", "60M", "-bufsize", "120M"]
    cmd += ["-progress", "pipe:1", str(render_path)]

    report(0.7, "Render akhir" + (" (upscale 4K)" if height >= 3840 else ""))

    def ffprog(fraction: float) -> None:
        report(0.7 + 0.26 * fraction, f"Render {fraction * 100:.0f}% · {width}×{height}")

    media.run_with_progress(cmd, total_out, ffprog, cwd=workdir, cancel_check=cancel_check)
    check_cancelled()
    if not render_path.exists() or render_path.stat().st_size < 2048:
        raise EditorError("Hasil render kosong — coba layout/pengaturan lain")
    # pastikan hasil akhir benar-benar punya gambar (bukan audio saja)
    result_info = media.probe(render_path)
    check_cancelled()
    if not result_info.has_video:
        raise EditorError(
            "Hasil render tidak punya track video (hanya suara). Coba Render ulang; "
            "kalau sumbernya audio saja, video akan dibuat dari background + caption."
        )
    if source_info.has_audio and not result_info.has_audio:
        raise EditorError(
            "Hasil render tidak punya track audio padahal sumbernya bersuara. Coba render ulang."
        )

    check_cancelled()
    if before_commit:
        before_commit()
    if not safe_replace(render_path, out_path):
        raise EditorError(
            "Hasil render tidak bisa disimpan karena file lama masih dipakai "
            "(misalnya sedang diputar di tab lain). Tutup preview lalu render ulang."
        )

    report(0.97, "Bikin thumbnail & subtitle…")
    try:
        media.thumb(out_path, min(1.5, total_out / 3), thumb_path, width=540)
        thumb_value = str(thumb_path)
    except Exception:
        thumb_value = ""

    srt_path = CLIP_DIR / f"{clip_id}.srt"
    try:
        srt_path.write_text(subtitles_to_srt(spec.get("subtitles") or []), encoding="utf-8")
        srt_value = str(srt_path) if srt_path.exists() else ""
    except OSError:
        srt_value = ""

    report(1.0, "Selesai")
    return {
        "file": str(out_path),
        "thumb": thumb_value,
        "srt": srt_value,
        "duration": total_out,
        "width": width,
        "height": height,
        "size": out_path.stat().st_size,
    }


def source_warning(info: media.MediaInfo) -> str:
    """Peringatan kalau sumber tidak punya gambar bergerak (klip tetap punya video).
    """
    if not info.has_video:
        return (
            "Sumber ini hanya berisi audio (tanpa track video). Klip tetap dirender sebagai "
            "video: background gradien + caption. Mau gambar bergerak? pakai video asli."
        )
    if info.attached_pic:
        return (
            "Sumber ini hanya berisi satu gambar (cover art) + audio. Klip dirender dengan "
            "gambar statis sebagai latar."
        )
    if info.width and info.height and max(info.width, info.height) < 480:
        return (
            f"Resolusi sumber kecil ({info.width}×{info.height}); hasil 1080p/4K hanya upscale, "
            "detail tidak bertambah."
        )
    return ""


def cleanup_workdir(job_id: str, clip_id: str | None = None) -> None:
    base = WORK_DIR / job_id / "render"
    target = base / clip_id if clip_id else base
    safe_rmtree(target)


# --------------------------------------------------------------------------- #
# util untuk UI
# --------------------------------------------------------------------------- #
def load_or_create_spec(
    clip: dict[str, Any],
    transcript: dict[str, Any] | None,
    source_duration: float,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ambil spec dari clip (kalau belum ada / rusak → bikin default dari transkrip)."""
    settings = settings or load_settings()
    raw = clip.get("spec") or ""
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("segments"):
                clip_meta = clip.get("meta") if isinstance(clip.get("meta"), dict) else {}
                framing_meta = clip_meta.get("framing") or {}
                meta_v = int(framing_meta.get("detector_v") or 0)
                spec_v = int(data.get("framing_v") or 0)
                if meta_v > spec_v and settings.get("auto_framing", True):
                    # Spec dibuat oleh detektor versi lama → sinkronkan hasil deteksi terbaru
                    base = default_spec(clip, settings, transcript, source_duration)
                    data["framing_v"] = meta_v
                    data["framing"] = base["framing"]
                    data["gameplay"] = base["gameplay"]
                    if not data.get("template"):
                        data["layout"] = base["layout"]
                        if base["layout"] == "gameplay":
                            data.setdefault("captions", {})["y"] = base["captions"]["y"]
                result = normalize_spec(data, clip, settings, source_duration)
                is_ai_clip = clip_meta.get("origin") == "ai" and not clip_meta.get("manual", False)
                if is_ai_clip and not clip_meta.get("edited"):
                    # Pulihkan title/hook dan caption otomatis pada clip AI lama yang
                    # pernah tersimpan tanpa keduanya, tetapi jangan timpa edit manual.
                    base = default_spec(clip, settings, transcript, source_duration)
                    if settings.get("captions", True) and clip_meta.get("captions") is not False:
                        result["captions"]["enabled"] = True
                        if not result["subtitles"] and base["subtitles"]:
                            result["subtitles"] = base["subtitles"]
                    show_hook = bool(settings.get("ai_hook_overlay", True))
                    if show_hook and base["texts"] and not has_hook_overlay(result):
                        result["texts"] = [*result["texts"], *base["texts"]]
                    result = normalize_spec(result, clip, settings, source_duration)
                return result
        except (json.JSONDecodeError, TypeError):
            pass
    return default_spec(clip, settings, transcript, source_duration)


def strip_hook_overlays(spec: dict[str, Any], hook_text: str = "") -> tuple[dict[str, Any], int]:
    """Buang teks judul/hook dari dalam video (untuk spec yang dibuat versi lama).

    Kembalikan (spec_baru, jumlah_teks_yang_dibuang).
    """
    texts = spec.get("texts")
    if not isinstance(texts, list):
        return spec, 0
    target = (hook_text or "").strip().lower()
    kept: list[dict[str, Any]] = []
    removed = 0
    for item in texts:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if str(item.get("id") or "") == "hook" or (target and text.lower() == target):
            removed += 1
            continue
        kept.append(item)
    if removed:
        spec = {**spec, "texts": kept}
    return spec, removed


def has_hook_overlay(spec: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict) and str(item.get("id") or "") == "hook"
        for item in (spec.get("texts") or [])
    )


def summarize(spec: dict[str, Any]) -> dict[str, Any]:
    duration = spec_duration(spec)
    return {
        "duration": duration,
        "segments": len(spec.get("segments") or []),
        "texts": len(spec.get("texts") or []),
        "subtitles": len([s for s in (spec.get("subtitles") or []) if not s.get("hidden")]),
        "resolution": f"{spec['output']['width']}×{spec['output']['height']}",
        "template": spec.get("template") or "",
    }


def new_text_id() -> str:
    return f"t{int(time.time() * 1000) % 10_000_000}"
