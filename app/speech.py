"""Keselarasan teks ↔ ucapan.

Modul ini menjawab satu masalah yang bikin hook overlay terasa "melenceng":

    videonya bilang "Saya", teks di layar menulis "Ada"

Sumber masalahnya cuma dua:

1. **teksnya beda kata** — hook diambil dari judul/parafrase, bukan dari kata
   yang benar-benar diucapkan;
2. **waktunya beda** — teks muncul di detik yang salah, jadi walau katanya sama
   tetap terlihat tidak sinkron.

Semua fungsi di sini bekerja dari transkrip yang sudah punya *word timing*
(Whisper) dan dari daftar segmen clip (trim + speed), lalu menerjemahkan waktu
sumber ke waktu **output** (timeline video hasil render). Jadi teks hook bisa
dipasang tepat di kata yang sedang diucapkan.

Yang disediakan:
- ``words_in_output``  : semua kata yang terdengar di clip, lengkap dengan waktu output
- ``hook_from_speech`` : hook **verbatim** dari ucapan pembuka (tidak mengarang kata)
- ``align_words``      : menempelkan teks hook apa pun ke kata yang cocok di ucapan
- ``verify_text``      : seberapa cocok sebuah teks dengan ucapan (+ saran perbaikan)

Semua fungsi murni (tanpa I/O) supaya mudah diuji.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Iterable

# Kata pembuka yang biasanya cuma "pemanasan" dan jelek kalau jadi hook.
FILLERS = {
    "jadi", "oke", "ok", "okay", "eh", "ehm", "em", "emm", "hmm", "hm", "uh",
    "ah", "oh", "nah", "ya", "yah", "kan", "sih", "gitu", "gini", "begini",
    "kayak", "kaya", "jadi gini", "so", "well", "um", "uhh", "right", "like",
}

# Akhir kalimat → hook berhenti di situ supaya tidak menggantung.
_SENTENCE_END = "[.!?…]"

# Jeda yang lebih besar dari ini dianggap batas klausa (hook berhenti).
CLAUSE_GAP = 0.45


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# --------------------------------------------------------------------------- #
# normalisasi teks
# --------------------------------------------------------------------------- #
def norm_token(text: str) -> str:
    """Huruf kecil tanpa tanda baca — bentuk yang dipakai untuk membandingkan."""
    out = []
    for ch in (text or "").lower():
        if ch.isalnum() or ch == "'":
            out.append(ch)
        elif ch in "-_/":
            out.append(" ")
    return " ".join("".join(out).split())


def _tokens(text: str) -> list[str]:
    return [tok for tok in (norm_token(part) for part in (text or "").split()) if tok]


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(
                previous[j] + 1,          # hapus
                current[j - 1] + 1,       # tambah
                previous[j - 1] + (ca != cb),  # ganti
            ))
        previous = current
    return previous[-1]


def similar(a: str, b: str) -> bool:
    """Dua kata dianggap sama kalau hanya beda ejaan kecil (mis. "gak"/"nggak")."""
    if not a or not b:
        return False
    if a == b:
        return True
    shortest, longest = (a, b) if len(a) <= len(b) else (b, a)
    if len(shortest) >= 4 and longest.startswith(shortest) and len(longest) - len(shortest) <= 3:
        return True  # "makan" vs "makannya", "ngomong" vs "ngomongin"
    allowed = 1 if len(longest) <= 7 else 2
    if _levenshtein(a, b) <= allowed:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.86


# --------------------------------------------------------------------------- #
# waktu output (trim + speed)
# --------------------------------------------------------------------------- #
def _out_ranges(segments: Iterable[dict[str, Any]]) -> list[dict[str, float]]:
    """Salinan kecil dari ``editor.with_out_ranges`` (tanpa import melingkar)."""
    ranges: list[dict[str, float]] = []
    cursor = 0.0
    for seg in segments or []:
        start = _f(seg.get("start"))
        end = max(start + 0.05, _f(seg.get("end"), start + 1.0))
        speed = max(0.5, min(2.0, _f(seg.get("speed"), 1.0)))
        duration = (end - start) / speed
        ranges.append({"start": start, "end": end, "speed": speed,
                       "out_start": round(cursor, 3), "out_end": round(cursor + duration, 3)})
        cursor += duration
    return ranges


def _iter_words(transcript: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Semua kata dari transkrip (Whisper punya kata; subtitle tidak → dibagi rata)."""
    words: list[dict[str, Any]] = []
    for seg in (transcript or {}).get("segments") or []:
        seg_words = seg.get("words") or []
        if seg_words:
            for word in seg_words:
                text = str(word.get("text") or "").strip()
                if not text:
                    continue
                start = _f(word.get("start"), _f(seg.get("start")))
                end = _f(word.get("end"), start + 0.25)
                words.append({"text": text, "start": start, "end": max(start + 0.05, end),
                              "prob": _f(word.get("prob"), 1.0)})
            continue
        tokens = str(seg.get("text") or "").split()
        if not tokens:
            continue
        start, end = _f(seg.get("start")), _f(seg.get("end"), _f(seg.get("start")) + 1.0)
        step = max(0.05, (end - start) / max(1, len(tokens)))
        for i, token in enumerate(tokens):
            words.append({"text": token, "start": start + i * step,
                          "end": start + (i + 1) * step, "prob": 0.5})
    words.sort(key=lambda w: w["start"])
    # tanda baca berdiri sendiri (".", ",") digabung ke kata sebelumnya, bukan
    # jadi "kata" yang ikut tampil di caption/hook
    merged: list[dict[str, Any]] = []
    for word in words:
        if not norm_token(word["text"]):
            if merged:
                merged[-1]["text"] = merged[-1]["text"] + word["text"]
                merged[-1]["end"] = max(merged[-1]["end"], word["end"])
            continue
        merged.append(word)
    return merged


def words_in_output(
    transcript: dict[str, Any] | None,
    segments: list[dict[str, Any]],
    *,
    min_prob: float = 0.0,
) -> list[dict[str, Any]]:
    """Kata yang benar-benar terdengar di dalam clip, dalam waktu *output*.

    Kata yang cuma sebagian masuk segmen dipotong (bukan dibuang) supaya caption
    dan hook tidak pernah menampilkan kata yang belum/tidak diucapkan.
    """
    ranges = _out_ranges(segments)
    result: list[dict[str, Any]] = []
    for word in _iter_words(transcript):
        if _f(word.get("prob"), 1.0) < min_prob:
            continue
        for seg in ranges:
            if word["end"] <= seg["start"] or word["start"] >= seg["end"]:
                continue
            src_start = max(word["start"], seg["start"])
            src_end = min(word["end"], seg["end"])
            if src_end - src_start < 0.02:
                continue
            start = seg["out_start"] + (src_start - seg["start"]) / seg["speed"]
            end = seg["out_start"] + (src_end - seg["start"]) / seg["speed"]
            result.append({
                "text": word["text"],
                "start": round(start, 3),
                "end": round(max(start + 0.06, end), 3),
                "prob": round(_f(word.get("prob"), 1.0), 3),
                "src_start": round(src_start, 3),
                "src_end": round(src_end, 3),
            })
            break
    result.sort(key=lambda w: w["start"])
    # jaga-jaga: kata yang tumpang tindih karena potongan segmen
    cleaned: list[dict[str, Any]] = []
    for word in result:
        if cleaned and word["start"] < cleaned[-1]["start"] - 0.001:
            continue
        cleaned.append(word)
    return cleaned


# --------------------------------------------------------------------------- #
# hook verbatim dari ucapan
# --------------------------------------------------------------------------- #
def hook_from_speech(
    words: list[dict[str, Any]],
    *,
    max_words: int = 12,
    max_seconds: float = 4.5,
    skip_fillers: bool = True,
) -> dict[str, Any]:
    """Ambil hook **dari kata yang diucapkan** (tanpa menambah/mengganti kata).

    Hasilnya selalu bisa dipakai sebagai teks overlay karena setiap kata punya
    waktu ucapannya sendiri → sinkron sempurna, dan tidak mungkin muncul kata
    yang tidak pernah diucapkan.
    """
    if not words:
        return {"text": "", "words": [], "start": 0.0, "end": 0.0, "skipped": []}

    start_idx = 0
    skipped: list[str] = []
    if skip_fillers:
        # maksimal 3 kata pembuka, dan jangan sampai seluruh hook habis
        while start_idx < min(3, max(0, len(words) - 3)):
            token = norm_token(words[start_idx]["text"])
            if token in FILLERS:
                skipped.append(words[start_idx]["text"])
                start_idx += 1
                continue
            break

    start = _f(words[start_idx]["start"])
    chosen: list[dict[str, Any]] = []
    for word in words[start_idx:]:
        if len(chosen) >= max(1, max_words):
            break
        if chosen and _f(word["start"]) - start > max_seconds:
            break
        # jeda panjang = pembicara berhenti; hook jangan menyeberang ke kalimat lain
        if chosen and _f(word["start"]) - _f(chosen[-1]["end"]) > CLAUSE_GAP:
            break
        if chosen and _f(word["end"]) - start > max_seconds and len(chosen) >= 3:
            break
        chosen.append(word)
        if len(chosen) >= 3 and str(word["text"]).rstrip().endswith((".", "!", "?", "…")):
            break

    if not chosen:
        return {"text": "", "words": [], "start": 0.0, "end": 0.0, "skipped": skipped}

    text = " ".join(str(w["text"]).strip() for w in chosen).strip()
    text = text.rstrip(" ,;:.!?…")
    return {
        "text": text,
        "words": [
            {"text": str(w["text"]).strip(), "start": round(_f(w["start"]), 3),
             "end": round(_f(w["end"]), 3)}
            for w in chosen
        ],
        "start": round(_f(chosen[0]["start"]), 3),
        "end": round(_f(chosen[-1]["end"]), 3),
        "skipped": skipped,
    }


# --------------------------------------------------------------------------- #
# menempelkan teks (judul) ke kata yang diucapkan
# --------------------------------------------------------------------------- #
def align_words(
    text: str,
    words: list[dict[str, Any]],
    *,
    start: float | None = None,
    end: float | None = None,
) -> list[dict[str, Any]]:
    """Beri waktu ucapan untuk setiap kata pada ``text``.

    Kalau sebuah kata hook tidak ada di ucapan, waktunya dibagi rata di antara
    tetangga (supaya highlight tetap berjalan mulus, tidak melompat).
    """
    tokens = [tok for tok in (text or "").split() if tok.strip()]
    if not tokens:
        return []
    keys = [norm_token(tok) for tok in tokens]
    matched: list[dict[str, Any] | None] = [None] * len(tokens)

    if words:
        pos = 0
        for i, key in enumerate(keys):
            if not key:
                continue
            for j in range(pos, len(words)):
                if similar(key, norm_token(words[j]["text"])):
                    matched[i] = words[j]
                    pos = j + 1
                    break

    first_hit = next((m for m in matched if m is not None), None)
    default_start = _f(first_hit["start"]) if first_hit else (
        _f(words[0]["start"]) if words else 0.0
    )
    span_start = _f(start, default_start)
    span_end = _f(end, _f(words[-1]["end"]) if words else span_start + max(2.0, 0.32 * len(tokens)))
    if span_end <= span_start:
        span_end = span_start + max(1.0, 0.32 * len(tokens))

    # isi yang belum ketemu dengan waktu rata di antara penanda terdekat
    result: list[dict[str, Any]] = []
    last_end = span_start
    for i, token in enumerate(tokens):
        hit = matched[i]
        if hit is not None:
            word_start = max(last_end, _f(hit["start"]))
            word_end = max(word_start + 0.1, _f(hit["end"]))
        else:
            word_start = last_end
            word_end = word_start + 0.3
        result.append({"text": token, "start": round(word_start, 3), "end": round(word_end, 3),
                       "matched": hit is not None})
        last_end = word_end

    if result:
        result[0]["start"] = round(min(result[0]["start"], span_start), 3)
        if result[-1]["end"] < span_end and result[-1]["matched"]:
            pass
        # rapikan supaya berurutan & tidak ada celah negatif
        for i in range(1, len(result)):
            if result[i]["start"] < result[i - 1]["end"]:
                result[i]["start"] = result[i - 1]["end"]
            if result[i]["end"] <= result[i]["start"]:
                result[i]["end"] = round(result[i]["start"] + 0.1, 3)
    return result


# --------------------------------------------------------------------------- #
# verifikasi teks vs ucapan
# --------------------------------------------------------------------------- #
def verify_text(text: str, words: list[dict[str, Any]], *, threshold: float = 0.8) -> dict[str, Any]:
    """Seberapa cocok ``text`` dengan kata yang benar-benar diucapkan.

    Dipakai UI untuk memberi peringatan "teks hook tidak ada di ucapan" plus
    saran kata pengganti (mis. teks "ada" padahal yang diucapkan "saya").
    """
    hook_tokens = _tokens(text)
    spoken = [norm_token(w["text"]) for w in words]
    if not hook_tokens:
        return {"ok": False, "ratio": 0.0, "checked": 0, "matched": 0,
                "missing": [], "suggestions": [], "reason": "teks kosong"}
    if not spoken:
        return {"ok": False, "ratio": 0.0, "checked": len(hook_tokens), "matched": 0,
                "missing": hook_tokens, "suggestions": [], "reason": "tidak ada transkrip"}

    pos = 0
    matched = 0
    missing: list[str] = []
    suggestions: list[dict[str, Any]] = []
    for index, key in enumerate(hook_tokens):
        hit = -1
        for j in range(pos, len(spoken)):
            if similar(key, spoken[j]):
                hit = j
                break
        if hit >= 0:
            matched += 1
            pos = hit + 1
            continue
        missing.append(key)
        # cari kata terdekat di sekitar posisi yang seharusnya
        window = spoken[max(0, pos - 2): pos + 4] or spoken
        best, best_score = "", 0.0
        for candidate in window:
            score = SequenceMatcher(None, key, candidate).ratio()
            if score > best_score:
                best, best_score = candidate, score
        if best and best_score >= 0.45:
            suggestions.append({"index": index, "from": key, "to": best,
                                "score": round(best_score, 2)})

    ratio = matched / len(hook_tokens)
    return {
        "ok": ratio >= threshold,
        "ratio": round(ratio, 3),
        "checked": len(hook_tokens),
        "matched": matched,
        "missing": missing[:8],
        "suggestions": suggestions[:6],
        "reason": "" if ratio >= threshold else "ada kata yang tidak diucapkan di klip ini",
    }


def summarize(text: str, words: list[dict[str, Any]]) -> dict[str, Any]:
    """Ringkasan siap tampil di UI: teks, ucapan pembuka, dan hasil verifikasi."""
    speech = hook_from_speech(words)
    check = verify_text(text, words)
    return {
        "text": (text or "").strip(),
        "speech_text": speech["text"],
        "speech_words": speech["words"],
        "speech_start": speech["start"],
        "speech_end": speech["end"],
        "match": check,
        "needs_fix": bool((text or "").strip()) and not check["ok"],
    }
