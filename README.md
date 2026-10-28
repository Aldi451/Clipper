# Clipper

Auto-clipper ala **OpusClip** yang jalan **sepenuhnya di komputer/server kamu sendiri** —
tanpa langganan, tanpa API berbayar, tanpa upload video ke pihak ketiga.

Tempel link YouTube/TikTok/IG/X **atau** lampirkan file video → Clipper mentranskrip,
mencari momen paling menarik, merender Shorts 9:16 dengan caption karaoke + hook,
lalu **upload otomatis ke YouTube Shorts** channel kamu.

<p align="center"><em>Python + FastAPI + ffmpeg + faster-whisper + yt-dlp · UI dark mode tanpa CDN (100% offline)</em></p>

---

## Fitur

| | |
|---|---|
| 🔗 **Dari link** | YouTube, TikTok, Instagram, X/Twitter, Facebook, Vimeo, ratusan situs lain via `yt-dlp`. Bisa pakai cookies browser untuk video yang perlu login. |
| 📁 **Dari file** | Upload lewat browser (drag & drop, aman untuk file besar, streaming) atau tunjuk path lokal di server. |
| 🎙️ **Transkrip offline** | `faster-whisper` (tiny → large-v3, CPU atau CUDA). Model diunduh **sekali**, setelah itu 100% offline. Kalau video sudah punya subtitle, Clipper memakainya lebih dulu (instan, tanpa model). |
| 🧠 **Pemilihan momen** | Scoring lokal: kata pemicu hook (ID+EN), pertanyaan, angka, energi audio (RMS), kepadatan bicara, kesinambungan, dan panjang ideal. Tidak pakai API AI berbayar. |
| ✨ **Judul & hashtag** | Heuristik dari kalimat terbaik. Opsional: diperbagus LLM lokal (**Ollama**) kalau kamu punya. |
| 📱 **Render 9:16** | 1080×1920, dua gaya: **blur background** (video utuh) atau **crop penuh** dengan kontrol fokus horizontal. |
| 💬 **Caption karaoke** | Word-by-word highlight (3 kata/baris, bisa diatur), warna & font custom, plus overlay judul/hook di atas video. |
| 🔊 **Normalisasi audio** | Loudnorm -14 LUFS, standar YouTube. |
| ✂️ **Editor per clip** | Ubah judul/hook/deskripsi/hashtag, geser start, ubah durasi, atur fokus crop, matikan caption, preview langsung, render ulang. |
| ▶️ **Upload YouTube** | OAuth Google resmi (device flow — login pakai kode, tanpa buka port lokal), upload resumable, subtitle `.srt` ikut diunggah, `#Shorts` otomatis. |
| 🤖 **Full otomatis** | Opsi "render otomatis" + "upload otomatis": tempel link → tunggu → Shorts terbit. |
| 💾 **Data lokal** | Semua video, clip, transkrip, token disimpan di folder `data/` (bisa dihapus kapan saja). |

---

## Instalasi

Butuh **Python 3.10+**. ffmpeg **tidak perlu** di-install manual (diambil dari paket `imageio-ffmpeg`).

**Linux / macOS**

```bash
git clone <repo-ini> clipper && cd clipper
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

**Windows (PowerShell)**

```powershell
git clone <repo-ini> clipper; cd clipper
py -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Atau pakai skrip setup: `bash scripts/setup.sh` (Linux/macOS) / `scripts\setup.bat` (Windows).

Buka **http://localhost:8420** (browser terbuka otomatis). Server listen di `0.0.0.0`,
jadi bisa juga diakses dari HP di jaringan yang sama: `http://IP-komputer:8420`.

```bash
python run.py --port 9000          # ganti port
python run.py --no-browser         # jangan buka browser
CLIPPER_DATA_DIR=/mnt/disk/clip python run.py   # simpan video di disk lain
```

---

## Cara pakai (3 langkah)

1. **Buat Clip** → tempel link atau drop file. Atur jumlah clip, durasi min/maks, gaya video.
2. Clipper jalan sendiri: *download → baca metadata → ekstrak audio → transkrip/subtitle →
   cari momen → buat clip kandidat*. Progress muncul real-time.
3. Di halaman project: **Render** atau **Render semua**, cek preview, edit kalau perlu,
   lalu **Upload** / **Upload semua** ke YouTube Shorts.

Tips: buka **Pengaturan** dan aktifkan `Render otomatis` + `Upload otomatis` di tab YouTube
untuk alur sekali klik.

---

## Upload ke YouTube Shorts (sekali setup, gratis)

YouTube mewajibkan OAuth client milik kamu sendiri. Sekali setup, gratis, tanpa kartu kredit:

1. Buka [console.cloud.google.com](https://console.cloud.google.com/projectcreate) → buat project.
2. **APIs & Services → Library** → cari **YouTube Data API v3** → **Enable**.
3. **OAuth consent screen** → tipe **External** → isi nama app → tambahkan email kamu di
   **Test users** (supaya bisa login walau app belum diverifikasi).
4. **Credentials → Create credentials → OAuth client ID**:
   - pilih **TVs and Limited Input devices** (paling gampang, login pakai kode), atau
   - **Desktop app** kalau Clipper jalan di komputer yang sama dengan browser.
5. Salin **Client ID** (+ **Client secret**) ke tab **YouTube** di Clipper → **Simpan kredensial**
   → **Login dengan kode** → buka `google.com/device`, masukkan kode → izinkan.

Token disimpan di `data/youtube_token.json` dan di-refresh otomatis.
Klik **Putuskan** untuk menghapus akses.

Catatan kuota: YouTube memberi **10.000 unit/hari** gratis; satu upload ≈ 1600 unit
(≈ 6 upload/hari). Kalau kuota habis, upload berikutnya gagal sampai reset (tengah malam PT).

---

## Struktur project

```
Clipper/
├── run.py                  # launcher (uvicorn)
├── requirements.txt
├── app/
│   ├── server.py           # REST API + static UI (FastAPI)
│   ├── pipeline.py         # orkestrasi: analisis → render → upload (thread pool)
│   ├── ingest.py           # upload streaming & download via yt-dlp
│   ├── media.py            # ffmpeg/ffprobe helper, ekstraksi audio, energi RMS
│   ├── transcribe.py       # faster-whisper (lokal)
│   ├── subs.py             # parser VTT/SRT (fallback transkrip)
│   ├── analyze.py          # scoring momen + judul/hashtag + Ollama opsional
│   ├── captions.py         # generator ASS (caption karaoke & hook)
│   ├── render.py           # render 1080×1920 (blur/crop, caption, loudnorm)
│   ├── youtube.py          # OAuth device flow + upload resumable
│   ├── db.py               # SQLite (jobs & clips)
│   └── config.py           # path, settings, pencarian ffmpeg
├── web/                    # UI tanpa framework & tanpa CDN
│   ├── index.html  app.js  styles.css
├── data/                   # dibuat otomatis: uploads, clips, work, token, db
└── models/                 # cache model Whisper (offline setelah unduh pertama)
```

## Pengaturan penting

| Kunci | Arti |
|---|---|
| `whisper_model` | `tiny` (cepat) → `large-v3` (akurat). Default `small`. |
| `language` | `auto` / `id` / `en` / dll. |
| `prefer_whisper` | `true` = selalu transkripsi sendiri walau ada subtitle. |
| `target_clips`, `min_clip_seconds`, `max_clip_seconds` | Berapa & sepanjang apa clip. |
| `layout` | `fit` (blur bg) atau `crop` (penuh 9:16). |
| `crop_focus` | 0 = kiri … 1 = kanan (mode crop). |
| `captions`, `caption_words`, `caption_color`, `caption_highlight`, `caption_font`, `caption_uppercase` | Gaya caption. |
| `hook_overlay` | Tampilkan judul hook 5 detik pertama. |
| `fps`, `crf`, `normalize_audio` | Kualitas render. |
| `yt_privacy`, `yt_category`, `yt_hashtags`, `yt_notify_subscribers` | Default upload. |
| `auto_render`, `yt_auto_upload` | Otomatisasi penuh. |
| `llm_enabled`, `llm_url`, `llm_model` | Judul dari Ollama lokal (opsional). |
| `ytdlp_cookies_browser`, `ytdlp_proxy` | Untuk video yang perlu login / koneksi khusus. |

Pengaturan tersimpan di `data/settings.json` (bisa diedit manual), dan bisa juga lewat API
`PATCH /api/settings`. Dokumentasi API interaktif: **http://localhost:8420/api/docs**.

---

## Catatan offline & sumber daya

- **Semua pemrosesan lokal.** Tidak ada video yang dikirim ke server orang lain.
- **Internet dibutuhkan untuk**: download video dari link, unduh model Whisper (sekali),
  dan upload ke YouTube. Selain itu bisa full offline.
- **RAM/CPU**: model `small` cukup ~2 GB RAM dan jalan di 2 core (lebih lambat). Ada GPU CUDA?
  set `whisper_device=cuda` di Pengaturan → transkripsi bisa 10× lebih cepat.
- **Font caption**: pakai font yang ada di sistem (mis. `Arial`, `DejaVu Sans`, `Inter`).
  "Inter" gratis: <https://rsms.me/inter/>.
- **Waktu render**: 1 menit video ≈ 15–40 detik render di CPU 2 core (preset `veryfast`).

## Troubleshooting

| Gejala | Solusi |
|---|---|
| `ffmpeg tidak ditemukan` | `pip install imageio-ffmpeg`, atau install ffmpeg dan pastikan ada di `PATH`. |
| Transkrip dilewati, pesan "model Whisper belum terunduh" | Jalankan sekali dengan internet, atau unduh `Systran/faster-whisper-small` lalu set `CLIPPER_MODEL_DIR` ke folder model. |
| Download link gagal (TikTok/IG private) | Isi `ytdlp_cookies_browser` (mis. `chrome`) di Pengaturan. |
| Upload 403 `quotaExceeded` | Kuota harian YouTube habis (≈6 upload/hari). Coba besok. |
| Login kode gagal | Pastikan OAuth client bertipe **TVs and Limited Input devices** dan email kamu terdaftar di *Test users*. |
| Preview video tidak mau seek | Format sumber tidak di-encode ulang; render dulu clip-nya. |

## Lisensi

MIT — pakai, ubah, jual hasilnya bebas. Hormati hak cipta video sumber dan
[Ketentuan YouTube](https://www.youtube.com/t/terms) saat mengunggah ulang konten orang lain.
