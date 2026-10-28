"""Cari momen paling potensial dari transkrip (scoring lokal, tanpa API berbayar).

Skor gabungan:
  - hook/kata pemicu (Indonesia + Inggris), angka, pertanyaan
  - energi audio (bagian ramai lebih menarik daripada bagian datar)
  - kepadatan bicara & jeda
  - panjang ideal + pembuka yang kuat
Opsional: re-ranking judul pakai LLM lokal (Ollama). Kalau Ollama tidak nyala,
semua tetap jalan dengan heuristik saja.
"""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .config import load_settings

HOOK_WORDS = {
    # indonesia
    "rahasia", "trik", "tips", "cara", "kesalahan", "salah", "jangan", "harus", "wajib",
    "ternyata", "penting", "gratis", "cuan", "untung", "rugi", "bahaya", "awas", "gila",
    "anak", "cepat", "mudah", "susah", "baru", "terbukti", "fakta", "mitos", "bongkar",
    "bocoran", "tutorial", "langkah", "modal", "hasil", "sukses", "gagal", "takut",
    "pertama", "terakhir", "selamanya", "nggak", "tidak", "banyak", "orang", "kamu",
    "kalian", "aku", "gue", "gua", "saya", "sebenarnya", "padahal", "justru", "makanya",
    # english
    "secret", "tips", "trick", "how", "why", "never", "always", "must", "should",
    "mistake", "mistakes", "stop", "worst", "best", "free", "money", "danger",
    "warning", "truth", "myth", "fact", "proven", "nobody", "everyone", "you",
    "your", "actually", "because", "biggest", "fastest", "easiest", "hardest",
    "hack", "hacks", "learn", "beginner", "growth", "viral",
}

QUESTION_WORDS = {
    "apa", "kenapa", "mengapa", "gimana", "bagaimana", "kok", "siapa", "kapan", "berapa",
    "why", "how", "what", "who", "when", "which", "can", "do", "does",
}

STOPWORDS = {
    "yang", "dan", "di", "ke", "dari", "itu", "ini", "untuk", "dengan", "pada", "adalah",
    "saya", "aku", "kamu", "kita", "kami", "mereka", "dia", "akan", "sudah", "belum",
    "bisa", "tidak", "nggak", "gak", "juga", "saja", "aja", "ada", "karena", "kalau",
    "jadi", "tapi", "atau", "dalam", "banyak", "lebih", "masih", "harus", "lagi", "banget",
    "the", "and", "for", "that", "this", "with", "you", "are", "was", "have", "has",
    "but", "not", "they", "them", "from", "will", "can", "just", "like", "about",
    "what", "when", "how", "why", "your", "our", "its", "it's", "i'm", "don't",
    "bawah", "atas", "sini", "sana", "berikutnya", "sekarang", "nanti", "pernah",
    "video", "konten", "orang", "semua", "sama", "sekali", "cuma", "hanya", "biar",
    "kayak", "banget", "gitu", "gini", "emang", "memang", "punya", "bikin", "buat",
    "this", "that", "with", "have", "here", "there", "then", "than", "very",
    "terlalu", "upload", "salah", "jam", "detik", "menit", "kali", "pertama", "kedua",
    "ketiga", "benerinnya", "gimana", "nonton", "tulis", "mau", "coba", "naik",
}

FILLERS = {"eee", "ee", "eh", "emm", "hmm", "uh", "um", "uhh", "anu", "apa ya", "gitu"}


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def token_count(self) -> int:
        return len(re.findall(r"\w+", self.text))


@dataclass
class Candidate:
    start: float
    end: float
    score: float
    text: str
    title: str
    hook: str
    description: str
    hashtags: str
    features: dict[str, float] = field(default_factory=dict)
    sentences: list[Sentence] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# --------------------------------------------------------------------------- #
# pembentukan kalimat
# --------------------------------------------------------------------------- #
def build_sentences(transcript: dict[str, Any]) -> list[Sentence]:
    words: list[dict[str, Any]] = []
    for seg in transcript.get("segments") or []:
        seg_words = seg.get("words") or []
        if seg_words:
            words.extend(seg_words)
        else:  # fallback: bagi teks segmen jadi kata dengan durasi rata
            tokens = (seg.get("text") or "").split()
            if not tokens:
                continue
            step = (seg["end"] - seg["start"]) / max(1, len(tokens))
            for i, token in enumerate(tokens):
                words.append({
                    "start": seg["start"] + i * step,
                    "end": seg["start"] + (i + 1) * step,
                    "text": token,
                })

    sentences: list[Sentence] = []
    current: list[dict[str, Any]] = []
    for i, word in enumerate(words):
        current.append(word)
        text = " ".join(w["text"] for w in current).strip()
        next_word = words[i + 1] if i + 1 < len(words) else None
        gap = (next_word["start"] - word["end"]) if next_word else 0
        end_of_sentence = bool(re.search(r"[.!?…]$", word["text"]))
        too_long = (word["end"] - current[0]["start"]) > 13 or len(current) >= 28
        if end_of_sentence or gap > 0.9 or too_long or next_word is None:
            if text:
                sentences.append(
                    Sentence(start=current[0]["start"], end=word["end"], text=text, words=list(current))
                )
            current = []
    return [s for s in sentences if s.text.strip()]


# --------------------------------------------------------------------------- #
# fitur
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-ZÀ-ÿ']+", text.lower())


def _energy_stats(energy: list[tuple[float, float]], start: float, end: float) -> tuple[float, float]:
    values = [db for t, db in energy if start <= t <= end and db > -90]
    if not values:
        return -60.0, 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return mean, math.sqrt(var)


def _global_loudness(energy: list[tuple[float, float]]) -> float:
    values = [db for _, db in energy if db > -90]
    if not values:
        return -30.0
    return sum(values) / len(values)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _hook_score(text: str) -> float:
    tokens = _tokens(text)
    if not tokens:
        return 0.0
    hooks = sum(1 for t in tokens if t in HOOK_WORDS)
    questions = sum(1 for t in tokens if t in QUESTION_WORDS)
    numbers = len(re.findall(r"\b\d+([.,]\d+)?\b", text))
    per_100 = (hooks + questions * 1.2 + numbers * 0.8) / len(tokens) * 100
    return _clamp(per_100 / 9.0)


def _clean_title(text: str, max_len: int = 70) -> str:
    text = re.sub(r"\s+", " ", text).strip(" .-–—")
    text = re.sub(r"^[#*\-\s]+", "", text)
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.strip() + "…"


def _make_title(sentences: list[Sentence]) -> str:
    first = sentences[0].text if sentences else ""
    tokens = first.split()
    # buang filler di awal ("eee", "jadi", "oke guys")
    while tokens and tokens[0].lower().strip(",.") in FILLERS | {"jadi", "oke", "okay", "nah", "so", "and"}:
        tokens.pop(0)
    text = " ".join(tokens)
    title = _clean_title(text, 70)
    if len(title) < 8 and len(sentences) > 1:
        title = _clean_title(" ".join(s.text for s in sentences[:2]), 70)
    return title


def _make_hook(title: str) -> str:
    tokens = title.split()
    words: list[str] = []
    for token in tokens:
        words.append(token)
        if len(words) >= 6:
            break
    hook = " ".join(words).strip(" ,.")
    return hook.rstrip(".") if hook else title[:48]


def _keywords(text: str, limit: int = 4) -> list[str]:
    """Kata kunci bermakna: panjang, bukan stopword, diutamakan yang muncul di judul."""
    text = re.sub(r"#\w+", " ", text or "")
    tokens = [t for t in _tokens(text) if len(t) >= 5 and t not in STOPWORDS and t not in HOOK_WORDS]
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return []
    first_sentence = set(_tokens(text.split(".")[0]))
    ranked = sorted(counts.items(), key=lambda kv: (-(kv[1] + (1.5 if kv[0] in first_sentence else 0)), kv[0]))
    seen: set[str] = set()
    result: list[str] = []
    for word, _ in ranked:
        if word in seen:
            continue
        seen.add(word)
        result.append(word)
        if len(result) >= limit:
            break
    return result


def _hashtags(keywords: Iterable[str], base: str) -> str:
    tags: list[str] = [t for t in re.split(r"\s+", base or "") if t.startswith("#")]
    for word in keywords:
        tag = "#" + re.sub(r"[^\w]", "", word.title())
        if len(tag) > 2 and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    if not any(t.lower() == "#shorts" for t in tags):
        tags.insert(0, "#Shorts")
    return " ".join(tags[:10])


# --------------------------------------------------------------------------- #
# pemilihan kandidat
# --------------------------------------------------------------------------- #
def _window_score(cand: Candidate, energy: list[tuple[float, float]], base_db: float,
                  min_len: float, max_len: float) -> tuple[float, dict[str, float]]:
    text = cand.text
    tokens = _tokens(text)
    dur = max(1.0, cand.duration)
    density = _clamp((len(tokens) / dur) / 3.0)            # ~3 kata/detik = ideal
    hook = _hook_score(text)
    opening_hook = _hook_score(" ".join(s.text for s in cand.sentences[:2]))
    mean_db, std_db = _energy_stats(energy, cand.start, cand.end)
    loud = _clamp((mean_db - (base_db - 12)) / 14.0) if base_db > -90 else 0.5
    lively = _clamp(std_db / 6.0)
    ideal = (min_len + 1.4 * min_len) / 2.4
    ideal = max(ideal, min_len)
    length_fit = _clamp(1.0 - abs(dur - ideal) / max(ideal, 1.0))
    gaps = [cand.sentences[i + 1].start - cand.sentences[i].end
            for i in range(len(cand.sentences) - 1)]
    silence = sum(g for g in gaps if g > 0.7)
    continuity = _clamp(1.0 - silence / max(3.0, dur * 0.2))
    filled = 0.0 if text.lower().strip(" .,!?").endswith(tuple(FILLERS)) else 1.0
    features = {
        "hook": round(hook, 3),
        "opening": round(opening_hook, 3),
        "energy": round(loud, 3),
        "lively": round(lively, 3),
        "density": round(density, 3),
        "length": round(length_fit, 3),
        "continuity": round(continuity, 3),
        "clean_ending": filled,
    }
    raw = (
        30 * hook + 16 * opening_hook + 16 * loud + 6 * lively +
        12 * density + 12 * length_fit + 6 * continuity + 2 * filled
    )
    return round(raw, 2), features


def find_candidates(
    transcript: dict[str, Any],
    energy: list[tuple[float, float]],
    settings: dict[str, Any] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
) -> list[Candidate]:
    settings = settings or load_settings()
    min_len = float(settings.get("min_clip_seconds") or 20)
    max_len = float(settings.get("max_clip_seconds") or 60)
    target = int(settings.get("target_clips") or 5)
    if max_len < min_len:
        max_len = min_len + 10
    target = max(1, min(20, target))

    sentences = build_sentences(transcript)
    if not sentences:
        return []
    base_db = _global_loudness(energy)

    raw: list[Candidate] = []
    total_starts = len(sentences)
    for i, start_sentence in enumerate(sentences):
        if on_progress and i % 25 == 0:
            on_progress(0.9 * i / total_starts, f"Menilai {i}/{total_starts} kandidat…")
        chunk: list[Sentence] = []
        for j in range(i, len(sentences)):
            chunk.append(sentences[j])
            dur = sentences[j].end - start_sentence.start
            if dur > max_len:
                break
            if dur < min_len:
                continue
            text = " ".join(s.text for s in chunk)
            if len(_tokens(text)) < 12:
                continue
            cand = Candidate(
                start=start_sentence.start,
                end=sentences[j].end,
                score=0.0,
                text=text,
                title="",
                hook="",
                description="",
                hashtags="",
                sentences=list(chunk),
            )
            score, features = _window_score(cand, energy, base_db, min_len, max_len)
            cand.score = score
            cand.features = features
            raw.append(cand)

    if not raw:  # audio terlalu pendek / transkrip tipis
        return []

    raw.sort(key=lambda c: -c.score)
    picked: list[Candidate] = []
    for cand in raw:
        if len(picked) >= target * 3:
            break
        clash = False
        for other in picked:
            overlap = min(cand.end, other.end) - max(cand.start, other.start)
            if overlap > 0.25 * min(cand.duration, other.duration):
                clash = True
                break
            if abs(cand.start - other.start) < 4:
                clash = True
                break
        if not clash:
            picked.append(cand)
    picked = picked[:target]

    # rapikan tepi clip: buang hening di awal/akhir
    for cand in picked:
        cand.start = _snap_start(cand.start, cand.sentences, energy)
        cand.end = _snap_end(cand.end, cand.sentences, energy)

    picked.sort(key=lambda c: c.start)
    for idx, cand in enumerate(picked):
        cand.title = _make_title(cand.sentences)
        cand.hook = _make_hook(cand.title)
        keywords = _keywords(cand.title + " " + cand.text)
        cand.hashtags = _hashtags(keywords, settings.get("yt_hashtags") or "#Shorts")
        cand.description = _build_description(cand, keywords)
        cand.features["order"] = idx
    if on_progress:
        on_progress(1.0, f"{len(picked)} kandidat clip ditemukan")
    return picked


def _snap_start(start: float, sentences: list[Sentence], energy: list[tuple[float, float]]) -> float:
    if not sentences:
        return max(0.0, start)
    first = sentences[0]
    new_start = first.start
    for t, db in energy:
        if first.start - 0.8 <= t <= first.start + 1.2 and db > -35:
            new_start = min(new_start, t)
            break
    return max(0.0, new_start - 0.12)


def _snap_end(end: float, sentences: list[Sentence], energy: list[tuple[float, float]]) -> float:
    if not sentences:
        return end
    return max(sentences[0].start + 3, end + 0.25)


def _build_description(cand: Candidate, keywords: list[str]) -> str:
    lines = [cand.title]
    if keywords:
        lines.append("")
        lines.append("Poin utama: " + ", ".join(keywords[:4]))
    lines.append("")
    lines.append(cand.hashtags)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# fallback tanpa transkrip (Whisper belum ada / bahasa tidak terdeteksi)
# --------------------------------------------------------------------------- #
def fixed_windows(duration: float, settings: dict[str, Any] | None = None) -> list[Candidate]:
    settings = settings or load_settings()
    min_len = float(settings.get("min_clip_seconds") or 20)
    max_len = float(settings.get("max_clip_seconds") or 60)
    target = max(1, min(20, int(settings.get("target_clips") or 5)))
    if duration <= 0:
        return []
    length = max(min_len, min(max_len, 45.0))
    results: list[Candidate] = []
    step = max(length * 0.9, 5.0)
    start = 0.0
    index = 0
    while start + min_len <= duration + 0.5 and len(results) < target:
        end = min(duration, start + length)
        if end - start < min(8.0, min_len):
            break
        index += 1
        cand = Candidate(
            start=round(start, 2), end=round(end, 2), score=float(max(1, 100 - index * 6)),
            text="", title=f"Clip {index} · {_fmt(start)}–{_fmt(end)}",
            hook="", description="", hashtags=settings.get("yt_hashtags") or "#Shorts",
        )
        cand.features = {"auto_window": 1.0}
        results.append(cand)
        start += step
    return results


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# --------------------------------------------------------------------------- #
# LLM lokal (Ollama) — opsional
# --------------------------------------------------------------------------- #
def ollama_available(settings: dict[str, Any] | None = None) -> bool:
    settings = settings or load_settings()
    url = (settings.get("llm_url") or "").rstrip("/")
    if not url:
        return False
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=2.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def refine_with_llm(
    candidates: list[Candidate],
    settings: dict[str, Any] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
) -> None:
    """Minta LLM lokal bikin judul/hook/hashtag lebih menarik. Gagal = diam saja."""
    settings = settings or load_settings()
    if not settings.get("llm_enabled") or not candidates:
        return
    url = (settings.get("llm_url") or "").rstrip("/")
    model = settings.get("llm_model") or "llama3.1:8b"
    if not url:
        return
    payload_items = [
        {"i": i, "transkrip": c.text[:1200]}
        for i, c in enumerate(candidates)
    ]
    prompt = (
        "Kamu editor konten short-form. Untuk tiap transkrip, buat judul clickbait jujur "
        "(maks 60 karakter, bahasa sama dengan transkrip), hook 4-6 kata, dan 3 hashtag. "
        "Balas HANYA JSON array: "
        '[{"i":0,"judul":"...","hook":"...","hashtags":["#a","#b"]}]\n\n'
        + json.dumps(payload_items, ensure_ascii=False)
    )
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.6},
    }).encode("utf-8")
    if on_progress:
        on_progress(0.5, "Minta judul ke LLM lokal…")
    req = urllib.request.Request(f"{url}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        raw = data.get("response") or ""
        parsed = _parse_json_loose(raw)
        items = parsed if isinstance(parsed, list) else parsed.get("clips") or parsed.get("data") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            idx = item.get("i", item.get("index"))
            if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
                continue
            cand = candidates[idx]
            title = str(item.get("judul") or item.get("title") or "").strip()
            hook = str(item.get("hook") or "").strip()
            tags = item.get("hashtags") or item.get("tags")
            if title:
                cand.title = _clean_title(title, 70)
                cand.description = cand.description.replace(cand.description.splitlines()[0], cand.title)
            if hook:
                cand.hook = _clean_title(hook, 48)
            if isinstance(tags, list) and tags:
                cand.hashtags = " ".join(
                    t if str(t).startswith("#") else "#" + str(t) for t in tags if str(t).strip()
                )[:200]
            cand.features["llm"] = 1.0
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return
    finally:
        if on_progress:
            on_progress(1.0, "Selesai")


def _parse_json_loose(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                continue
    return {}
