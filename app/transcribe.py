"""Transkripsi lokal pakai faster-whisper (gratis, jalan offline setelah model ke-cache)."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .config import MODEL_DIR, WORK_DIR, load_settings

_model_cache: dict[tuple[str, str, str], Any] = {}
_model_lock = threading.Lock()


class TranscriptionUnavailable(RuntimeError):
    pass


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word]


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def _pick_device(requested: str) -> tuple[str, str]:
    settings = load_settings()
    compute = (settings.get("whisper_compute") or "auto").lower()
    device = requested if requested != "auto" else settings.get("whisper_device", "auto")
    if device in ("auto", ""):
        try:
            import ctranslate2  # type: ignore

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if compute in ("auto", ""):
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def _get_model(model_name: str, device: str, compute: str):
    key = (model_name, device, compute)
    with _model_lock:
        if key not in _model_cache:
            from faster_whisper import WhisperModel  # type: ignore

            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            _model_cache[key] = WhisperModel(
                model_name,
                device=device,
                compute_type=compute,
                download_root=str(MODEL_DIR),
                cpu_threads=max(2, (os.cpu_count() or 4) // 2),
            )
        return _model_cache[key]


def transcribe(
    media_path: str | Path,
    on_progress: Callable[[float, str], None] | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Kembalikan {'language', 'duration', 'segments': [...]} dengan word timestamps."""
    if not available():
        raise TranscriptionUnavailable(
            "faster-whisper belum terinstall. Jalankan: pip install faster-whisper"
        )
    settings = load_settings()
    model_name = settings.get("whisper_model") or "small"
    device, compute = _pick_device(settings.get("whisper_device") or "auto")
    lang = language or settings.get("language") or "auto"
    if on_progress:
        on_progress(0.02, f"Menyiapkan model Whisper '{model_name}' ({device}/{compute})…")

    model = _get_model(model_name, device, compute)

    segments_iter, info = model.transcribe(
        str(media_path),
        language=None if lang in ("auto", "", None) else lang,
        task="transcribe",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    total = float(getattr(info, "duration", 0) or 0)
    detected = getattr(info, "language", None) or "unknown"
    segments: list[Segment] = []
    for seg in segments_iter:
        words = [
            Word(start=float(w.start or 0), end=float(w.end or 0), text=(w.word or "").strip(),
                 prob=float(getattr(w, "probability", 1.0) or 1.0))
            for w in (seg.words or [])
            if (w.word or "").strip()
        ]
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append(
            Segment(start=float(seg.start), end=float(seg.end), text=text, words=words)
        )
        if on_progress and total > 0:
            on_progress(min(0.98, 0.05 + 0.9 * (float(seg.end) / total)),
                        f"Transkripsi {float(seg.end) / total * 100:.0f}% · {len(segments)} baris")

    if on_progress:
        on_progress(1.0, f"Transkripsi selesai ({detected})")

    return {
        "language": detected,
        "duration": total,
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text,
             "words": [asdict(w) for w in s.words]}
            for s in segments
        ],
    }


def save_transcript(job_id: str, data: dict[str, Any]) -> Path:
    path = WORK_DIR / job_id / "transcript.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_transcript(job_id: str) -> dict[str, Any] | None:
    path = WORK_DIR / job_id / "transcript.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def transcript_text(data: dict[str, Any], start: float | None = None,
                    end: float | None = None) -> str:
    parts: list[str] = []
    for seg in data.get("segments") or []:
        if start is not None and seg["end"] <= start:
            continue
        if end is not None and seg["start"] >= end:
            continue
        parts.append(seg["text"])
    return " ".join(parts).strip()
