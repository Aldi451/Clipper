"""Generate subtitle ASS (gaya karaoke OpusClip) + overlay judul hook."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .config import load_settings

CANVAS_W = 1080
CANVAS_H = 1920


# --------------------------------------------------------------------------- #
# util warna & escaping
# --------------------------------------------------------------------------- #
def ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS pakai BGR)."""
    value = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        value = "FFFFFF"
    try:
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
    except ValueError:
        r = g = b = 255
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _escape(text: str) -> str:
    text = text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
    text = text.replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:d}:{minutes:02d}:{secs:05.2f}"


# --------------------------------------------------------------------------- #
# caption karaoke
# --------------------------------------------------------------------------- #
def _word_events(words: list[dict[str, Any]], clip_start: float, clip_end: float,
                 words_per_group: int) -> Iterable[tuple[float, float, list[dict[str, Any]], int]]:
    """Pecah kata jadi grup; hasilkan (mulai, selesai, grup, indeks aktif) relatif ke clip."""
    inside: list[dict[str, Any]] = []
    for word in words:
        start = float(word.get("start") or 0)
        end = float(word.get("end") or start)
        if end <= clip_start or start >= clip_end:
            continue
        text = (word.get("text") or "").strip()
        if not text:
            continue
        inside.append({"start": max(0.0, start - clip_start), "end": max(0.0, end - clip_start),
                       "text": text})

    if not inside:
        return []
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in inside:
        current.append(word)
        ends_sentence = bool(re.search(r"[.!?…,;:]$", word["text"]))
        if len(current) >= max(1, words_per_group) or ends_sentence:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    events: list[tuple[float, float, list[dict[str, Any]], int]] = []
    for group in groups:
        for idx, word in enumerate(group):
            start = word["start"]
            end = group[idx + 1]["start"] if idx + 1 < len(group) else max(word["end"], start + 0.12)
            if end - start < 0.08:
                end = start + 0.08
            events.append((start, end, group, idx))
    return events


def build_caption_ass(
    words: list[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    out_path: str | Path,
    settings: dict[str, Any] | None = None,
) -> bool:
    settings = settings or load_settings()
    per_group = max(1, int(settings.get("caption_words") or 3))
    font = settings.get("caption_font") or "Inter"
    primary = ass_color(settings.get("caption_color") or "#FFFFFF")
    highlight = ass_color(settings.get("caption_highlight") or "#FFE02B")
    uppercase = bool(settings.get("caption_uppercase"))
    events = _word_events(words, clip_start, clip_end, per_group)
    if not events:
        return False

    font_size = 96 if per_group <= 2 else 86 if per_group <= 3 else 76
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {CANVAS_W}
PlayResY: {CANVAS_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{font_size},{primary},&H000000FF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,9,4,2,80,80,430,1
Style: Hook,{font},78,&H00FFFFFF,&H000000FF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,10,5,8,90,90,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    for start, end, group, active in events:
        parts: list[str] = []
        for idx, word in enumerate(group):
            text = _escape(word["text"])
            if uppercase:
                text = text.upper()
            if idx == active:
                parts.append("{\\c" + highlight + "\\fscx104\\fscy104}" + text + "{\\r}")
            else:
                parts.append(text)
        body = " ".join(parts)
        body = "{\\fad(40,0)}" + body
        lines.append(
            f"Dialogue: 0,{ts(start)},{ts(end)},Cap,,0,0,0,,{body}"
        )

    Path(out_path).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return True


# --------------------------------------------------------------------------- #
# overlay judul hook di atas
# --------------------------------------------------------------------------- #
def build_hook_ass(hook: str, out_path: str | Path, duration: float,
                   settings: dict[str, Any] | None = None) -> bool:
    settings = settings or load_settings()
    hook = _escape(hook or "")
    if not hook:
        return False
    if bool(settings.get("caption_uppercase")):
        hook = hook.upper()
    font = settings.get("caption_font") or "Inter"
    end = max(1.0, min(duration, 5.5))
    text = (
        "{\\fad(150,250)\\fscx90\\fscy90\\t(0,180,\\fscx100\\fscy100)}"
        + f"{hook}"
    )
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {CANVAS_W}
PlayResY: {CANVAS_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{font},80,&H00FFFFFF,&H000000FF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,10,6,8,90,90,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,{ts(0)},{ts(end)},Hook,,0,0,0,,{text}
"""
    Path(out_path).write_text(header, encoding="utf-8")
    return True


def collect_words(transcript: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not transcript:
        return []
    words: list[dict[str, Any]] = []
    for seg in transcript.get("segments") or []:
        words.extend(seg.get("words") or [])
    return words
