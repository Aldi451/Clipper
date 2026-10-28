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
| 🔥 **Cari yang viral** | Tab **Viral**: trending YouTube per negara + pencarian "paling banyak ditonton" per kata kunci, diberi skor viral (views/jam, total views, engagement). Satu klik → jadi bahan clip. |
| 🔗 **Dari link** | YouTube, TikTok, Instagram, X/Twitter, Facebook, Vimeo, ratusan situs lain via `yt-dlp`. Bisa pakai cookies browser untuk video yang perlu login. |
| 📁 **Dari file** | Upload lewat browser (drag & drop, aman untuk file besar, streaming) atau tunjuk path lokal di server. |
| 🎙️ **Transkrip offline** | `faster-whisper` (tiny → large-v3, CPU atau CUDA). Model diunduh **sekali**, setelah itu 100% offline. Kalau video sudah punya subtitle, Clipper memakainya lebih dulu (instan, tanpa model). |
| 🧠 **Pemilihan momen** | Scoring lokal: kata pemicu hook (ID+EN), pertanyaan, angka, energi audio (RMS), kepadatan bicara, kesinambungan, dan panjang ideal. Tidak pakai API AI berbayar. |
| ✨ **Judul & hashtag** | Heuristik dari kalimat terbaik. Opsional: diperbagus LLM lokal (**Ollama**) kalau kamu punya. |
| 🎬 **Video editor** | Timeline multi-segmen (potong, gabung, atur kecepatan 0.5×–2×), teks overlay tak terbatas (posisi, ukuran, warna, garis, kotak, rotasi, animasi pop/fade), logo/watermark milik sendiri, fade in/out, preview realtime. |
| 🎨 **Template** | 6 template siap pakai (Podcast Blur, MrBeast, Neon TikTok, Gradient Studio, Clean Vlog, Cinema) + 6 gaya caption, sekali klik langsung berubah. |
| ✨ **Auto subtitle** | Generate caption word-by-word dari transkrip (tanpa Whisper ulang), geser timing ±0.2s, edit/sembunyikan per baris, export `.srt`. |
| 🚀 **Export sampai 4K** | 1080p / 1440p / **2160×3840 (4K)** dengan upscale lanczos + unsharp, CRF & FPS bisa diatur. **Tanpa watermark, tanpa batas** — watermark hanya kalau kamu unggah logo sendiri. |
| 💬 **Caption karaoke** | Word-by-word highlight (3 kata/baris, bisa diatur), warna & font custom, plus overlay judul/hook di atas video. |
| 🔊 **Normalisasi audio** | Loudnorm -14 LUFS, standar YouTube. |
| ✂️ **Editor per clip** | Ubah judul/hook/deskripsi/hashtag, geser start, ubah durasi, atur fokus crop, matikan caption, preview langsung, render ulang. |
| ▶️ **Upload YouTube** | OAuth Google resmi (device flow — login pakai kode, tanpa buka port lokal), upload resumable, subtitle `.srt` ikut diunggah, `#Shorts` otomatis. |
| 🤖 **Full otomatis** | Opsi "render otomatis" + "upload otomatis": tempel link → tunggu → Shorts terbit. |
| 💾 **Data lokal** | Semua video, clip, transkrip, token disimpan di folder `data/` (bisa dihapus kapan saja). |

---

## Instalasi & menjalankan (panduan singkat)

Butuh **Python 3.10+**. ffmpeg, yt-dlp, dan model Whisper **tidak perlu** dipasang manual —
ffmpeg diambil dari paket `imageio-ffmpeg`, model Whisper diunduh sekali saat pertama dipakai.

### Cara tercepat (disarankan)

**Windows**
1. Install Python dari <https://www.python.org/downloads/> — **centang "Add python.exe to PATH"**.
2. Double-click **`run.bat`** di folder Clipper.
3. Skrip otomatis: cek Python → buat `.venv` → install dependensi → start server → buka browser.

**macOS / Linux**
```bash
./run.sh
```
Sama: otomatis bikin virtualenv, install dependensi, lalu start.

Kalau berhasil, terminal menampilkan:

```
==============================================================
  Clipper siap
==============================================================
  Buka di browser : http://localhost:8420
  Data lokal      : /path/ke/Clipper/data
  Python          : /path/ke/Clipper/.venv/bin/python

  Biarkan jendela ini terbuka. Tekan Ctrl+C untuk berhenti.
==============================================================
```

Buka **http://localhost:8420** (biasanya browser terbuka sendiri). Server listen di `0.0.0.0`,
jadi dari HP di Wi-Fi yang sama bisa buka `http://IP-komputer:8420`.

### Cara manual

```bash
# Windows (PowerShell / cmd)
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Opsi launcher:

```bash
python run.py                 # start + buka browser
python run.py --no-browser    # jangan buka browser
python run.py --port 9000     # ganti port (otomatis pindah kalau bentrok)
python run.py --doctor        # cek lingkungan: paket, ffmpeg, port, folder data
python run.py --reload        # mode development
CLIPPER_DATA_DIR=D:\video-clipper python run.py   # simpan video di disk lain
```

### "Saya jalankan run.py tapi tidak terjadi apa-apa" — cek ini

| Gejala | Penyebab & solusi |
|---|---|
| **Double-click `run.py` → jendela hitam muncul lalu langsung hilang** | Itu karena skrip selesai/error dan jendela otomatis menutup. Jalankan lewat terminal: buka **cmd/PowerShell** di folder Clipper, lalu `python run.py` — pesan errornya akan kelihatan. Atau lebih mudah: double-click **`run.bat`** (sudah diatur berhenti menunggu Enter kalau error). |
| **`python` tidak dikenali / membuka Microsoft Store** | Python belum ada di PATH. Di Windows pakai `py -3 run.py`, atau install Python resmi lalu centang "Add python.exe to PATH". |
| **`ModuleNotFoundError: No module named 'uvicorn'` / `fastapi`** | Dependensi belum terpasang. Jalankan `pip install -r requirements.txt` (dalam venv yang aktif), atau cukup `run.bat` / `./run.sh`. Launcher sekarang menampilkan perintah persisnya. |
| **Terminal diam, tapi tidak ada halaman di browser** | Lihat baris **`Buka di browser : http://...`** — portnya mungkin **bukan** 8420 karena 8420 sedang dipakai. Clipper otomatis pindah ke 8421/8422 dst. Buka URL yang tertulis. |
| **`Port 8420 dipakai program lain`** | Clipper lain masih jalan. Tutup yang lama, atau biarkan Clipper memakai port alternatif yang ditampilkan. |
| **`ffmpeg tidak ditemukan`** | `pip install imageio-ffmpeg` (sudah masuk requirements). Kalau tetap gagal, install ffmpeg dan pastikan ada di `PATH`. |
| **Python terlalu lama (< 3.10)** | Install Python 3.11+ dari python.org, lalu buat ulang venv: hapus folder `.venv`, jalankan `run.bat` / `./run.sh` lagi. |
| **`folder data tidak bisa ditulis`** | Project ada di folder terproteksi (mis. `C:\Program Files`). Pindahkan ke `D:\Clipper` atau `Documents`, atau set `CLIPPER_DATA_DIR` ke folder lain. |
| **`[WinError 32] The process cannot access the file because it is being used by another process: ...\Temp\tmpXXXX.txt`** | Bug lama (sudah diperbaiki): file sementara untuk analisa audio tidak ditutup sebelum dipakai ffmpeg, jadi Windows menganggapnya terkunci. Sudah diatasi di versi ini — cukup klik **Coba lagi** pada project yang gagal. Kalau masih muncul: tutup pemutar video/preview browser yang memutar file clip, exclude folder Clipper dari antivirus, lalu coba lagi. |
| **`[WinError 5] Access is denied`** | Folder Clipper di lokasi terproteksi atau dijalankan tanpa izin tulis. Pindahkan ke `D:\Clipper` / `Documents`. |
| **Render gagal padahalsebelumnya bisa** | File clip lama sedang diputar di tab browser lain. Clipper sekarang merender ke file sementara lalu memindahkannya; cukup tutup preview lalu render ulang. |
| **Masih bingung** | Jalankan `python run.py --doctor`. Outputnya menyebut satu per satu apa yang OK dan apa yang kurang. |

Catatan: **jendela terminal harus tetap terbuka** selama Clipper dipakai — server berjalan di situ.
Menutup jendela = mematikan Clipper.

---

## Cara pakai (3 langkah)

1. **Buat Clip** → tempel link atau drop file. Atur jumlah clip, durasi min/maks, gaya video.
2. Clipper jalan sendiri: *download → baca metadata → ekstrak audio → transkrip/subtitle →
   cari momen → buat clip kandidat*. Progress muncul real-time.
3. Di halaman project: buka **✂ Editor** untuk memotong/menambah teks/caption, atau langsung
   **Render** / **Render semua**, cek preview, lalu **Upload** ke YouTube Shorts.

Tips: buka **Pengaturan** dan aktifkan `Render otomatis` + `Upload otomatis` di tab YouTube
untuk alur sekali klik (tempel link → Shorts terbit).

## Video editor (tab ✂ Editor)

Buka project → klik **✂ Editor** pada kartu clip. Semua perubahan tersimpan otomatis dan
langsung terlihat di preview (tanpa render).

| Bagian | Fungsi |
|---|---|
| **Preview** | Player dengan overlay caption + teks realtime. Tombol play, seek bar, `⇤`/`⇥` untuk menandai awal/akhir segmen di playhead. |
| **Timeline** | Segmen-segmen video sumber yang dipakai. Klik segmen → atur awal/akhir/kecepatan, **✂ Pecah di playhead**, tambah/hapus segmen. |
| **Teks** | Tambah teks overlay: isi, waktu mulai/selesai (bisa “= playhead”), posisi X/Y, ukuran, rotasi, warna, garis luar, kotak latar + transparansi, HURUF BESAR, animasi **pop**/**fade**. |
| **Caption** | Pilih gaya (Karaoke Kuning, MrBeast, TikTok Neon, Clean, Podcast Box, Cinema Green), kata per baris, ukuran, posisi, warna teks + highlight, ketebalan garis, highlight per kata. Tombol **✨ Auto subtitle** membuat/sinkron ulang caption dari transkrip, `±0.2s` untuk geser timing, klik baris untuk edit, ikon ◉/◌ untuk sembunyikan, **Unduh .srt**. |
| **Layout & Logo** | Layout `fit` (video utuh), `crop` (penuh 9:16 + fokus horizontal), `black` (letterbox). Background: blur video, gradien (2 warna), atau warna solid. Unggah logo PNG/JPG sendiri (posisi, ukuran, opacity). |
| **Export & 4K** | Resolusi 1080p/2K/4K, FPS 24–60, CRF, sharpen saat upscale, normalisasi audio, fade in/out, edit deskripsi/hashtag/hook untuk YouTube, lalu **Render** (progress muncul di preview) atau **Upload ke Shorts**. |

Template sekali klik (dropdown di kanan atas editor) mengubah layout + gaya caption sekaligus.
Template default dan resolusi export bisa diatur di **Pengaturan**.

### Soal 4K (jujur ya)
Upscale 4K memakai **lanczos + unsharp** di ffmpeg: hasilnya lebih tajam dan memenuhi syarat
“video 4K” di platform, tapi **detail baru tidak muncul** — sumber 1080p tetap 1080p.
Kalau sumbernya 4K (atau pakai `layout: crop` dari video 4K), hasilnya benar-benar 4K asli.
Render 4K kira-kira 3–6× lebih lama daripada 1080p di CPU.

## Cari video viral (buat bahan clip / re-upload)

Buka tab **🔥 Viral**. Ada dua cara:

1. **Trending sekarang** — ambil daftar trending YouTube untuk negara terpilih.
2. **Cari kata kunci** — mis. `podcast indonesia, motivasi hidup, ceramah agama`
   (urut "paling banyak ditonton" dalam rentang waktu yang dipilih).

Hasilnya diberi **skor viral 0–100** = kecepatan views/jam (50%) + total views (32%) +
engagement like/komentar (18%). Filter yang tersedia: negara, rentang waktu (7 hari–1 tahun),
minimal views, durasi (video panjang / sedang / Shorts), dan kategori.

Klik **Buat clip** pada kartu → video otomatis masuk antrean project (unduh → transkrip →
cari momen → render → upload). Bisa juga centang beberapa video lalu **Buat clip terpilih**.

**Dua backend** (otomatis dipilih):

| Backend | Kapan dipakai | Catatan |
|---|---|---|
| YouTube Data API v3 | Kalau akun YouTube sudah login **atau** kamu mengisi API key gratis di Pengaturan | Paling akurat (views, like, komentar, tanggal). Trending = 1 unit kuota, pencarian keyword ≈ 101 unit. |
| yt-dlp | Kalau API tidak tersedia / dimatikan | Tanpa login & tanpa kuota; data views dilengkapi bertahap otomatis. |

> ⚠️ **Hak cipta:** mengunggah ulang video orang lain apa adanya bisa melanggar hak cipta
> dan berisiko klaim/strike di channel kamu. Pakai clip sebagai bahan komentar, reaksi,
> atau potongan transformatif, dan sebutkan sumbernya di deskripsi.

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
│   ├── editor.py           # engine video editor: spec JSON → render (timeline, teks, subtitle, template, 4K)
│   ├── discover.py        # pencarian video viral (YouTube API + yt-dlp) & skor viral
│   ├── youtube.py          # OAuth device flow + upload resumable
│   ├── db.py               # SQLite (jobs & clips)
│   └── config.py           # path, settings, pencarian ffmpeg
├── web/                    # UI tanpa framework & tanpa CDN
│   ├── index.html  app.js  editor.js  styles.css
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
| `export_height`, `export_sharpen`, `default_template` | Default export editor: 1080p/2K/4K, pertajam saat upscale, template awal. |
| `yt_privacy`, `yt_category`, `yt_hashtags`, `yt_notify_subscribers` | Default upload. |
| `auto_render`, `yt_auto_upload` | Otomatisasi penuh. |
| `llm_enabled`, `llm_url`, `llm_model` | Judul dari Ollama lokal (opsional). |
| `ytdlp_cookies_browser`, `ytdlp_proxy` | Untuk video yang perlu login / koneksi khusus. |
| `discover_region`, `discover_days` | Default region & rentang waktu pencarian viral. |
| `discover_min_views`, `discover_min_duration`, `discover_max_duration`, `discover_limit` | Filter default hasil viral (`120–180` detik cocok untuk cari Shorts). |
| `discover_queries` | Kata kunci default (dipisah koma) saat tab Viral dibuka. |
| `discover_use_api`, `discover_api_key` | Pakai YouTube API resmi & API key gratis (opsional, tanpa login). |

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
| Tab Viral: `Tidak ada hasil dari YouTube` | Server tanpa akses internet, atau YouTube minta verifikasi → isi `ytdlp_cookies_browser`, atau pakai API key. |
| Tab Viral kosong padahal internet lancar | Filter terlalu ketat (minimal views/durasi). Turunkan minimal views atau pilih "Semua durasi". |
| Caption tampil tapi font aneh / kotak-kotak | Font tidak ada di sistem. Install font tersebut (mis. Inter) atau ganti di Pengaturan → Font caption. |
| Render 4K lama / laptop panas | Wajar: 2160×3840 ≈ 4× piksel 1080p. Turunkan ke 1080p, atau turunkan FPS/pakai durasi lebih pendek. |
| Perubahan di editor tidak muncul di video | Klik **Render** — editor menyimpan “spec”, video baru dihasilkan saat render. |

## API editor

| Endpoint | Fungsi |
|---|---|
| `GET /api/editor/meta` | daftar template, gaya caption, resolusi, font |
| `GET /api/clips/{id}/editor` | state editor: clip, spec, ringkasan, info sumber |
| `PUT /api/clips/{id}/spec` | simpan spec (dinormalisasi & dibatasi di server) |
| `POST /api/clips/{id}/spec/template` | terapkan template ke spec |
| `POST /api/clips/{id}/subtitles` | `regenerate` / `shift` / `clear` subtitle otomatis |
| `POST /api/clips/{id}/assets` | unggah logo/gambar overlay |

## Lisensi

MIT — pakai, ubah, jual hasilnya bebas. Hormati hak cipta video sumber dan
[Ketentuan YouTube](https://www.youtube.com/t/terms) saat mengunggah ulang konten orang lain.
