"""Cari video yang sedang viral, untuk dijadikan bahan clip.

Dua backend:

1. **YouTube Data API v3** (paling akurat, butuh OAuth/login yang sudah dipakai untuk
   upload, atau cukup API key gratis). Endpoint yang dipakai:
   - `videos.list(chart=mostPopular)` → trending per negara (1 unit kuota)
   - `search.list(order=viewCount)` + `videos.list` → video paling banyak ditonton
     untuk keyword tertentu dalam rentang waktu (≈101 unit kuota)
2. **yt-dlp** (tanpa login, tanpa kuota): feed trending YouTube + pencarian YouTube,
   lalu opsional "diperkaya" (enrich) dengan metadata lengkap per video.

Hasilnya dinormalkan, diberi skor viral (views/jam + total views + engagement),
lalu disimpan di SQLite supaya bisa dibuka lagi tanpa query ulang.
"""
from __future__ import annotations

import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import db
from .config import load_settings

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"

REGIONS = [
    ("ID", "Indonesia"), ("MY", "Malaysia"), ("SG", "Singapura"), ("US", "Amerika"),
    ("GB", "Inggris"), ("IN", "India"), ("PH", "Filipina"), ("TH", "Thailand"),
    ("JP", "Jepang"), ("KR", "Korea"), ("AU", "Australia"), ("DE", "Jerman"),
    ("BR", "Brasil"), ("SA", "Arab Saudi"), ("NG", "Nigeria"), ("CA", "Kanada"),
]

# kategori YouTube (videoCategoryId)
CATEGORIES = [
    ("", "Semua kategori"),
    ("24", "Entertainment"),
    ("22", "People & Blogs"),
    ("27", "Pendidikan"),
    ("23", "Komedi"),
    ("20", "Gaming"),
    ("10", "Musik"),
    ("17", "Olahraga"),
    ("28", "Sains & Teknologi"),
    ("26", "Howto & Style"),
    ("25", "Berita & Politik"),
]

# preset kata kunci: jenis video panjang yang paling sering di-clip
PRESET_QUERIES = [
    ("podcast", "Podcast"),
    ("podcast indonesia", "Podcast ID"),
    ("motivasi hidup", "Motivasi"),
    ("ceramah agama", "Ceramah"),
    ("bisnis online", "Bisnis"),
    ("tutorial edit video", "Tutorial"),
    ("wawancara eksklusif", "Wawancara"),
    ("curhat kehidupan", "Curhat"),
    ("edukasi sains", "Edukasi"),
    ("reaksi film", "Reaksi"),
    ("gameplay lucu", "Gaming"),
    ("standup comedy indonesia", "Komedi"),
    ("tips keuangan", "Keuangan"),
    ("kesehatan mental", "Kesehatan"),
    ("sepak bola analisa", "Olahraga"),
]

_search_lock = threading.Lock()


class DiscoverError(RuntimeError):
    pass


@dataclass
class SearchParams:
    queries: list[str] = field(default_factory=list)
    region: str = "ID"
    days: int = 30
    min_views: int = 100_000
    min_duration: int = 0            # detik
    max_duration: int = 0            # detik, 0 = tanpa batas
    limit: int = 24
    include_trending: bool = True
    category: str = ""
    enrich: bool = True
    use_api: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SearchParams":
        settings = load_settings()
        raw_queries = payload.get("queries")
        if isinstance(raw_queries, str):
            raw_queries = re.split(r"[,\n;]+", raw_queries)
        queries = [str(q).strip() for q in (raw_queries or []) if str(q).strip()][:6]
        if not queries:
            saved = (settings.get("discover_queries") or "").strip()
            queries = [q.strip() for q in re.split(r"[,\n;]+", saved) if q.strip()][:6]

        def _int(key: str, default: int) -> int:
            try:
                return int(payload.get(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            queries=queries,
            region=(payload.get("region") or settings.get("discover_region") or "ID").upper()[:2],
            days=max(1, min(365, _int("days", int(settings.get("discover_days") or 30)))),
            min_views=max(0, _int("min_views", int(settings.get("discover_min_views") or 100_000))),
            min_duration=max(0, _int("min_duration", int(settings.get("discover_min_duration") or 0))),
            max_duration=max(0, _int("max_duration", int(settings.get("discover_max_duration") or 0))),
            limit=max(4, min(60, _int("limit", int(settings.get("discover_limit") or 24)))),
            include_trending=bool(payload.get("include_trending", True)),
            category=str(payload.get("category") or ""),
            enrich=bool(payload.get("enrich", True)),
            use_api=bool(payload.get("use_api", True)),
        )


# --------------------------------------------------------------------------- #
# util
# --------------------------------------------------------------------------- #
def _now() -> float:
    return time.time()


def _iso_to_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).timestamp()
    except ValueError:
        pass
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[: len(fmt) + 2], fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return 0.0


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_duration(value: Any) -> float:
    """ISO8601 (PT1H2M3S) atau detik/teks durasi."""
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    match = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text)
    if match:
        days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
        return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = text.split(":")
    try:
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
        return total
    except ValueError:
        return 0.0


def _fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def viral_score(views: int, published_at: float, likes: int = 0, comments: int = 0) -> tuple[float, float]:
    """Kembalikan (skor 0-100, views per jam)."""
    views = max(0, int(views or 0))
    if views <= 0:
        return 0.0, 0.0
    if published_at > 0:
        age_hours = max(0.5, (_now() - published_at) / 3600.0)
    else:
        age_hours = 24 * 30.0  # tidak diketahui → anggap sebulan
    vph = views / age_hours

    def norm(value: float, low: float, high: float) -> float:
        if value <= low:
            return 0.0
        if value >= high:
            return 1.0
        return (value - low) / (high - low)

    velocity = norm(math.log10(max(1.0, vph)), 1.0, 5.2)      # 10 → 160k views/jam
    reach = norm(math.log10(max(1.0, views)), 3.7, 7.3)       # 5k → 20 juta
    engagement = 0.0
    if views >= 1000:
        like_rate = likes / views
        comment_rate = comments / views
        engagement = min(1.0, norm(like_rate, 0.005, 0.09) * 0.75 + norm(comment_rate, 0.0003, 0.004) * 0.25)

    score = 100.0 * (0.50 * velocity + 0.32 * reach + 0.18 * engagement)
    return round(min(100.0, score), 1), round(vph, 1)


def _normalize(
    item: dict[str, Any],
    query: str,
    source: str,
    region: str,
) -> dict[str, Any] | None:
    """Ubah item dari YouTube API / yt-dlp menjadi bentuk seragam."""
    ext_id = str(item.get("ext_id") or item.get("id") or "").strip()
    if not ext_id:
        return None
    views = _int_or_zero(item.get("views"))
    likes = _int_or_zero(item.get("likes"))
    comments = _int_or_zero(item.get("comments"))
    duration = _parse_duration(item.get("duration"))
    published_at = float(item.get("published_at") or 0)
    if not published_at:
        published_at = _iso_to_ts(item.get("upload_date") or item.get("published"))
    score, vph = viral_score(views, published_at, likes, comments)
    url = item.get("url") or f"https://www.youtube.com/watch?v={ext_id}"
    if ext_id.isalnum() or "-" in ext_id or "_" in ext_id:
        if "youtube" in url or item.get("platform", "youtube") == "youtube":
            url = f"https://www.youtube.com/watch?v={ext_id}"
    thumbnail = item.get("thumbnail") or f"https://i.ytimg.com/vi/{ext_id}/hqdefault.jpg"
    if isinstance(thumbnail, list):
        thumbnail = thumbnail[-1] if thumbnail else ""
    title = (item.get("title") or "").strip()
    if not title:
        return None
    return {
        "platform": item.get("platform") or "youtube",
        "ext_id": ext_id,
        "url": url,
        "title": title,
        "channel": (item.get("channel") or item.get("uploader") or "").strip(),
        "channel_url": item.get("channel_url") or "",
        "thumbnail": thumbnail,
        "duration": round(duration, 2),
        "views": views,
        "likes": likes,
        "comments": comments,
        "published_at": published_at,
        "vph": vph,
        "score": score,
        "is_short": bool(0 < duration <= 180),
        "query": query,
        "source": source,
        "region": region,
        "meta": {
            "live": bool(item.get("live") or item.get("is_live")),
            "description": (item.get("description") or "")[:400],
            "view_count_raw": item.get("view_count_raw", ""),
        },
    }


def _passes(item: dict[str, Any], params: SearchParams) -> bool:
    duration = float(item.get("duration") or 0)
    if params.min_views and int(item.get("views") or 0) < params.min_views:
        return False
    if params.min_duration and duration and duration < params.min_duration:
        return False
    if params.max_duration and duration and duration > params.max_duration:
        return False
    if params.days and item.get("published_at"):
        age_days = (_now() - float(item["published_at"])) / 86400
        if age_days > params.days:
            return False
    if item["meta"].get("live"):
        return False
    return True


# --------------------------------------------------------------------------- #
# backend 1: YouTube Data API
# --------------------------------------------------------------------------- #
def api_key() -> str:
    settings = load_settings()
    return (settings.get("discover_api_key") or "").strip()


def _api_available() -> bool:
    if api_key():
        return True
    try:
        from . import youtube

        return youtube.is_connected()
    except Exception:
        return False


def _api_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    key = api_key()
    if key:
        params = {**params, "key": key}
        url = f"{YOUTUBE_API}/{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                return _json_loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                payload = _json_loads(exc.read())
                detail = ((payload.get("error") or {}).get("message") or "")[:200]
            except Exception:
                pass
            if exc.code in (401, 403):
                raise DiscoverError(
                    "API key YouTube ditolak / kuota habis"
                    + (f": {detail}" if detail else "")
                    + ". Matikan 'pakai API resmi' untuk memakai mode yt-dlp (tanpa kuota)."
                ) from exc
            raise DiscoverError(f"YouTube API error {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise DiscoverError(f"Tidak bisa menghubungi YouTube API ({exc.reason})") from exc

    # tanpa API key → pakai OAuth milik user (scope readonly sudah diminta saat login)
    from . import youtube

    try:
        yt = youtube._youtube_client()  # noqa: SLF001 - dipakai internal
    except Exception as exc:
        raise DiscoverError(f"Gagal memakai akun YouTube: {exc}") from exc
    service_path, _, method = path.partition("/")
    try:
        result = getattr(getattr(yt, service_path)(), method)(**params).execute()
        return result or {}
    except Exception as exc:
        raise DiscoverError(f"YouTube API error: {exc}") from exc


def _json_loads(raw: bytes) -> dict[str, Any]:
    import json

    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {}


def _api_videos_by_ids(ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    data = _api_get(
        "videos/list",
        {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(ids[:50]),
            "maxResults": 50,
        },
    )
    result: dict[str, dict[str, Any]] = {}
    for item in data.get("items") or []:
        result[item.get("id", "")] = item
    return result


def _api_item_to_flat(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    details = item.get("contentDetails") or {}
    thumbs = snippet.get("thumbnails") or {}
    thumb = (thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {}).get("url", "")
    return {
        "ext_id": item.get("id", ""),
        "title": snippet.get("title", ""),
        "channel": snippet.get("channelTitle", ""),
        "channel_url": (
            f"https://www.youtube.com/channel/{snippet['channelId']}" if snippet.get("channelId") else ""
        ),
        "thumbnail": thumb,
        "duration": details.get("duration", ""),
        "views": stats.get("viewCount"),
        "likes": stats.get("likeCount"),
        "comments": stats.get("commentCount"),
        "published_at": _iso_to_ts(snippet.get("publishedAt")),
        "description": snippet.get("description", ""),
        "live": (snippet.get("liveBroadcastContent") or "none") != "none",
    }


def api_trending(params: SearchParams) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "part": "snippet,statistics,contentDetails",
        "chart": "mostPopular",
        "regionCode": params.region,
        "maxResults": 50,
    }
    if params.category:
        query["videoCategoryId"] = params.category
    data = _api_get("videos/list", query)
    items: list[dict[str, Any]] = []
    for raw in data.get("items") or []:
        flat = _api_item_to_flat(raw)
        flat["url"] = f"https://www.youtube.com/watch?v={flat['ext_id']}"
        items.append(flat)
    return items


def api_search(query_text: str, params: SearchParams) -> list[dict[str, Any]]:
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=min(365, max(1, params.days)))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    query: dict[str, Any] = {
        "part": "snippet",
        "q": query_text,
        "type": "video",
        "order": "viewCount",
        "maxResults": min(50, max(10, params.limit)),
        "publishedAfter": published_after,
        "regionCode": params.region,
        "relevanceLanguage": "id" if params.region in ("ID", "MY") else "",
    }
    if params.min_duration and params.min_duration >= 240:
        query["videoDuration"] = "long"
    elif params.min_duration and params.min_duration >= 60:
        query["videoDuration"] = "medium"
    if not query["relevanceLanguage"]:
        query.pop("relevanceLanguage")
    data = _api_get("search/list", query)
    ids = [
        (item.get("id") or {}).get("videoId", "")
        for item in data.get("items") or []
        if (item.get("id") or {}).get("videoId")
    ]
    details = _api_videos_by_ids(ids)
    items: list[dict[str, Any]] = []
    for video_id in ids:
        raw = details.get(video_id)
        if not raw:
            continue
        flat = _api_item_to_flat(raw)
        flat["url"] = f"https://www.youtube.com/watch?v={video_id}"
        items.append(flat)
    return items


# --------------------------------------------------------------------------- #
# backend 2: yt-dlp (tanpa login / tanpa kuota)
# --------------------------------------------------------------------------- #
def _ydl_opts(flat: bool = True, limit: int = 40) -> dict[str, Any]:
    settings = load_settings()
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "ignoreerrors": True,
        "extract_flat": "in_playlist" if flat else False,
        "playlistend": max(1, limit),
        "socket_timeout": 15,
        "retries": 1,
        "geo_bypass": True,
    }
    cookies = (settings.get("ytdlp_cookies_browser") or "").strip()
    if cookies:
        opts["cookiesfrombrowser"] = (cookies,)
    proxy = (settings.get("ytdlp_proxy") or "").strip()
    if proxy:
        opts["proxy"] = proxy
    return opts


def _ytdlp_module():
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise DiscoverError("yt-dlp belum terinstall (pip install yt-dlp)") from exc
    return yt_dlp


def _entries(info: Any) -> list[dict[str, Any]]:
    if not info:
        return []
    if isinstance(info, dict) and info.get("entries"):
        return [e for e in info["entries"] if isinstance(e, dict)]
    return [info] if isinstance(info, dict) else []


def ytdlp_trending(params: SearchParams) -> list[dict[str, Any]]:
    yt_dlp = _ytdlp_module()
    urls = [
        f"https://www.youtube.com/feed/trending?gl={params.region}",
        "https://www.youtube.com/playlist?list=PLrEnWoR732-BHrPp_Pm8_VleD68f9s14-",  # music trending
    ]
    results: list[dict[str, Any]] = []
    for url in urls[:1]:
        try:
            with yt_dlp.YoutubeDL(_ydl_opts(flat=True, limit=40)) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001 - jaringan/format bisa apa saja
            raise DiscoverError(f"Gagal ambil trending YouTube via yt-dlp: {exc}") from exc
        for entry in _entries(info):
            results.append(
                {
                    "ext_id": entry.get("id", ""),
                    "title": entry.get("title", ""),
                    "channel": entry.get("channel") or entry.get("uploader") or "",
                    "channel_url": entry.get("channel_url") or entry.get("uploader_url") or "",
                    "thumbnail": _pick_thumb(entry),
                    "duration": entry.get("duration"),
                    "views": entry.get("view_count"),
                    "published_at": _iso_to_ts(entry.get("upload_date")) or float(entry.get("timestamp") or 0),
                    "description": entry.get("description") or "",
                    "url": entry.get("url") or entry.get("webpage_url") or "",
                    "live": entry.get("live_status") in ("is_live", "is_upcoming"),
                    "view_count_raw": entry.get("view_count"),
                }
            )
    return results


def ytdlp_search(query_text: str, params: SearchParams) -> list[dict[str, Any]]:
    yt_dlp = _ytdlp_module()
    count = min(40, max(params.limit, 12))
    urls = [
        f"ytsearch{count}:{query_text}",
        f"ytsearchdate{count}:{query_text}",
    ]
    results: list[dict[str, Any]] = []
    error: Exception | None = None
    for url in urls:
        try:
            with yt_dlp.YoutubeDL(_ydl_opts(flat=True, limit=count)) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001
            error = exc
            continue
        for entry in _entries(info):
            results.append(
                {
                    "ext_id": entry.get("id", ""),
                    "title": entry.get("title", ""),
                    "channel": entry.get("channel") or entry.get("uploader") or "",
                    "channel_url": entry.get("channel_url") or entry.get("uploader_url") or "",
                    "thumbnail": _pick_thumb(entry),
                    "duration": entry.get("duration"),
                    "views": entry.get("view_count"),
                    "published_at": _iso_to_ts(entry.get("upload_date")) or float(entry.get("timestamp") or 0),
                    "description": entry.get("description") or "",
                    "url": entry.get("url") or entry.get("webpage_url") or "",
                    "live": entry.get("live_status") in ("is_live", "is_upcoming"),
                }
            )
    if not results and error is not None:
        raise DiscoverError(f"Pencarian YouTube via yt-dlp gagal: {error}") from error
    return results


def _pick_thumb(entry: dict[str, Any]) -> str:
    thumbs = entry.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        best = sorted(
            (t for t in thumbs if isinstance(t, dict)),
            key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
        )
        for thumb in reversed(best):
            url = thumb.get("url")
            if url:
                return url
    return entry.get("thumbnail") or ""


def ytdlp_enrich(items: list[dict[str, Any]], params: SearchParams) -> None:
    """Lengkapi metadata (views, tanggal, likes) untuk item yang datanya kosong."""
    targets = [
        item for item in items
        if not item.get("published_at") or not item.get("views") or not item.get("duration")
    ][: max(0, min(14, params.limit))]
    if not targets:
        return
    yt_dlp = _ytdlp_module()

    def fetch(item: dict[str, Any]) -> None:
        url = item.get("url") or f"https://www.youtube.com/watch?v={item['ext_id']}"
        try:
            with yt_dlp.YoutubeDL(
                {
                    **_ydl_opts(flat=False, limit=1),
                    "extract_flat": False,
                    "playlistend": 1,
                    "skip_download": True,
                    "format": "bestaudio/best",
                }
            ) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:  # noqa: BLE001 - biarkan data apa adanya
            return
        if not isinstance(info, dict):
            return
        if info.get("view_count"):
            item["views"] = info.get("view_count")
        if info.get("like_count"):
            item["likes"] = info.get("like_count")
        if info.get("comment_count"):
            item["comments"] = info.get("comment_count")
        if info.get("duration"):
            item["duration"] = info.get("duration")
        stamp = _iso_to_ts(info.get("upload_date")) or float(info.get("timestamp") or 0)
        if stamp:
            item["published_at"] = stamp
        if info.get("channel"):
            item["channel"] = info.get("channel")
        if info.get("title"):
            item["title"] = info.get("title")
        if not item.get("thumbnail"):
            item["thumbnail"] = _pick_thumb(info)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="enrich") as pool:
        list(pool.map(fetch, targets))


# --------------------------------------------------------------------------- #
# orkestrasi
# --------------------------------------------------------------------------- #
def backends() -> dict[str, Any]:
    settings = load_settings()
    return {
        "api_key": bool(api_key()),
        "oauth": _oauth_connected(),
        "api_available": _api_available(),
        "ytdlp": _ytdlp_available(),
        "prefer_api": bool(settings.get("discover_use_api", True)),
    }


def _oauth_connected() -> bool:
    try:
        from . import youtube

        return youtube.is_connected()
    except Exception:
        return False


def _ytdlp_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("yt_dlp") is not None
    except Exception:
        return False


def search(params: SearchParams, on_progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Jalankan pencarian, simpan hasil, kembalikan ringkasannya."""
    if not _search_lock.acquire(blocking=False):
        raise DiscoverError("Pencarian lain masih berjalan, tunggu sebentar.")
    try:
        notes: list[str] = []
        raw_items: list[dict[str, Any]] = []
        used_backend = ""
        use_api = params.use_api and _api_available()

        def note(message: str) -> None:
            notes.append(message)
            if on_progress:
                on_progress(message)

        if use_api:
            used_backend = "youtube-api"
            if params.include_trending:
                note("Mengambil trending YouTube…")
                try:
                    for flat in api_trending(params):
                        raw_items.append({**flat, "query": "", "source": "trending"})
                except DiscoverError as exc:
                    note(f"Trending API gagal: {exc}")
            for query in params.queries:
                note(f"Mencari “{query}” (paling banyak ditonton {params.days} hari)…")
                try:
                    for flat in api_search(query, params):
                        raw_items.append({**flat, "query": query, "source": "search"})
                except DiscoverError as exc:
                    note(f"Pencarian “{query}” gagal: {exc}")

        if not raw_items:
            if not _ytdlp_available():
                raise DiscoverError(
                    "yt-dlp tidak tersedia dan YouTube API tidak bisa dipakai. "
                    "Install yt-dlp (pip install yt-dlp) atau isi API key YouTube di Pengaturan."
                )
            used_backend = used_backend or "yt-dlp"
            if params.include_trending:
                note("Mengambil trending YouTube (yt-dlp)…")
                try:
                    for flat in ytdlp_trending(params):
                        raw_items.append({**flat, "query": "", "source": "trending"})
                except DiscoverError as exc:
                    notes.append(str(exc))
            for query in params.queries:
                note(f"Mencari “{query}” (yt-dlp)…")
                try:
                    for flat in ytdlp_search(query, params):
                        raw_items.append({**flat, "query": query, "source": "search"})
                except DiscoverError as exc:
                    notes.append(f"“{query}”: {exc}")
            if not raw_items:
                detail = " | ".join(notes[-2:])[:300]
                raise DiscoverError(
                    "Tidak ada hasil dari YouTube. Kemungkinan server ini tidak punya akses internet, "
                    "YouTube meminta verifikasi (isi cookies browser di Pengaturan), atau kata kuncinya terlalu sempit."
                    + (f" Detail: {detail}" if detail else "")
                )
            if params.enrich:
                note("Melengkapi data views & tanggal…")
                ytdlp_enrich(raw_items, params)

        # normalisasi + dedupe
        normalized: dict[str, dict[str, Any]] = {}
        for flat in raw_items:
            item = _normalize(
                flat, flat.get("query", ""), flat.get("source", "search"), params.region
            )
            if not item:
                continue
            key = item["ext_id"]
            existing = normalized.get(key)
            if existing:
                # gabungkan: pakai data terlengkap dari dua sumber (trending & pencarian)
                best = item if item["score"] > existing["score"] else existing
                other = existing if best is item else item
                for key_name in ("views", "likes", "comments", "duration", "published_at"):
                    if not best.get(key_name) and other.get(key_name):
                        best[key_name] = other[key_name]
                if not best.get("thumbnail") and other.get("thumbnail"):
                    best["thumbnail"] = other["thumbnail"]
                if not best.get("channel") and other.get("channel"):
                    best["channel"] = other["channel"]
                best["query"] = other.get("query") or best.get("query") or ""
                normalized[key] = best
                continue
            normalized[key] = item

        candidates = list(normalized.values())
        kept = [item for item in candidates if _passes(item, params)]
        # kalau filter terlalu ketat, jangan balik kosong: longgarkan min_views
        if not kept and candidates:
            kept = sorted(candidates, key=lambda i: -i["score"])[: params.limit]
            notes.append("Filter terlalu ketat — menampilkan hasil terbaik tanpa filter ketat.")
        if params.category and used_backend == "youtube-api":
            kept = kept  # kategori hanya berlaku untuk trending via API

        kept.sort(key=lambda item: (-item["score"], -item["views"]))
        kept = kept[: params.limit]

        saved = db.upsert_discoveries(kept)
        note(f"{len(kept)} video viral ditemukan")
        return {
            "backend": used_backend,
            "notes": notes,
            "count": len(kept),
            "saved": saved,
            "items": db.list_discoveries(limit=params.limit, sort="score"),
        }
    finally:
        _search_lock.release()


def trending_now(
    region: str = "ID",
    limit: int = 24,
    min_views: int = 0,
    category: str = "",
    days: int = 30,
    use_api: bool = True,
    enrich: bool = False,
) -> dict[str, Any]:
    params = SearchParams(
        queries=[], region=region, days=days, min_views=min_views, limit=limit,
        include_trending=True, category=category, enrich=enrich, use_api=use_api,
    )
    return search(params)


def suggestion_queries(region: str = "ID") -> list[dict[str, str]]:
    """Preset kata kunci + saran dari judul yang sudah tersimpan."""
    presets = [{"query": q, "label": label} for q, label in PRESET_QUERIES]
    seen = {p["query"].lower() for p in presets}
    try:
        for item in db.list_discoveries(limit=40, sort="recent"):
            channel = (item.get("channel") or "").strip()
            if channel and channel.lower() not in seen and len(channel) < 40:
                seen.add(channel.lower())
                presets.append({"query": channel, "label": channel})
            if len(presets) >= 26:
                break
    except Exception:
        pass
    return presets
