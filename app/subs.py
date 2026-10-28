"""Parser subtitle (.vtt/.srt) — dipakai sebagai fallback transkrip kalau Whisper belum ada.

Banyak video YouTube sudah punya subtitle (manual/otomatis). Kita pakai itu supaya
caption + pemilihan momen tetap jalan tanpa model Whisper.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_TS = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)
_TAG = re.compile(r"<[^>]+>")
_LANG = re.compile(r"\.([a-z]{2}(?:-[A-Za-z]+)?)\.(vtt|srt)$", re.I)


def _to_seconds(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_subtitles(text: str) -> list[dict[str, Any]]:
    """Kembalikan list cue {start, end, text} dari isi VTT/SRT."""
    cues: list[dict[str, Any]] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        match = _TS.search(line)
        if not match:
            i += 1
            continue
        g = match.groups()
        start = _to_seconds(g[0], g[1], g[2], g[3])
        end = _to_seconds(g[4], g[5], g[6], g[7])
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip() and not _TS.search(lines[i]):
            body.append(lines[i].strip())
            i += 1
        content = " ".join(_TAG.sub("", chunk) for chunk in body).strip()
        content = re.sub(r"\s+", " ", content)
        if content and end > start:
            cues.append({"start": round(start, 3), "end": round(end, 3), "text": content})
    return _dedupe(cues)


def _dedupe(cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """VTT auto dari YouTube sering punya cue bertumpuk/kata berulang."""
    result: list[dict[str, Any]] = []
    last_text = ""
    for cue in cues:
        text = cue["text"].strip()
        if text == last_text or not text:
            if result and cue["end"] > result[-1]["end"]:
                result[-1]["end"] = cue["end"]
            continue
        if result and text.startswith(result[-1]["text"]) and cue["start"] <= result[-1]["end"] + 0.15:
            result[-1]["text"] = text
            result[-1]["end"] = cue["end"]
        else:
            result.append(dict(cue))
        last_text = text
    return result


def cues_to_transcript(cues: list[dict[str, Any]], language: str = "") -> dict[str, Any]:
    """Ubah cue jadi struktur transkrip + word timing perkiraan (proporsional panjang kata)."""
    segments: list[dict[str, Any]] = []
    for cue in cues:
        tokens = cue["text"].split()
        if not tokens:
            continue
        span = max(0.2, cue["end"] - cue["start"])
        weights = [max(2, len(token)) for token in tokens]
        total_weight = sum(weights) or 1
        words: list[dict[str, Any]] = []
        cursor = cue["start"]
        for token, weight in zip(tokens, weights):
            step = span * weight / total_weight
            words.append({
                "start": round(cursor, 3),
                "end": round(cursor + step, 3),
                "text": token,
                "prob": 0.6,
            })
            cursor += step
        segments.append({
            "start": cue["start"], "end": cue["end"],
            "text": cue["text"], "words": words,
        })
    duration = segments[-1]["end"] if segments else 0.0
    return {"language": language, "duration": duration, "segments": segments, "source": "subtitle"}


def pick_subtitle_file(workdir: str | Path) -> tuple[Path, str] | None:
    """Cari file subtitle hasil yt-dlp, prioritas bahasa Indonesia lalu Inggris."""
    workdir = Path(workdir)
    candidates = sorted(workdir.glob("*.vtt")) + sorted(workdir.glob("*.srt"))
    if not candidates:
        return None
    def score(path: Path) -> int:
        match = _LANG.search(path.name)
        lang = (match.group(1) if match else "").lower()
        order = {"id": 0, "id-id": 0, "en": 1, "en-us": 1, "en-gb": 1}
        return order.get(lang, 5)
    best = min(candidates, key=score)
    match = _LANG.search(best.name)
    language = (match.group(1) if match else "").split("-")[0]
    return best, language


def load_subtitle_transcript(path: str | Path, language: str = "") -> dict[str, Any] | None:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cues = parse_subtitles(text)
    if len(cues) < 3:
        return None
    return cues_to_transcript(cues, language)


def find_sidecar_subtitle(media_path: str | Path) -> tuple[Path, str] | None:
    """Cari .srt/.vtt di sebelah file video (mis. hasil ekspor editor)."""
    media_path = Path(media_path)
    for ext in (".srt", ".vtt"):
        sibling = media_path.with_suffix(ext)
        if sibling.exists():
            return sibling, ""
    return None
