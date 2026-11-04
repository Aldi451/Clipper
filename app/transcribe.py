"""Transkripsi lokal pakai faster-whisper (gratis, jalan offline setelah model ke-cache)."""
from __future__ import annotations

import inspect as _inspect

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


WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")

# ukuran kira-kira (MB) untuk ditampilkan di UI
MODEL_SIZES = {"tiny": 75, "base": 145, "small": 480, "medium": 1500, "large-v3": 3100}

_download_lock = threading.Lock()
_download_state: dict[str, Any] = {"model": "", "running": False, "message": "", "done": False,
                                   "error": ""}


def current_model() -> str:
    return load_settings().get("whisper_model") or "small"


def model_downloaded(model: str) -> bool:
    """Model sudah ada di folder models/ (ditandai file model.bin atau folder snapshot)."""
    if not model:
        return False
    try:
        for path in MODEL_DIR.glob("**/model.bin"):
            folder = str(path.parent).lower()
            if model.lower() in folder:
                return True
    except OSError:
        return False
    return False


def models_status() -> dict[str, Any]:
    with _download_lock:
        state = dict(_download_state)
    current = current_model()
    return {
        "available": available(),          # library faster-whisper terpasang
        "current": current,
        "current_ready": model_downloaded(current),
        "models_dir": str(MODEL_DIR),
        "download": state,
        "models": [
            {
                "name": name,
                "size_mb": MODEL_SIZES.get(name, 0),
                "downloaded": model_downloaded(name),
            }
            for name in WHISPER_MODELS
        ],
    }


def start_download(model: str | None = None) -> dict[str, Any]:
    """Unduh model Whisper ke folder models/ (sekali, butuh internet)."""
    model = (model or current_model() or "small").strip()
    if model not in WHISPER_MODELS:
        raise TranscriptionUnavailable(f"Model '{model}' tidak dikenal")
    if not available():
        raise TranscriptionUnavailable(
            "Library faster-whisper belum terpasang. Jalankan: pip install -r requirements.txt"
        )
    with _download_lock:
        if _download_state["running"]:
            return models_status()
        _download_state.update({"model": model, "running": True, "done": False,
                                "message": f"Mengunduh model {model}…", "error": ""})

    def worker() -> None:
        try:
            import inspect

            from faster_whisper.utils import download_model  # type: ignore

            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            # nama argumen berbeda antar versi faster-whisper
            params = inspect.signature(download_model).parameters
            if "cache_dir" in params:
                download_model(model, cache_dir=str(MODEL_DIR))
            elif "download_root" in params:
                download_model(model, download_root=str(MODEL_DIR))
            elif "output_dir" in params:
                download_model(model, output_dir=str(MODEL_DIR / model))
            else:  # versi sangat lama: biarkan default HF cache
                download_model(model)
            with _download_lock:
                _download_state.update({
                    "running": False,
                    "done": True,
                    "message": f"Model {model} siap dipakai offline",
                })
        except Exception as exc:  # noqa: BLE001 - tampilkan pesan yang bisa dibaca
            detail = str(exc).splitlines()[0][:200] if str(exc) else exc.__class__.__name__
            with _download_lock:
                _download_state.update({
                    "running": False,
                    "done": False,
                    "message": "Gagal mengunduh model",
                    "error": f"{detail}. Periksa koneksi internet lalu coba lagi.",
                })

    threading.Thread(target=worker, daemon=True).start()
    return models_status()


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

    # --- parameter akurasi ---
    # initial_prompt menjaga gaya bahasa (Indonesia baku + tanda baca) sehingga
    # kata pendek seperti "saya"/"ada" tidak gampang tertukar oleh model kecil.
    prompt = str(settings.get("whisper_initial_prompt") or "").strip()
    if not prompt:
        prompt = (
            "Ini transkrip percakapan bahasa Indonesia yang jelas, dengan tanda baca. "
            "Contoh: Saya bukan orang biasa, saya sudah mencoba semuanya."
            if lang in ("auto", "", None, "id")
            else ""
        )
    model_name_l = str(model_name).lower()
    # model kecil rawan salah dengar; suhu cadangan membuat Whisper mengulang
    # bagian yang ragu alih-alih menebak satu kata (mis. "saya" jadi "ada").
    kwargs = {
        "language": None if lang in ("auto", "", None) else lang,
        "task": "transcribe",
        "beam_size": 5,
        "best_of": 5,
        "patience": 1.0,
        "temperature": [0.0, 0.2, 0.4],
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 300, "speech_pad_ms": 200},
        "word_timestamps": True,
        "condition_on_previous_text": False,
        "initial_prompt": prompt or None,
        "prefix": None,
        "hallucination_silence_threshold": 1.5 if model_name_l in ("tiny", "base", "small") else None,
    }
    # Versi faster-whisper lama tidak punya semua parameter di atas; buang yang
    # tidak dikenal supaya transkripsi tidak gagal total di komputer user.
    try:
        allowed = set(_inspect.signature(model.transcribe).parameters)
        kwargs = {key: value for key, value in kwargs.items() if key in allowed}
    except (TypeError, ValueError):
        pass
    segments_iter, info = model.transcribe(str(media_path), **kwargs)
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

    all_words = [w for seg in segments for w in seg.words]
    probs = [float(w.prob) for w in all_words if w.prob is not None]
    low = sum(1 for value in probs if value < 0.5)
    quality = {
        "model": str(model_name),
        "words": len(all_words),
        "avg_prob": round(sum(probs) / len(probs), 3) if probs else 0.0,
        "low_confidence": low,
        "low_ratio": round(low / len(probs), 3) if probs else 0.0,
    }

    return {
        "language": detected,
        "duration": total,
        "quality": quality,
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
