"""Upload otomatis ke YouTube Shorts (OAuth device flow, tanpa API berbayar).

Alur:
1. User bikin OAuth client di Google Cloud Console (gratis), tipe
   "TVs and Limited Input devices" (atau "Desktop app").
2. Tempel client id/secret di tab YouTube → klik login → buka URL, masukkan kode.
3. Token disimpan lokal di data/youtube_token.json, refresh otomatis.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .config import TOKEN_FILE, load_settings, update_settings

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class YouTubeError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# kredensial client
# --------------------------------------------------------------------------- #
def client_credentials() -> tuple[str, str]:
    settings = load_settings()
    client_id = (settings.get("yt_client_id") or "").strip()
    client_secret = (settings.get("yt_client_secret") or "").strip()
    if client_id:
        return client_id, client_secret
    # fallback: file client_secret.json dari Google Cloud Console
    from .config import CLIENT_SECRET_FILE

    if CLIENT_SECRET_FILE.exists():
        try:
            data = json.loads(CLIENT_SECRET_FILE.read_text(encoding="utf-8"))
            block = data.get("installed") or data.get("web") or data.get("tv") or data
            return block.get("client_id", ""), block.get("client_secret", "")
        except Exception:
            return "", ""
    return "", ""


def import_client_secret(raw: str) -> dict[str, str]:
    """Terima isi file client_secret.json (atau hanya client id/secret)."""
    raw = (raw or "").strip()
    if not raw:
        raise YouTubeError("Isi client id / JSON client secret dulu")
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise YouTubeError("JSON tidak valid") from exc
        block = data.get("installed") or data.get("web") or data.get("tv") or data
        client_id = block.get("client_id", "")
        client_secret = block.get("client_secret", "")
    elif ":" in raw and " " not in raw:
        client_id, client_secret = raw.split(":", 1)
    else:
        client_id, client_secret = raw, ""
    client_id = client_id.strip()
    if ".apps.googleusercontent.com" not in client_id:
        raise YouTubeError(
            "Client ID harus berakhiran .apps.googleusercontent.com (salin dari Google Cloud Console)"
        )
    update_settings({"yt_client_id": client_id, "yt_client_secret": client_secret.strip()})
    return {"client_id": client_id, "has_secret": "yes" if client_secret.strip() else "no"}


# --------------------------------------------------------------------------- #
# token store
# --------------------------------------------------------------------------- #
def load_token() -> dict[str, Any] | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_token(data: dict[str, Any]) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def is_connected() -> bool:
    token = load_token()
    return bool(token and (token.get("refresh_token") or token.get("token")))


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def _post_form(url: str, payload: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    # PENTING: HTTPError adalah subclass URLError, jadi harus ditangkap lebih dulu.
    # Endpoint token Google membalas HTTP 400 {"error": "authorization_pending"}
    # selama user belum menyetujui di browser — itu bukan gangguan jaringan.
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise YouTubeError(f"Google menolak permintaan ({exc.code})") from exc
    except urllib.error.URLError as exc:
        raise YouTubeError(
            "Tidak bisa menghubungi Google. Cek koneksi internet server ini "
            f"({getattr(exc, 'reason', exc)})"
        ) from exc


def _credentials():
    from google.oauth2.credentials import Credentials  # type: ignore

    token = load_token()
    if not token:
        raise YouTubeError("Belum login YouTube. Buka tab YouTube dan hubungkan akun dulu.")
    client_id, client_secret = client_credentials()
    return Credentials(
        token=token.get("token"),
        refresh_token=token.get("refresh_token"),
        token_uri=TOKEN_URL,
        client_id=token.get("client_id") or client_id,
        client_secret=token.get("client_secret") or client_secret,
        scopes=token.get("scopes") or SCOPES,
    )


def _youtube_client():
    try:
        from googleapiclient.discovery import build  # type: ignore
    except ImportError as exc:
        raise YouTubeError(
            "Library Google belum terinstall. Jalankan: pip install google-api-python-client google-auth-oauthlib"
        ) from exc
    return build("youtube", "v3", credentials=_credentials(), cache_discovery=False)



# --------------------------------------------------------------------------- #
# pesan error yang bisa ditindaklanjuti
# --------------------------------------------------------------------------- #
CONSOLE_AUDIENCE_URL = "https://console.cloud.google.com/apis/credentials/consent"
# batas tunggu login via browser (detik) — cukup untuk login manual, tidak menggantung selamanya
BROWSER_LOGIN_TIMEOUT = 300
CONSOLE_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"

_HELP_TESTERS = {
    "title": "Google memblokir login: akunmu belum terdaftar sebagai test user",
    "steps": [
        "Buka Google Cloud Console → APIs & Services → OAuth consent screen (tab 'Audience'). "
        "Pastikan project yang dibuka SAMA dengan project pemilik Client ID di Clipper "
        "(nama app di pesan Google, mis. 'Tester clipper', adalah nama OAuth app-nya).",
        "Pastikan Publishing status = Testing dan User type = External.",
        "Di bagian 'Test users' klik + ADD USERS, masukkan email Google yang tampil di halaman "
        "blokir Google (mis. namakamu@gmail.com). Akun project owner TIDAK otomatis jadi test user.",
        "Simpan, tunggu ±1 menit (kadang perlu 5 menit / logout-login), lalu ulangi login. "
        "Kalau browser masih memakai akun Google lain, pakai mode Incognito.",
        "Alternatif permanen: klik 'Publish app' → status Production. Selama app belum "
        "diverifikasi Google, layar peringatan 'Google belum memverifikasi aplikasi ini' tetap "
        "muncul — klik Advanced → Continue. Verifikasi hanya wajib kalau dipakai banyak orang.",
        "Selama masih Testing, Google mematikan refresh token setiap 7 hari, jadi sesekali perlu "
        "login ulang (publish app menghilangkan batasan ini).",
    ],
    "link": CONSOLE_AUDIENCE_URL,
    "link_label": "Buka OAuth consent screen",
}

_HELP_WAIT_TIMEOUT = {
    "title": "Login tidak selesai — tidak ada balasan dari Google",
    "steps": [
        "Halaman Google tidak mengirim balik hasil login ke Clipper (biasanya karena jendela "
        "login ditutup, atau Google menampilkan halaman 'Access blocked').",
        "Kalau di browser muncul 'Access blocked … 403 access_denied', akunmu belum terdaftar "
        "sebagai test user: tambahkan di OAuth consent screen → Audience → Test users → "
        "+ ADD USERS, lalu login lagi.",
        "Kalau browser tidak terbuka otomatis, salin URL login yang tercetak di jendela Clipper "
        "ke browser, atau pakai tombol 'Login dengan kode' yang tidak butuh localhost.",
    ],
    "link": CONSOLE_AUDIENCE_URL,
    "link_label": "Buka OAuth consent screen",
}

_HELP_INVALID_CLIENT = {
    "title": "Client ID / secret tidak dikenali Google",
    "steps": [
        "Pastikan Client ID diambil dari project yang sama dengan Client secret.",
        "Untuk login pakai kode, tipe OAuth client harus 'TVs and Limited Input devices'.",
        "Untuk login via browser, tipenya harus 'Desktop app'.",
        "Kalau baru membuat ulang client, simpan ulang kredensialnya di tab Pengaturan YouTube.",
    ],
    "link": CONSOLE_CREDENTIALS_URL,
    "link_label": "Buka Credentials",
}

_HELP_EXPIRED = {
    "title": "Login kedaluwarsa",
    "steps": [
        "Kalau app OAuth masih berstatus Testing, Google mematikan refresh token setiap 7 hari, "
        "jadi kamu perlu login ulang.",
        "Klik 'Login dengan kode' sekali lagi untuk memperbarui token.",
        "Kalau mau permanen, klik 'Publish app' di OAuth consent screen (tanpa verifikasi pun "
        "bisa dipakai untuk akunmu sendiri, cukup lewati layar peringatan).",
    ],
    "link": CONSOLE_AUDIENCE_URL,
    "link_label": "Buka OAuth consent screen",
}


def help_for(raw: str) -> dict[str, Any] | None:
    """Petunjuk langkah-demi-langkah untuk error OAuth yang sering muncul."""
    low = (raw or "").lower()
    if any(k in low for k in ("timed out", "timeout", "tidak ada balasan", "tidak menerima balasan",
                              "login tidak selesai")):
        return _HELP_WAIT_TIMEOUT
    # cocokkan juga pesan ramah hasil friendly_error(), bukan hanya teks mentah Google
    if any(k in low for k in ("verification process", "developer-approved testers", "test user",
                              "berstatus testing", "belum terdaftar")):
        return _HELP_TESTERS
    if "access_denied" in low or "access blocked" in low or "403" in low:
        return _HELP_TESTERS
    if any(k in low for k in ("invalid_client", "unauthorized_client", "client_secret",
                              "client secret", "client id", "tipe oauth client", "tidak dikenali google")):
        return _HELP_INVALID_CLIENT
    if any(k in low for k in ("invalid_grant", "expired", "revoked", "kedaluwarsa", "dicabut")):
        return _HELP_EXPIRED
    return None


def friendly_error(raw: str, default: str = "Gagal login ke YouTube") -> str:
    """Terjemahkan error OAuth mentah jadi penjelasan Bahasa Indonesia."""
    msg = (raw or "").strip()
    if not msg:
        return default
    low = msg.lower()
    name = ""
    if "has not completed the google verification process" in low:
        # nama app diambil dari pesan Google, mis. "Tester clipper has not completed ..."
        head_raw = msg.split(" has not completed", 1)[0]
        head_raw = re.sub(r"^\s*(access blocked|access denied|error\s*403)\s*[:\-]\s*", "", head_raw, flags=re.I)
        name = head_raw.strip().strip("'\"")
    if "verification process" in low or "developer-approved testers" in low:
        head = f'OAuth app "{name}" masih berstatus Testing' if name else "OAuth app masih berstatus Testing"
        return (
            f"{head}, jadi Google hanya mengizinkan akun yang terdaftar sebagai test user. "
            "Tambahkan email Google kamu di Google Cloud Console → APIs & Services → "
            "OAuth consent screen → Audience → Test users → + ADD USERS (pakai akun yang sama "
            "dengan channel YouTube-nya), simpan, lalu ulangi login."
        )
    if "timed out" in low or "tidak ada balasan" in low:
        return (
            "Login tidak selesai: Clipper tidak menerima balasan dari Google. Kalau di browser "
            "muncul 'Access blocked … 403 access_denied', akunmu belum terdaftar sebagai test "
            "user — tambahkan email tersebut di OAuth consent screen → Audience → Test users → "
            "+ ADD USERS, lalu ulangi login. Kalau browser tidak terbuka, salin URL login dari "
            "jendela Clipper ke browser, atau pakai 'Login dengan kode'."
        )
    if "access_denied" in low:
        return (
            "Google menolak akses (access_denied). Biasanya karena akun yang dipakai belum "
            "terdaftar sebagai test user di OAuth consent screen, atau kamu menekan 'Cancel' "
            "di layar izin. Tambahkan email kamu di Test users lalu coba lagi."
        )
    if "invalid_client" in low:
        return ("Client ID / Client secret salah atau tipenya tidak cocok. Login pakai kode "
                "butuh tipe 'TVs and Limited Input devices'; login via browser butuh 'Desktop app'.")
    if "invalid_grant" in low:
        return ("Login sudah kedaluwarsa/dicabut (invalid_grant). Kalau app OAuth masih Testing, "
                "token mati setiap 7 hari. Login ulang di tab YouTube.")
    if "redirect_uri_mismatch" in low:
        return ("Redirect URI tidak cocok. Untuk login via browser pakai OAuth client tipe "
                "'Desktop app' (bukan 'Web application').")
    if "device_flow_disabled" in low or "device" in low and "not supported" in low:
        return ("Tipe OAuth client ini tidak mendukung login dengan kode. Buat client tipe "
                "'TVs and Limited Input devices', atau pakai 'Login via browser'.")
    return msg[:400]

# --------------------------------------------------------------------------- #
# device flow
# --------------------------------------------------------------------------- #
_pending: dict[str, Any] = {"state": "idle", "message": "", "user_code": "", "url": "",
                            "expires_at": 0.0, "channel": "", "help": None}
_pending_lock = threading.Lock()


def device_status() -> dict[str, Any]:
    with _pending_lock:
        state = dict(_pending)
    token = load_token() or {}
    obtained = float(token.get("obtained_at") or 0.0)
    state["token_age_days"] = round((time.time() - obtained) / 86400, 1) if obtained else None
    state["testing_mode_warning"] = bool(
        state.get("connected") and obtained and (time.time() - obtained) > 5 * 86400
    )
    state["connected"] = is_connected()
    if state["connected"] and state["state"] != "connected":
        token = load_token() or {}
        state["state"] = "connected"
        state["channel"] = state.get("channel") or token.get("channel", "")
        state["message"] = "Terhubung ke YouTube"
    return state


def _device_poll_once(client_id: str, client_secret: str, device_code: str) -> tuple[bool, float]:
    """Satu kali cek status device flow.

    Mengembalikan (selesai?, interval_baru). Kalau selesai, token sudah disimpan.
    """
    payload = {
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    if client_secret:
        payload["client_secret"] = client_secret
    result = _post_form(TOKEN_URL, payload)
    if "access_token" in result:
        save_token({
            "token": result["access_token"],
            "refresh_token": result.get("refresh_token"),
            "scopes": SCOPES,
            "client_id": client_id,
            "client_secret": client_secret,
            "expires_at": time.time() + float(result.get("expires_in") or 3600),
            "obtained_at": time.time(),
        })
        title = ""
        try:
            title = channel_info().get("title", "")
        except Exception:
            pass
        if title:
            token = load_token() or {}
            token["channel"] = title
            save_token(token)
        with _pending_lock:
            _pending.update({"state": "connected", "channel": title,
                             "message": "Terhubung ke YouTube", "help": None})
        return True, 0.0
    error = result.get("error", "")
    if error == "authorization_pending":
        return False, 0.0
    if error == "slow_down":
        return False, 5.0
    raw_message = result.get("error_description") or error or "Gagal login"
    with _pending_lock:
        _pending.update({"state": "error",
                         "message": friendly_error(raw_message),
                         "help": help_for(f"{error} {raw_message}")})
    return True, 0.0


def check_device_once() -> dict[str, Any]:
    """Cek sekali sekarang (tombol 'Cek status login').

    Berguna kalau Google sudah bilang "Success! Device connected" tapi Clipper
    belum berubah — mis. jendela Clipper sempat ditutup atau jaringan sempat putus.
    """
    with _pending_lock:
        state = dict(_pending)
    if state.get("state") == "waiting" and state.get("device_code"):
        client_id = state.get("client_id") or client_credentials()[0]
        client_secret = client_credentials()[1]
        try:
            _device_poll_once(client_id, client_secret, state["device_code"])
        except Exception as exc:  # noqa: BLE001
            return {"state": "error", "message": friendly_error(str(exc)),
                    "help": help_for(str(exc)), "connected": is_connected()}
    return device_status()


def start_device_auth() -> dict[str, Any]:
    client_id, _ = client_credentials()
    if not client_id:
        raise YouTubeError(
            "Client ID YouTube belum diisi. Buka console.cloud.google.com → APIs & Services → "
            "Credentials → Create OAuth client ID → tipe 'TVs and Limited Input devices'."
        )
    if is_connected():
        return device_status()

    resp = _post_form(DEVICE_CODE_URL, {"client_id": client_id, "scope": " ".join(SCOPES)})
    if "device_code" not in resp:
        raw = f"{resp.get('error', '')} {resp.get('error_description', '')}".strip()
        raise YouTubeError(
            friendly_error(
                raw,
                "Gagal memulai login. Pastikan OAuth client bertipe 'TVs and Limited Input devices'.",
            )
        )
    interval = int(resp.get("interval") or 5)
    expire = int(resp.get("expires_in") or 1800)
    with _pending_lock:
        _pending.update({
            "state": "waiting",
            "message": "Buka URL lalu masukkan kode",
            "help": None,
            "user_code": resp.get("user_code", ""),
            "url": resp.get("verification_url") or "https://www.google.com/device",
            "expires_at": time.time() + expire,
        })

    device_code = resp["device_code"]
    client_secret = client_credentials()[1]
    with _pending_lock:
        # disimpan supaya user bisa "Cek status login" kalau thread polling mati
        _pending.update({"device_code": device_code, "client_id": client_id})

    def poll() -> None:
        deadline = time.time() + expire
        wait = interval
        network_errors = 0
        while time.time() < deadline:
            time.sleep(wait)
            try:
                done, wait_next = _device_poll_once(client_id, client_secret, device_code)
            except Exception as exc:  # noqa: BLE001 - jangan sampai thread mati diam-diam
                network_errors += 1
                if network_errors >= 5:
                    with _pending_lock:
                        _pending.update({
                            "state": "error",
                            "message": friendly_error(str(exc), "Gagal login ke YouTube"),
                            "help": help_for(str(exc)),
                        })
                    return
                with _pending_lock:
                    _pending.update({
                        "message": "Menunggu persetujuan di browser… (gangguan jaringan, "
                                   f"mencoba lagi: {network_errors}/5)"
                    })
                continue
            network_errors = 0
            if done:
                return
            if wait_next:
                # slow_down: interval hanya boleh naik, jangan turun
                wait = max(wait, wait_next)
        with _pending_lock:
            _pending.update({
                "state": "expired",
                "message": "Kode login kedaluwarsa. Klik 'Login dengan kode' lagi untuk minta kode baru.",
                "help": None,
            })

    threading.Thread(target=poll, daemon=True).start()
    return device_status()


def local_browser_auth() -> dict[str, Any]:
    """Alternatif: OAuth desktop app, buka browser di mesin server."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    except ImportError as exc:
        raise YouTubeError("Install dulu: pip install google-auth-oauthlib") from exc
    client_id, client_secret = client_credentials()
    if not client_id or not client_secret:
        raise YouTubeError("Metode ini butuh client id + client secret (tipe Desktop app)")
    config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URL,
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    try:
        # Tanpa timeout_seconds, run_local_server menunggu selamanya. Kalau Google
        # menampilkan "Access blocked" (app masih Testing & akun bukan test user),
        # browser tidak pernah redirect balik → login menggantung tanpa pesan.
        creds = flow.run_local_server(
            port=0, prompt="consent", open_browser=True, timeout_seconds=BROWSER_LOGIN_TIMEOUT
        )
    except Exception as exc:  # access_denied / invalid_client / port sibuk / timeout
        raw = str(exc)
        if not raw.strip() or "timed out" in raw.lower():
            raw = (
                "Timed out waiting for response from authorization server. "
                "Kalau di browser muncul 'Access blocked' / '403 access_denied', akun Google "
                "yang dipakai belum terdaftar sebagai test user di OAuth consent screen."
            )
        raise YouTubeError(friendly_error(raw, "Login lewat browser gagal")) from exc
    save_token({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "scopes": SCOPES,
        "client_id": client_id,
        "client_secret": client_secret,
        "expires_at": time.time() + 3600,
        "obtained_at": time.time(),
    })
    with _pending_lock:
        _pending.update({"state": "connected", "message": "Terhubung ke YouTube"})
    return device_status()


def channel_info() -> dict[str, Any]:
    if not is_connected():
        return {}
    yt = _youtube_client()
    resp = yt.channels().list(part="snippet,statistics", mine=True).execute()
    items = resp.get("items") or []
    if not items:
        return {}
    item = items[0]
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    info = {
        "id": item.get("id"),
        "title": snippet.get("title"),
        "avatar": (snippet.get("thumbnails") or {}).get("default", {}).get("url", ""),
        "subscribers": stats.get("subscriberCount"),
        "videos": stats.get("videoCount"),
        "views": stats.get("viewCount"),
    }
    with _pending_lock:
        _pending["channel"] = info.get("title", "")
    return info


# --------------------------------------------------------------------------- #
# upload
# --------------------------------------------------------------------------- #
def _clean_title(title: str, hashtags: str) -> str:
    title = (title or "Clip").strip() or "Clip"
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"
    return title[:100]


def _clean_description(description: str, hashtags: str) -> str:
    desc = (description or "").strip()
    tags = (hashtags or "").strip()
    if tags and "#shorts" not in desc.lower():
        desc = f"{desc}\n\n{tags}".strip()
    if "#shorts" not in desc.lower():
        desc = f"{desc}\n\n#Shorts".strip()
    return desc[:4900]


def upload_video(
    file_path: str | Path,
    title: str,
    description: str = "",
    hashtags: str = "",
    privacy: str = "public",
    on_progress: Callable[[float, str], None] | None = None,
    srt_path: str | Path | None = None,
    category_id: str = "24",
    notify_subscribers: bool = True,
) -> dict[str, Any]:
    """Upload satu file ke YouTube sebagai Short (video vertikal <= 3 menit)."""
    from googleapiclient.http import MediaFileUpload  # type: ignore

    file_path = Path(file_path)
    if not file_path.exists():
        raise YouTubeError("File clip belum ada — render dulu")
    if not is_connected():
        raise YouTubeError("Belum login YouTube. Hubungkan akun dulu di tab YouTube.")

    yt = _youtube_client()
    body = {
        "snippet": {
            "title": _clean_title(title, hashtags),
            "description": _clean_description(description, hashtags),
            "categoryId": str(category_id or "24"),
            "defaultLanguage": "id",
            "defaultAudioLanguage": "id",
        },
        "status": {
            "privacyStatus": privacy if privacy in ("public", "unlisted", "private") else "public",
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
        },
    }
    media_body = MediaFileUpload(str(file_path), chunksize=8 * 1024 * 1024, resumable=True,
                                 mimetype="video/mp4")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media_body,
                                 notifySubscribers=bool(notify_subscribers))
    response = None
    total = max(1, file_path.stat().st_size)
    attempt = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status and on_progress:
                on_progress(min(0.99, status.progress() or 0.0),
                            f"Upload {status.progress() * 100:.0f}% "
                            f"({status.resumable_progress / 1048576:.1f}/{total / 1048576:.1f} MB)")
        except Exception as exc:  # retry error jaringan
            attempt += 1
            if attempt > 5:
                raise YouTubeError(f"Upload gagal: {exc}") from exc
            if on_progress:
                on_progress(0.0, f"Ulangi upload ({attempt}/5)…")
            time.sleep(2 * attempt)

    video_id = response.get("id", "")
    if srt_path and video_id and Path(srt_path).exists():
        try:
            yt.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": video_id, "language": "id", "name": "Indonesia"}},
                media_body=MediaFileUpload(str(srt_path), mimetype="application/octet-stream"),
            ).execute()
        except Exception:
            pass
    if on_progress:
        on_progress(1.0, "Upload selesai")
    return {
        "id": video_id,
        "url": f"https://youtube.com/shorts/{video_id}" if video_id else "",
        "watch_url": f"https://youtu.be/{video_id}" if video_id else "",
        "privacy": body["status"]["privacyStatus"],
    }
