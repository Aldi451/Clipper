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

import bisect
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

# Frasa pemicu (2+ kata) — bobotnya lebih besar daripada kata tunggal, karena
# inilah yang membuat orang berhenti scroll: "jangan pernah", "kebanyakan orang",
# "yang gak banyak orang tahu", dst.
HOOK_PHRASES: dict[str, float] = {
    # keingintahuan / membongkar
    "rahasia yang": 1.15, "yang gak banyak orang": 1.2, "yang tidak banyak orang": 1.2,
    "kebanyakan orang": 1.1, "banyak orang gak": 1.0, "sebenarnya yang": 0.9,
    "ternyata yang": 0.95, "ini yang bikin": 1.1, "ini yang bikin orang": 1.15,
    "kuncinya adalah": 0.95, "yang bikin beda": 1.0, "bongkar rahasia": 1.2,
    "nobody tells you": 1.2, "what nobody": 1.2, "here is why": 1.0, "the reason": 0.9,
    # peringatan / kesalahan
    "jangan pernah": 1.25, "jangan sampai": 1.15, "jangan lakukan": 1.15,
    "kesalahan paling": 1.15, "kesalahan yang": 1.05, "stop lakukan": 1.15,
    "biggest mistake": 1.2, "never do": 1.15,
    # bukti / angka
    "hasilnya adalah": 0.9, "terbukti bahwa": 0.9, "datanya bilang": 1.0,
    "riset menunjukkan": 0.95, "faktanya adalah": 1.0,
    # nilai / uang
    "tanpa modal": 1.1, "gratis tanpa": 1.0, "cuan gede": 1.0, "untung besar": 1.0,
    # penegasan
    "paling penting": 0.95, "yang paling": 0.85, "satu-satunya": 1.0,
    "pertama kali": 0.85, "akhirnya": 0.8,
}

# Pembuka yang melemahkan hook ("jadi gini guys…", "oke lanjut…")
WEAK_OPENERS = {
    "jadi", "oke", "okay", "ok", "nah", "so", "and", "terus", "lalu", "kemudian",
    "sekarang", "gini", "begini", "eee", "ee", "eh", "emm", "hmm", "uh", "um",
    "ya", "iya", "betul", "benar", "halo", "hai", "guys", "teman", "semuanya",
}


def _norm_phrase(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


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
INCOMPLETE_ENDINGS = {
    "dan", "karena", "tapi", "atau", "kalau", "jika", "yang", "untuk", "dengan",
    "sehingga", "namun", "maka", "ketika", "sebelum", "sesudah", "supaya", "agar",
    "bahwa", "di", "ke", "dari", "and", "because", "but", "or", "if", "which",
    "that", "with", "to", "for", "when", "while", "although", "so",
}


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
    hook_info: dict[str, Any] = field(default_factory=dict)

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


def _ending_quality(text: str) -> float:
    """Penalti ringan untuk filler atau klausa yang jelas terpotong di akhir klip."""
    clean = (text or "").strip()
    tokens = _tokens(clean)
    if not tokens:
        return 0.0
    ending = clean.lower().rstrip(" .,!?'\"…")
    if any(ending == filler or ending.endswith(" " + filler) for filler in FILLERS):
        return 0.0
    if tokens[-1] in INCOMPLETE_ENDINGS:
        return 0.0
    if re.search(r"[.!?…][\"'”’)]*$", clean):
        return 1.0
    return 0.55


_energy_cache: tuple[int, int, float, float, list[float], list[float], list[float]] | None = None


def _build_energy_index(
    energy: list[tuple[float, float]],
) -> tuple[list[float], list[float], list[float]]:
    global _energy_cache
    first_t = float(energy[0][0]) if energy else 0.0
    last_t = float(energy[-1][0]) if energy else 0.0
    cache = _energy_cache
    if (
        cache is not None
        and cache[0] == id(energy)
        and cache[1] == len(energy)
        and cache[2] == first_t
        and cache[3] == last_t
    ):
        return cache[4], cache[5], cache[6]

    times: list[float] = []
    psum: list[float] = [0.0]
    psq: list[float] = [0.0]
    acc = 0.0
    acc_sq = 0.0
    for t, db in sorted((t, db) for t, db in energy if db > -90):
        times.append(t)
        acc += db
        acc_sq += db * db
        psum.append(acc)
        psq.append(acc_sq)
    _energy_cache = (id(energy), len(energy), first_t, last_t, times, psum, psq)
    return times, psum, psq


def _energy_stats(energy: list[tuple[float, float]], start: float, end: float) -> tuple[float, float]:
    if not energy:
        return -60.0, 0.0
    times, psum, psq = _build_energy_index(energy)
    lo = bisect.bisect_left(times, start)
    hi = bisect.bisect_right(times, end)
    count = hi - lo
    if count <= 0:
        return -60.0, 0.0
    mean = (psum[hi] - psum[lo]) / count
    var = max(0.0, (psq[hi] - psq[lo]) / count - mean * mean)
    return mean, math.sqrt(var)


def _global_loudness(energy: list[tuple[float, float]]) -> float:
    values = [db for _, db in energy if db > -90]
    if not values:
        return -30.0
    return sum(values) / len(values)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


HOOK_GRADES = ((60.0, "Kuat"), (40.0, "Sedang"), (0.0, "Lemah"))


def _grade(score: float) -> str:
    for threshold, name in HOOK_GRADES:
        if score >= threshold:
            return name
    return "Lemah"


def hook_assessment(text: str) -> dict[str, Any]:
    """Nilai kekuatan hook sebuah kalimat (0–100) + alasan yang bisa dibaca user.

    Berbeda dari versi lama yang hanya menghitung kata pemicu: di sini ada enam
    sinyal yang masing-masing punya bobot, plus penalti untuk pembuka lemah dan
    kalimat yang bertele-tele. Tujuannya supaya dua clip yang berbeda benar-benar
    mendapat nilai berbeda — bukan semuanya "sedang".
    """
    raw = re.sub(r"\s+", " ", (text or "")).strip()
    tokens = _tokens(raw)
    if not tokens:
        return {"score": 0.0, "grade": "Lemah", "signals": {}, "reasons": [],
                "words": 0, "phrases": []}

    padded = " " + _norm_phrase(raw) + " "
    found_phrases = [(phrase, weight) for phrase, weight in HOOK_PHRASES.items()
                     if f" {phrase} " in padded]
    phrase_weight = sum(weight for _, weight in found_phrases)

    lowered = [t.lower() for t in tokens]
    hook_hits = sum(1 for t in lowered if t in HOOK_WORDS)
    questions = sum(1 for t in lowered if t in QUESTION_WORDS)
    numbers = len(re.findall(r"\b\d+([.,]\d+)?\b", raw))
    superlatives = sum(1 for t in lowered if t in
                       ("paling", "terbesar", "tercepat", "termurah", "nomor", "biggest",
                        "fastest", "best", "worst", "terbaik", "terburuk"))
    address = sum(1 for t in lowered if t in ("kamu", "kalian", "lo", "lu", "anda", "you", "your"))

    word_count = len(tokens)
    first = lowered[0]
    weak_open = first in WEAK_OPENERS
    stop_ratio = sum(1 for t in lowered if t in STOPWORDS) / word_count
    filler_hits = sum(1 for t in lowered if t in FILLERS)

    # --- sinyal 0..1 ---
    trigger = _clamp((phrase_weight * 0.9 + hook_hits * 0.32) / 2.4)
    curiosity = 1.0 if questions else 0.0
    concrete = _clamp(numbers / 2.0 + (0.45 if numbers else 0.0))
    punch = _clamp((superlatives * 0.5 + address * 0.35))
    # panjang ideal: 4–14 kata. Terlalu pendek tidak jelas, terlalu panjang bertele-tele.
    if word_count < 3:
        length_fit = 0.25
    elif word_count <= 14:
        length_fit = 1.0
    else:
        length_fit = _clamp(1.0 - (word_count - 14) / 18.0)
    clarity = _clamp(1.0 - max(0.0, stop_ratio - 0.42) / 0.35 - filler_hits * 0.12)
    opener = 0.15 if weak_open else 1.0

    score = 100.0 * (
        0.34 * trigger + 0.14 * curiosity + 0.12 * concrete +
        0.10 * punch + 0.14 * length_fit + 0.10 * clarity + 0.06 * opener
    )
    if weak_open:
        score *= 0.86
    score = round(_clamp(score / 100.0) * 100.0, 1)

    # --- alasan yang ditampilkan ke user ---
    reasons: list[str] = []
    if found_phrases:
        reasons.append("frasa pemicu: " + ", ".join(f"“{p}”" for p, _ in found_phrases[:2]))
    elif hook_hits >= 2:
        reasons.append(f"{hook_hits} kata pemicu")
    if questions:
        reasons.append("berbentuk pertanyaan")
    if numbers:
        reasons.append(f"ada angka konkret ({numbers})")
    if superlatives:
        reasons.append("pakai kata superlatif")
    if address:
        reasons.append("menyapa penonton langsung")
    if weak_open:
        reasons.append("dibuka kata lemah (“" + first + "”)")
    if not reasons:
        reasons.append("tidak ada pemicu kuat")

    return {
        "score": score,
        "grade": _grade(score),
        "words": word_count,
        "phrases": [phrase for phrase, _ in found_phrases[:4]],
        "signals": {
            "trigger": round(trigger, 3),
            "curiosity": round(curiosity, 3),
            "concrete": round(concrete, 3),
            "punch": round(punch, 3),
            "length": round(length_fit, 3),
            "clarity": round(clarity, 3),
            "opener": round(opener, 3),
        },
        "reasons": reasons[:4],
    }


def _hook_score(text: str) -> float:
    """Skor hook 0..1 (kompatibel dengan pemanggil lama)."""
    return _clamp(hook_assessment(text)["score"] / 100.0)


# --------------------------------------------------------------------------- #
# pembuat hook: ambil klausa terbaik, bukan 6 kata pertama judul
# --------------------------------------------------------------------------- #
_CLAUSE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\s+(?:tapi|namun|tetapi|karena|soalnya|padahal)\s+", re.I)


def _trim_words(text: str, limit: int = 12) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip(" ,.")
    return " ".join(words[:limit]).strip(" ,.") + "…"


def _lower_first(text: str) -> str:
    """Huruf pertama jadi kecil, kecuali singkatan/angka (“90 persen”, “AI …”)."""
    if not text:
        return text
    if len(text) > 1 and text[0].isupper() and text[1].isupper():
        return text
    return text[0].lower() + text[1:]


def hook_variants(text: str, limit: int = 3) -> list[dict[str, Any]]:
    """Beberapa varian hook dari satu klip, diurutkan dari yang paling kuat.

    Gaya: klausa asli (dipotong), versi pendek, versi peringatan, versi penasaran,
    dan versi pertanyaan — hanya dibuat kalau kalimatnya memang cocok, supaya
    tidak muncul kalimat janggal seperti “Kenapa Ternyata …”.
    """
    clauses = [c.strip(" ,.") for c in _CLAUSE_SPLIT.split(text or "") if c.strip(" ,.")]
    if not clauses:
        return []
    clauses = [c for c in clauses if 3 <= len(c.split()) <= 30] or clauses[:1]
    best = max(clauses, key=lambda c: hook_assessment(c)["score"])
    core = _trim_words(best, 11)
    core_low = _lower_first(core)
    core_score = hook_assessment(best)["score"]
    tokens = _tokens(best)
    has_question = any(t in QUESTION_WORDS for t in tokens)
    head = next((w for w in tokens if w in HOOK_WORDS), "")
    numbers = len(re.findall(r"\b\d+([.,]\d+)?\b", best))

    seeds: list[tuple[str, str]] = [("asli", core)]
    short = _trim_words(best, 7)
    if short and len(short.split()) < len(core.split()):
        seeds.append(("singkat", short))
    NO_QUESTION = {"jangan", "awas", "stop", "berhenti", "ternyata", "sebenarnya",
                   "padahal", "justru", "salah", "kesalahan", "kebanyakan"}
    if not has_question and not core.rstrip().endswith("?"):
        if head in ("jangan", "awas", "stop", "salah", "kesalahan"):
            seeds.append(("peringatan", f"Berhenti dulu: {core_low}"))
        elif head in ("rahasia", "ternyata", "sebenarnya", "bocoran", "fakta"):
            seeds.append(("penasaran", f"{core} — dan ini alasannya"))
        elif numbers:
            seeds.append(("angka", f"Angka ini penting: {core_low}"))
        else:
            seeds.append(("penasaran", f"Ini yang bikin beda: {core_low}"))
        # "Kenapa ternyata…?" / "Kenapa jangan pernah…?" terdengar janggal, jadi
        # bentuk pertanyaan hanya dibuat untuk kalimat yang memang netral.
        if len(core.split()) <= 9 and head not in NO_QUESTION:
            seeds.append(("pertanyaan", f"Kenapa {core_low}?"))

    seen: set[str] = set()
    variants: list[dict[str, Any]] = []
    for style, candidate in seeds:
        candidate = re.sub(r"\s+", " ", candidate).strip(" ,.")
        key = _norm_phrase(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        assessment = hook_assessment(candidate)
        # jangan biarkan awalan bagus "menyelamatkan" kalimat inti yang lemah
        blended = round(0.65 * assessment["score"] + 0.35 * core_score, 1)
        variants.append({
            "text": candidate[:90],
            "style": style,
            "score": blended,
            "grade": _grade(blended),
            "reasons": assessment["reasons"],
        })
    variants.sort(key=lambda item: -item["score"])
    return variants[:limit]


def _make_hook(title: str) -> str:
    """Hook terbaik dari judul/kalimat klip (fallback: 8 kata pertama)."""
    variants = hook_variants(title, limit=1)
    if variants:
        return variants[0]["text"][:60]
    words = title.split()[:8]
    hook = " ".join(words).strip(" ,.")
    return hook.rstrip(".") if hook else title[:48]


def _jaccard(a: list[str], b: list[str]) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _clean_title(text: str, max_len: int = 70) -> str:
    text = re.sub(r"\s+", " ", text).strip(" .-–—")
    text = re.sub(r"^[#*\-\s]+", "", text)
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    if max_len <= 1:
        return "…"[:max_len]
    return cut.strip()[:max_len - 1].rstrip() + "…"


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


def _clean_hashtags(values: Any, limit: int = 5) -> str:
    """Normalize model tags and cap them so malformed output cannot flood metadata."""
    if not isinstance(values, list):
        return ""
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = re.sub(r"[^\w]", "", str(value or "").strip().lstrip("#"))
        if not name:
            continue
        tag = "#" + name[:40]
        if tag.lower() not in seen:
            tags.append(tag)
            seen.add(tag.lower())
        if len(tags) >= max(1, limit):
            break
    return " ".join(tags)


# --------------------------------------------------------------------------- #
# pemilihan kandidat
# --------------------------------------------------------------------------- #
def target_clip_count(settings: dict[str, Any] | None, source_duration: float) -> int:
    """Target akhir yang dipakai AI dari durasi sumber (termasuk aturan video panjang)."""
    settings = settings or load_settings()
    try:
        target = int(settings.get("target_clips") or 5)
    except (TypeError, ValueError):
        target = 5
    target = max(1, min(20, target))
    try:
        duration = max(0.0, float(source_duration))
    except (TypeError, ValueError):
        duration = 0.0
    # Pertahankan perilaku lama untuk sumber panjang dengan target default 5.
    if duration >= 1500.0 and target == 5:
        target = min(12, max(5, int(duration // 360)))
    return target


def candidate_pool_target(settings: dict[str, Any] | None, target: int) -> int:
    """AI mendapat pool alternatif 2x untuk membandingkan momen sebelum dipangkas."""
    settings = settings or load_settings()
    local_llm_configured = bool(
        settings.get("llm_enabled") and str(settings.get("llm_url") or "").strip()
    )
    if gemini_available(settings) or local_llm_configured:
        return min(20, max(1, target) * 2)
    return max(1, target)


def select_candidate_subset(candidates: list[Candidate], target: int) -> list[Candidate]:
    """Pilih pool dengan skor terbaik sambil menjaga variasi pembuka dan waktu.

    Dapat dipanggil lagi sesudah model mengubah skor, sehingga AI ikut menentukan
    momen akhir, bukan hanya menulis judul untuk pilihan lokal.
    """
    if not candidates or target <= 0:
        return []
    pool = list(candidates)
    openings = {
        id(cand): _tokens(" ".join(x.text for x in cand.sentences[:2]) or cand.text[:300])
        for cand in pool
    }
    picked: list[Candidate] = []
    picked_openings: list[list[str]] = []

    while pool and len(picked) < target:
        best_index, best_value, best_similarity = 0, float("-inf"), 0.0
        for index, cand in enumerate(pool):
            opening = openings[id(cand)]
            similarity = max((_jaccard(opening, other) for other in picked_openings), default=0.0)
            value = float(cand.score) * (1.0 - 0.45 * similarity)
            if value > best_value:
                best_index, best_value, best_similarity = index, value, similarity

        cand = pool.pop(best_index)
        clash = any(
            min(cand.end, other.end) - max(cand.start, other.start)
            > 0.25 * min(cand.duration, other.duration)
            or abs(cand.start - other.start) < 4
            for other in picked
        )
        if clash:
            continue

        cand.features.setdefault("original_score", round(float(cand.score), 2))
        cand.features["selection_score"] = round(best_value, 2)
        cand.features["score"] = round(best_value, 2)
        cand.features["opening_novelty"] = round(1.0 - best_similarity, 3)
        picked.append(cand)
        picked_openings.append(openings[id(cand)])

    return sorted(picked, key=lambda cand: cand.start)


def _window_score(cand: Candidate, energy: list[tuple[float, float]], base_db: float,
                  min_len: float, max_len: float,
                  opening_hook: float | None = None,
                  token_count: int | None = None) -> tuple[float, dict[str, float]]:
    text = cand.text
    n_tokens = token_count if token_count is not None else len(_tokens(text))
    dur = max(1.0, cand.duration)
    density = _clamp((n_tokens / dur) / 3.0)               # ~3 kata/detik = ideal
    hook = _hook_score(text)
    if opening_hook is None:
        opening_hook = _hook_score(" ".join(s.text for s in cand.sentences[:2]))
    mean_db, std_db = _energy_stats(energy, cand.start, cand.end)
    loud = _clamp((mean_db - (base_db - 12)) / 14.0) if base_db > -90 else 0.5
    lively = _clamp(std_db / 6.0)
    ideal = (1.4 * min_len + max(min_len, max_len)) / 2.4
    ideal = max(min_len, min(max(min_len, max_len), ideal))
    span = max(15.0, max_len - min_len, ideal * 0.6)
    length_fit = _clamp(1.0 - abs(dur - ideal) / span)
    gaps = [cand.sentences[i + 1].start - cand.sentences[i].end
            for i in range(len(cand.sentences) - 1)]
    silence = sum(g for g in gaps if g > 0.7)
    continuity = _clamp(1.0 - silence / max(3.0, dur * 0.2))
    clean_ending = _ending_quality(text)
    features = {
        "hook": round(hook, 3),
        "opening": round(opening_hook, 3),
        "energy": round(loud, 3),
        "lively": round(lively, 3),
        "density": round(density, 3),
        "length": round(length_fit, 3),
        "continuity": round(continuity, 3),
        "clean_ending": clean_ending,
    }
    raw = (
        30 * hook + 16 * opening_hook + 12 * loud + 6 * lively +
        12 * density + 12 * length_fit + 6 * continuity + 6 * clean_ending
    )
    return round(raw, 2), features


def find_candidates(
    transcript: dict[str, Any],
    energy: list[tuple[float, float]],
    settings: dict[str, Any] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
) -> list[Candidate]:
    settings = settings or load_settings()
    min_len = float(settings.get("min_clip_seconds") or 60)
    max_len = float(settings.get("max_clip_seconds") or 180)
    # 1 menit .. 3 menit untuk video panjang; adaptif bila video sumber lebih pendek dari 60d
    min_len = max(15.0, min(180.0, min_len))
    max_len = max(min_len, min(180.0, max_len))

    sentences = build_sentences(transcript)
    if not sentences:
        return []
    total_span = max(0.0, sentences[-1].end - sentences[0].start)
    target = target_clip_count(settings, total_span)
    pool_target = candidate_pool_target(settings, target)
    if total_span >= 60.0 and float(settings.get("min_clip_seconds") or 60) >= 60.0:
        min_len = max(60.0, min_len)
        max_len = max(min_len, max_len)
    elif 0.0 < total_span < min_len:
        min_len = max(5.0, total_span * 0.5)
        max_len = max(min_len + 1.0, total_span + 1.0)

    base_db = _global_loudness(energy)
    sent_tokens = [len(_tokens(s.text)) for s in sentences]

    raw: list[Candidate] = []
    total_starts = len(sentences)
    for i, start_sentence in enumerate(sentences):
        if on_progress and i % 25 == 0:
            on_progress(0.9 * i / total_starts, f"Menilai {i}/{total_starts} kandidat…")
        chunk: list[Sentence] = []
        chunk_tokens = 0
        opening_hook: float | None = None
        for j in range(i, len(sentences)):
            chunk.append(sentences[j])
            chunk_tokens += sent_tokens[j]
            if len(chunk) == 2:
                opening_hook = _hook_score(" ".join(s.text for s in chunk[:2]))
            dur = sentences[j].end - start_sentence.start
            if dur > max_len:
                break
            if dur < min_len:
                continue
            if chunk_tokens < 12:
                continue
            text = " ".join(s.text for s in chunk)
            if opening_hook is None:
                opening_hook = _hook_score(" ".join(s.text for s in chunk[:2]))
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
            score, features = _window_score(
                cand, energy, base_db, min_len, max_len,
                opening_hook=opening_hook, token_count=chunk_tokens,
            )
            cand.score = score
            cand.features = features
            raw.append(cand)

    if not raw:  # audio terlalu pendek / transkrip tipis
        return []

    raw.sort(key=lambda c: -c.score)
    # Jika AI aktif, simpan kandidat alternatif yang beragam untuk dibandingkan
    # secara semantik; proses akhir tetap dibatasi ke jumlah target di pipeline.
    picked = select_candidate_subset(raw, pool_target)

    # rapikan tepi clip: buang hening di awal/akhir
    for cand in picked:
        cand.start = _snap_start(cand.start, cand.sentences, energy)
        cand.end = _snap_end(cand.end, cand.sentences, energy)

    picked.sort(key=lambda c: c.start)
    used_hooks: list[list[str]] = []
    for idx, cand in enumerate(picked):
        cand.title = _make_title(cand.sentences)
        keywords = _keywords(cand.title + " " + cand.text)
        cand.hook, cand.hook_info = _pick_hook(cand, used_hooks)
        cand.features["hook_score"] = float(cand.hook_info.get("score") or 0.0)
        cand.hashtags = _hashtags(keywords, settings.get("yt_hashtags") or "#Shorts")
        cand.description = _build_description(cand, keywords)
        cand.features["order"] = idx
    if on_progress:
        on_progress(1.0, f"{len(picked)} kandidat clip ditemukan")
    return picked


def choose_hook(text: str, used: list[list[str]] | None = None) -> tuple[str, dict[str, Any]]:
    """Pilih hook terbaik dari sebuah teks + detailnya (dipakai pipeline & API).

    Kalau varian terbaik terlalu mirip hook clip lain (pembawa acara sering
    mengulang frasa yang sama), ambil varian berikutnya yang lebih segar.
    """
    used = used if used is not None else []
    variants = hook_variants(text, limit=4)
    if not variants or variants[0]["score"] < 40.0:
        broader = hook_variants(text[:1200], limit=4)
        if broader and (not variants or broader[0]["score"] > variants[0]["score"]):
            variants = broader

    # Pilih dengan penalti kemiripan: varian terkuat menang, tapi kalau hook-nya
    # sudah dipakai clip lain, varian yang lebih segar didahulukan. Ini yang bikin
    # tiap clip di satu project tidak terasa "semua sama".
    chosen = variants[0] if variants else None
    best_value = float("-inf")
    for variant in variants:
        similarity = max((_jaccard(_tokens(variant["text"]), other) for other in used), default=0.0)
        value = float(variant["score"]) - 32.0 * similarity
        if value > best_value:
            best_value, chosen = value, variant

    used.append(_tokens(chosen["text"]) if chosen else _tokens(text))
    info = {
        "score": float(chosen["score"]) if chosen else 0.0,
        "grade": chosen["grade"] if chosen else "Lemah",
        "style": chosen["style"] if chosen else "asli",
        "reasons": chosen["reasons"] if chosen else [],
        "variants": variants,
    }
    fallback = _make_hook(text[:120])
    return (chosen["text"] if chosen else fallback)[:60], info


def _pick_hook(cand: Candidate, used: list[list[str]]) -> tuple[str, dict[str, Any]]:
    opening_text = " ".join(x.text for x in cand.sentences[:3]) or cand.title
    return choose_hook(opening_text or cand.text, used)


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
    last = sentences[-1]
    new_end = max(end, last.end)
    for t, db in energy:
        if last.end - 0.2 <= t <= last.end + 0.45 and db > -35:
            new_end = max(new_end, t)
    return max(sentences[0].start + 3, new_end + 0.25)


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
    min_len = float(settings.get("min_clip_seconds") or 60)
    max_len = float(settings.get("max_clip_seconds") or 180)
    target = max(1, min(20, int(settings.get("target_clips") or 5)))
    if duration <= 0:
        return []
    # tanpa transkrip: pakai panjang ideal di antara min–max, tidak pernah di bawah min
    length = max(min_len, min(max_len, (min_len + max_len) / 2))
    length = min(length, max(min_len, duration))
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
# Google Gemini API (Google AI Studio — ala OpenShorts / edwins.ai.id)
# & LLM lokal (Ollama) — opsional, fallback 100% ke lokal bila tidak diisi
# --------------------------------------------------------------------------- #
def gemini_available(settings: dict[str, Any] | None = None) -> bool:
    import os
    settings = settings or load_settings()
    if settings.get("gemini_enabled") is False:
        return False
    key = str(settings.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()
    return len(key) >= 16


def refine_with_gemini(
    candidates: list[Candidate],
    settings: dict[str, Any] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
) -> bool:
    """Gunakan Google Gemini API (gratis via Google AI Studio) untuk menyempurnakan
    judul, hook pembuka, skor viral (0-100), dan hashtag tiap kandidat klip.

    Mengembalikan ``True`` bila berhasil, ``False`` bila tidak aktif/gagal
    (sehingga otomatis lanjut ke Ollama / heuristik lokal tanpa error).
    """
    import os
    settings = settings or load_settings()
    if not candidates or not gemini_available(settings):
        return False
    api_key = str(settings.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()
    model = str(settings.get("gemini_model") or "gemini-2.5-flash").strip() or "gemini-2.5-flash"

    payload_items = [
        {
            "i": i,
            "mulai_detik": round(c.start, 1),
            "durasi_detik": round(c.duration, 1),
            "skor_lokal": round(c.score, 1),
            "sinyal_lokal": {
                key: round(float(c.features[key]), 2)
                for key in ("hook", "opening", "continuity", "clean_ending")
                if isinstance(c.features.get(key), (int, float))
            },
            "transkrip": c.text[:1400],
        }
        for i, c in enumerate(candidates)
    ]
    prompt = (
        "Kamu editor senior video pendek untuk YouTube Shorts, Reels, dan TikTok. "
        "Bandingkan semua kandidat sebagai satu daftar dan beri skor RELATIF: pilih momen "
        "yang paling layak ditonton sampai selesai, bukan yang sekadar memakai kata sensasional.\n"
        "Nilai apakah tiap klip bisa dipahami tanpa menonton bagian sebelumnya, punya satu gagasan "
        "jelas dan payoff/informasi nyata, serta dimulai dengan kalimat menarik. Turunkan skor untuk "
        "pengulangan, konteks yang hilang, pembuka basa-basi, dan akhir yang menggantung/terpotong. "
        "Kamu hanya melihat transkrip; jangan menganggap ada visual atau konteks lain.\n"
        "Gunakan bahasa yang sama dengan transkrip. Jangan menambah fakta, angka, janji, atau kutipan "
        "yang tidak ada. Hook 4–7 kata harus didukung ucapan dalam klip. Buat judul jujur maksimal "
        "60 karakter; hindari clickbait yang menjanjikan lebih dari isi.\n"
        "Untuk SETIAP kandidat, keluarkan `judul`, `hook`, `viral_score` 0–100 (nilai dengan wajar "
        "secara relatif, jangan memberi semua kandidat skor tinggi), `alasan` singkat yang merujuk "
        "isi, dan 3–5 `hashtags` relevan. Pertahankan indeks `i` persis seperti input.\n"
        "Balas HANYA JSON array valid, satu objek per kandidat, format:\n"
        '[{"i":0,"judul":"...","hook":"...","viral_score":72,"alasan":"...","hashtags":["#Shorts"]}]\n\n'
        "Data kandidat berikut berisi transkrip dan sinyal lokal (transkrip satu-satunya sumber fakta):\n"
        + json.dumps(payload_items, ensure_ascii=False)
    )
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.25,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
        },
    }).encode("utf-8")

    if on_progress:
        on_progress(0.35, f"Menyempurnakan hook & skor viral via Gemini ({model})…")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        candidates_resp = data.get("candidates") or []
        if not candidates_resp:
            return False
        parts = ((candidates_resp[0].get("content") or {}).get("parts")) or []
        raw = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
        parsed = _parse_json_loose(raw)
        items = parsed if isinstance(parsed, list) else (
            parsed.get("clips") or parsed.get("data") or [] if isinstance(parsed, dict) else []
        )
        updated = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            idx = item.get("i", item.get("index"))
            if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(candidates)):
                continue
            cand = candidates[idx]
            title = str(item.get("judul") or item.get("title") or "").strip()
            hook = str(item.get("hook") or "").strip()
            reason = str(item.get("alasan") or item.get("reason") or "").strip()
            vscore = item.get("viral_score", item.get("score"))
            tags = item.get("hashtags") or item.get("tags")
            if title:
                old_first = cand.description.splitlines()[0] if cand.description else ""
                cand.title = _clean_title(title, 60)
                if old_first:
                    cand.description = cand.description.replace(old_first, cand.title, 1)
                else:
                    cand.description = cand.title
            if hook:
                cand.hook = _clean_title(hook, 52)
            if isinstance(vscore, (int, float)) and not isinstance(vscore, bool) and 0 <= float(vscore) <= 100:
                # Blend Gemini score (65%) + local audio/hook heuristic score (35%)
                cand.score = round(0.65 * float(vscore) + 0.35 * float(cand.score), 1)
                cand.features["score"] = cand.score
            if reason:
                cand.hook_info = dict(cand.hook_info or {})
                reasons = list(cand.hook_info.get("reasons") or [])
                cand.hook_info["reasons"] = [reason[:120]] + [r for r in reasons if r != reason][:2]
                cand.hook_info["gemini_reason"] = reason[:140]
            cleaned_tags = _clean_hashtags(tags)
            if cleaned_tags:
                cand.hashtags = cleaned_tags
            cand.features["gemini"] = 1.0
            updated += 1
        return updated > 0
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, KeyError):
        return False
    finally:
        if on_progress:
            on_progress(1.0, "Kurasi AI selesai")


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
    """Sempurnakan judul/hook/skor viral memakai Gemini API (jika API key diisi)
    atau LLM lokal Ollama. Jika keduanya tidak aktif, gunakan hasil heuristik lokal."""
    settings = settings or load_settings()
    if not candidates:
        return
    if gemini_available(settings):
        if refine_with_gemini(candidates, settings, on_progress=on_progress):
            return
    if not settings.get("llm_enabled"):
        return
    url = (settings.get("llm_url") or "").rstrip("/")
    model = settings.get("llm_model") or "llama3.1:8b"
    if not url or not ollama_available(settings):
        return
    payload_items = [
        {
            "i": i,
            "mulai_detik": round(c.start, 1),
            "durasi_detik": round(c.duration, 1),
            "skor_lokal": round(c.score, 1),
            "transkrip": c.text[:1400],
        }
        for i, c in enumerate(candidates)
    ]
    prompt = (
        "Kamu editor senior video pendek. Bandingkan semua kandidat dan pilih momen yang "
        "paling menarik sebagai klip mandiri: konteks cukup, satu gagasan jelas, payoff nyata, "
        "pembuka kuat, serta akhir yang selesai. Turunkan nilai untuk pengulangan, basa-basi, "
        "konteks hilang, atau kalimat terpotong. Nilai hanya dari transkrip; jangan menambah "
        "fakta, angka, janji, atau kutipan. Gunakan bahasa yang sama dengan transkrip.\n"
        "Untuk setiap kandidat buat judul jujur maksimal 60 karakter, hook 4–7 kata yang didukung "
        "ucapan, skor relatif 0–100 (jangan semua tinggi), alasan singkat yang spesifik, dan 3–5 "
        "hashtag relevan. Pertahankan indeks `i`. Balas HANYA JSON array valid:\n"
        '[{"i":0,"judul":"...","hook":"...","viral_score":72,"alasan":"...",'
        '"hashtags":["#a","#b"]}]\n\n'
        "Data kandidat (transkrip satu-satunya sumber fakta):\n"
        + json.dumps(payload_items, ensure_ascii=False)
    )
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.25},
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
        items = parsed if isinstance(parsed, list) else (
            parsed.get("clips") or parsed.get("data") or [] if isinstance(parsed, dict) else []
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            idx = item.get("i", item.get("index"))
            if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(candidates)):
                continue
            cand = candidates[idx]
            title = str(item.get("judul") or item.get("title") or "").strip()
            hook = str(item.get("hook") or "").strip()
            reason = str(item.get("alasan") or item.get("reason") or "").strip()
            vscore = item.get("viral_score", item.get("score"))
            tags = item.get("hashtags") or item.get("tags")
            if title:
                old_first = cand.description.splitlines()[0] if cand.description else ""
                cand.title = _clean_title(title, 60)
                cand.description = (
                    cand.description.replace(old_first, cand.title, 1) if old_first else cand.title
                )
            if hook:
                cand.hook = _clean_title(hook, 52)
            if isinstance(vscore, (int, float)) and not isinstance(vscore, bool) and 0 <= float(vscore) <= 100:
                cand.score = round(0.65 * float(vscore) + 0.35 * float(cand.score), 1)
                cand.features["score"] = cand.score
            if reason:
                cand.hook_info = dict(cand.hook_info or {})
                cand.hook_info["llm_reason"] = reason[:140]
            cleaned_tags = _clean_hashtags(tags)
            if cleaned_tags:
                cand.hashtags = cleaned_tags
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
