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
| ♪ **Upload TikTok** | Upload clip ke TikTok langsung dari Clipper — memakai browser di komputer sendiri (gratis, tanpa API key, tanpa daftar aplikasi). Caption + hashtag terisi otomatis, tombol Post ditekan otomatis (bisa dimatikan). |
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
python run.py --doctor        # cek lingkungan: paket, ffmpeg, port, folder data, path DB
python run.py --data-dir D:\Clipper\data   # pakai folder data lain
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

### Video dibatasi umur / "Sign in to confirm your age"

YouTube menolak unduhan kalau videonya **age-restricted**, private, members-only, atau
menganggap permintaan ini bot. Ini bukan bug Clipper — server YouTube memang butuh sesi login.

**Cara paling andal: pasang cookies (sekali saja)**

1. Login YouTube di browser (akun yang berusia 18+ kalau video memang dibatasi umur).
2. Pasang ekstensi **“Get cookies.txt LOCALLY”** (Chrome/Firefox), buka `youtube.com`,
   lalu **Export** → dapat file `cookies.txt`.
3. Di Clipper: **Pengaturan → Cookies untuk video terbatas usia** → **Unggah cookies.txt**
   (bisa juga **Tempel** isi file, atau tempel JSON dari ekstensi Cookie-Editor).
4. Ulangi project yang gagal → klik **Coba lagi**.

Cookies disimpan lokal di `data/cookies.txt` (permission 600) dan **tidak dikirim ke mana pun**
selain ke YouTube saat mengunduh. Hapus kapan saja lewat tombol **Hapus**.

**Format file yang diterima**

| Format | Contoh nama file | Catatan |
|---|---|---|
| Netscape `cookies.txt` | `www.youtube.com_cookies.txt`, `cookies.txt` | Hasil “Get cookies.txt LOCALLY”. Nama file bebas — yang penting isinya. |
| JSON ekspor ekstensi | `cookies.json` | Cookie-Editor → Export → JSON. |
| Tempel teks | – | Tombol **Tempel** → paste isi file. |

Baris `#HttpOnly_.youtube.com …` **dihitung** sebagai cookie (bukan komentar) — justru di situ
cookie login `SID`/`HSID`/`SSID` berada. BOM dan akhir baris Windows (CRLF) juga ditangani.

**Kalau muncul “You have requested merging of multiple formats but ffmpeg is not installed”**

yt-dlp butuh ffmpeg untuk menggabungkan video+audio. Clipper memakai ffmpeg dari paket
`imageio-ffmpeg`, yang **tidak selalu ada di PATH** — karena itu Clipper sekarang mengirim
lokasi ffmpeg langsung ke yt-dlp (`ffmpeg_location`), jadi tidak perlu install ffmpeg terpisah.
Cek di Pengaturan → Diagnostik baris `yt-dlp → ffmpeg` (atau jalankan `python run.py --doctor`).

Kalau tetap muncul:

```bash
pip install -U imageio-ffmpeg         # Clipper memakai ffmpeg dari paket ini
python run.py --doctor                # pastikan baris "yt-dlp → ffmpeg" OK
```

Sebagai jaring pengaman, kalau ffmpeg benar-benar tidak ada Clipper otomatis beralih ke
**format tunggal** (`b`) yang tidak perlu penggabungan, jadi unduhan tetap jalan walau
kualitasnya mengikuti file gabungan yang disediakan YouTube.

**Kalau unduhan berhenti di tengah (mis. 3%) dengan “The page needs to be reloaded”**

YouTube membagikan halaman/player yang sudah kedaluwarsa, sehingga unduhan terputus beberapa
detik setelah mulai. Clipper sekarang:

1. **mengulang otomatis** — percobaan kedua memakai instance yt-dlp baru dan membuang file
   parsial (`.part`) sisa unduhan sebelumnya,
2. **berganti client** (web → TV) kalau percobaan pertama masih gagal,
3. menampilkan pesan yang menjelaskan langkah manual kalau semuanya gagal.

Kalau masih berulang, biasanya **yt-dlp kamu sudah tua** (YouTube sering mengubah halamannya):

```bash
pip install -U yt-dlp          # macOS/Linux
.venv\Scripts\python -m pip install -U yt-dlp   # Windows (venv project)
```

Pengaturan → Diagnostik sekarang menunjukkan umur rilis yt-dlp dan menampilkan peringatan kalau
lebih dari 45 hari. Cara lain: pasang cookies (Pengaturan → Cookies) atau ganti **Client YouTube → TV**.

**Kalau unduhan gagal karena format**

`Requested format is not available` biasanya berarti video itu tidak punya stream
video+audio terpisah (video lama, Shorts, atau format dibatasi). Clipper sekarang mencoba
rantai format otomatis:

```
video+audio (mp4/m4a) → video+audio (ekstensi apa pun) → format gabungan → apa pun
```

Kalau masih gagal, set **Pengaturan → Format unduhan → Best** lalu ulangi. Kalau videonya
butuh login, lihat bagian *batas umur* di atas (pasang cookies dulu — daftar format ikut berubah).

**Kalau unggahan gagal**

| Gejala | Penyebab / solusi |
|---|---|
| Tombol unggah tidak membuka apa pun | Klik kanan file → pastikan ekstensinya `.txt`/`.json` (bukan `.txt.txt`). Atau pakai **Tempel** isi filenya. |
| “Format cookies tidak dikenali” | File bukan cookies.txt/JSON — ekspor ulang dari halaman `youtube.com`. |
| “tidak ada cookie login (SID/HSID/SSID)” | Kamu mengekspor sebelum login. Login dulu di YouTube, baru ekspor. |
| Cookie terpasang tapi unduhan masih gagal | Cookies kedaluwarsa (biasanya beberapa minggu) → ekspor ulang. |

**Alternatif lain**

| Cara | Catatan |
|---|---|
| **Cookies browser** | Di Pengaturan pilih Chrome/Firefox/Edge/Brave/Safari. Clipper membaca cookies langsung dari profil browser — **tutup browsernya dulu** (terutama di Windows) supaya file cookies bisa dibaca. |
| **Client YouTube → TV** | Clipper otomatis mencoba client `tv` setelah client web kalau videonya diblokir umur (`Client YouTube: Auto`). Memilih **TV** membuat semua unduhan memakai client itu. |
| **Unduh manual** | Kalau semua gagal: unduh videonya sendiri, lalu di tab **Buat Clip** pilih **Dari file** (drag & drop). Semua fitur editor tetap jalan. |

Pesan error di Clipper sekarang menyebutkan penyebabnya secara spesifik (batas umur, bot,
private, geo-restricted, jaringan) beserta langkah perbaikannya, dan halaman project menampilkan
tombol **“Atur cookies / unduhan”** saat error-nya berkaitan dengan login.

### "Hasil klip cuma suara, gambarnya tidak ada"

Kalau klip yang kamu unduh isinya audio saja (atau layar hitam), penyebabnya hampir selalu **sumbernya**,
bukan renderer-nya:

| Penyebab | Ciri | Solusi |
|---|---|---|
| Video sumber hanya audio | `ffprobe` tidak punya stream `Video:` (mis. hasil unduhan `.m4a`, podcast, atau format fallback yt-dlp) | Clipper versi ini **otomatis membuat video** dari background gradien + caption. Mau gambar bergerak? pakai video asli. |
| Video hanya berisi 1 gambar (cover art) | Stream video ada tapi `attached_pic` / 1 frame (musik + cover album) | Klip dirender dengan gambar statis sebagai latar. |
| Unduhan dibatasi usia / perlu login | yt-dlp hanya dapat audio | Atur **Pengaturan → Ambil video dari link → Cookies browser** ke browser yang sudah login, lalu ulangi. |
| Video habis di tengah | Video 10 detik, audio 20 detik; segmen di menit 00:12 | Segmen fallback memakai frame terakhir + audio, jadi klip tetap punya gambar. |

Editor sekarang menampilkan **banner peringatan** di atas timeline kalau sumbernya audio-only/gambar
statis, dan render **menolak menyimpan hasil tanpa track video** (error jelas, bukan file cacat).
Format unduhan bisa diatur di **Pengaturan → Format unduhan** (`Video maks 1080p` / `720p` /
`Best`); semua pilihan selalu mengunduh track video + audio.

### Soal 4K (jujur ya)
Upscale 4K memakai **lanczos + unsharp** di ffmpeg: hasilnya lebih tajam dan memenuhi syarat
“video 4K” di platform, tapi **detail baru tidak muncul** — sumber 1080p tetap 1080p.
Kalau sumbernya 4K (atau pakai `layout: crop` dari video 4K), hasilnya benar-benar 4K asli.
Render 4K kira-kira 3–6× lebih lama daripada 1080p di CPU.

### YouTube: `Error 403: access_denied` / "has not completed the Google verification process"

Pesan ini datang dari **Google**, bukan dari Clipper:

> Access blocked: **Tester clipper** has not completed the Google verification process. The app is
> currently being tested, and can only be accessed by developer-approved testers. Error 403: access_denied

Artinya OAuth app yang kamu buat masih berstatus **Testing**, dan akun Google yang sedang login
**belum didaftarkan sebagai test user**. Selama statusnya Testing, Google hanya mengizinkan akun
yang ada di daftar test user — Clipper tidak bisa melewati ini.

**Perbaikan (2 menit, sekali saja):**

1. Buka <https://console.cloud.google.com/apis/credentials/consent> — pilih project yang sama
   dengan OAuth client kamu.
2. Pastikan **Publishing status = Testing** dan **User type = External**.
3. Di bagian **Test users**, klik **+ ADD USERS**, masukkan **email Google yang dipakai untuk
   channel YouTube** (harus sama persis, termasuk kalau ada `+` alias).
4. **Save**, tunggu ±1 menit, lalu login ulang di tab YouTube. Kalau browser masih login dengan
   akun Google lain, pakai **Incognito** atau logout akun lain dulu.

Setelah akun terdaftar, Google akan menampilkan layar *"Google hasn't verified this app"* →
klik **Advanced → Go to … (unsafe)**, lalu **Allow**. Layar itu normal untuk app pribadi yang
belum diverifikasi.

**Kalau Google bilang “Success! Device connected” tapi Clipper masih “belum terhubung”**

Ini bug yang sudah diperbaiki di v1.0.10. Penyebabnya: endpoint token Google membalas
**HTTP 400** `{"error": "authorization_pending"}` selama kamu belum menekan izinkan, dan
`HTTPError` (subclass `URLError`) tertangkap oleh blok “tidak bisa menghubungi Google” — thread
polling mati diam-diam di percobaan pertama, jadi status tidak pernah berubah.

Kalau masih mengalaminya: klik **“Sudah disetujui di browser? Cek sekarang”** (memanggil
`POST /api/youtube/login/device/check`) untuk memeriksa sekali lagi tanpa mengulang dari awal,
atau ulangi **Login dengan kode**.

**Notifikasi “YouTube terhubung 🎉” muncul berulang di pojok kanan bawah**

Bug yang diperbaiki di v1.0.11. `renderYouTube()` memanggil `pollDeviceStatus()` saat status
sudah **connected**, lalu `pollDeviceStatus()` memanggil `renderYouTube()` lagi → loop tanpa
henti, satu toast per putaran. Sekarang:

- polling hanya jalan saat status **waiting** (tidak ada yang perlu ditanya kalau sudah terhubung),
- toast “terhubung” hanya muncul **sekali** per sesi login,
- hanya **satu** loop polling bisa aktif (panggilan baru membatalkan loop lama),
- toast kembar (pesan sama dalam 1,2 detik) dibuang, dan maksimal **3** notifikasi tampil bersamaan.

**Catatan penting:**

| Hal | Penjelasan |
|---|---|
| Token mati tiap 7 hari | Selama app berstatus **Testing**, Google mematikan refresh token setelah 7 hari. Cukup klik **Login dengan kode** lagi. |
| Mau tanpa login ulang? | Klik **Publish app** di OAuth consent screen. Untuk dipakai sendiri, verifikasi tidak wajib — cukup lewati layar peringatan. Verifikasi (bisa berhari-hari) hanya perlu kalau app dipakai banyak orang. |
| Batas 100 test user | Status Testing maksimal 100 akun test user. |
| Akun owner bukan otomatis tester | Akun yang membuat project Google Cloud **tetap** harus ditambahkan sendiri ke daftar *Test users*, kalau tidak akan kena blokir yang sama. |
| Email harus sama persis | Yang login di browser harus akun yang didaftarkan. Kalau ada beberapa akun Google aktif, pakai Incognito. |
| Login browser menggantung | Sejak v1.0.9, login via browser menunggu maksimal **5 menit** lalu berhenti dengan pesan jelas (dulu bisa menunggu selamanya). Halaman *Access blocked* tidak pernah mengirim balik hasilnya ke Clipper. |
| `invalid_client` | Tipe OAuth client salah. Login pakai **kode** → tipe *TVs and Limited Input devices*; login via **browser** → tipe *Desktop app*. |
| `invalid_grant` | Token sudah kedaluwarsa/dicabut → login ulang. |
| API belum aktif | Kalau upload gagal `accessNotConfigured`, aktifkan **YouTube Data API v3** di Library. |

Clipper versi ini menampilkan panduan di atas langsung di tab **YouTube** (blok
*"Muncul Error 403: access_denied?"*) dan menerjemahkan error OAuth mentah jadi langkah perbaikan
di kotak login.

## Folder Clipper dipindah (mis. `C:` → `D:`) — Clipper blank / tidak bisa dibuka

Ini normal dan **bukan data yang hilang**. Penyebabnya: `.venv` menyimpan path absolut, jadi
setelah folder dipindah:

1. `python.exe` di `.venv` masih ada tapi paket-paketnya tidak bisa di-import → server gagal
   start, jendela langsung tertutup atau halaman kosong;
2. Windows service / autostart masih menunjuk `C:\...` lama → `localhost:8420` tidak menjawab.

**Perbaikan satu kali klik:**

```bat
repair.bat
```

Yang dikerjakan `repair.bat`:

| Langkah | Isi |
|---|---|
| 1 | memeriksa file penting (`run.py`, `app/`, `web/`) — kalau hilang, folder belum tersalin lengkap |
| 2 | menghentikan Clipper/service yang masih jalan |
| 3 | mendeteksi `.venv` rusak → menghapus dan **membuat ulang** + memasang dependensi |
| 4 | menjalankan `python run.py --repair` → mengarahkan **Windows service** & **autostart** ke folder baru (tipe startup dipertahankan) |
| 5 | **menulis ulang path lama di database** (`jobs.source_path`, `clips.file`, `clips.thumb`) ke folder data sekarang |
| 6 | `--doctor` untuk verifikasi akhir |

**Data aman:** `data/` (video, klip, cookies, token YouTube, DB) tidak dihapus.

### Aplikasi terbuka tapi project/klip lama kosong ("file tidak ditemukan")

Kalau folder sudah dipindah **dan isinya ikut tersalin**, tapi daftar project kosong, thumbnail
hilang, atau video tidak mau diputar, penyebabnya **path absolut di database**: `data/clipper.db`
menyimpan lokasi file (`C:\Clipper\data\clips\clip_x.mp4`). Setelah pindah ke `D:`, path itu
tidak ada lagi — walau file-nya ada di `D:\Clipper\data\clips\`.

Clipper sekarang menangani ini otomatis:

- saat memutar/membuka clip, file dicari berdasarkan **nama file** di folder data yang aktif
  (`clips/`, `uploads/`, `work/`, `assets/`) → klip lama tetap bisa diputar tanpa analisis ulang;
- `repair.bat` **menulis ulang path lama** di database ke folder baru (berlaku permanen);
- di **Pengaturan → Folder data** ada lokasi data aktif, jumlah file yang ketemu, dan tombol
  **Perbaiki path** (bisa diklik kapan saja tanpa menutup aplikasi).

Cek manual kalau masih kosong:

```bat
.venv\Scripts\python run.py --doctor
```

Bagian `folder data:` menunjukkan folder yang dipakai, dan baris berikutnya menunjukkan berapa path
di database yang masih menunjuk lokasi lama. Kalau angka kedua muncul, jalankan `repair.bat`.

Kalau folder data sengaja ditaruh di luar folder Clipper:

```bat
run.bat --data-dir D:\Clipper\data
```

atau set permanen `CLIPPER_DATA_DIR=D:\Clipper\data` (System Properties → Environment Variables).

Alternatif per langkah:

```bat
.venv\Scripts\python run.py --doctor     :: lihat apa yang rusak
.venv\Scripts\python run.py --repair     :: perbaiki venv + service + autostart
```

Kalau memakai `ClipperService.exe`, buka aplikasinya — status akan menampilkan
**“PERLU DIPERBAIKI: service masih menunjuk folder lama”** dan ada tombol **Perbaiki**.

Kalau `repair.bat` bilang butuh Administrator, klik kanan → **Run as administrator** (perlu untuk
memasang ulang service).

`run.bat` sekarang juga mendeteksi `.venv` rusak otomatis dan membuat ulang, jadi untuk pemakaian
manual cukup `run.bat` saja.

## Jalankan sebagai Windows service (tanpa jendela Command Prompt)

Kalau tidak mau ada jendela cmd yang harus dibiarkan terbuka, jalankan Clipper sebagai
**Windows service**: hidup di latar belakang, start otomatis saat Windows menyala, dan muncul
di `services.msc` sebagai **Clipper**.

### Versi .exe — install & atur sendiri (manual / auto / disable)

Kalau lebih suka klik-klik daripada menu cmd, buat **`ClipperService.exe`** sekali lalu pakai
selamanya:

```bat
build_service_exe.bat
```

Hasilnya `ClipperService.exe` di folder Clipper. **Double-click file itu** → muncul jendela kecil
berisi:

| Kontrol | Fungsi |
|---|---|
| Status | terpasang / berjalan + tipe startup sekarang |
| **Tipe startup** | **Automatic** (nyala saat Windows boot) · **Manual** (start sendiri) · **Disabled** (dimatikan total) |
| **Pasang / Update service** | daftarkan service memakai tipe startup yang dipilih |
| **Start / Stop** | nyalakan atau hentikan sekarang |
| **Terapkan tipe startup** | ubah auto ⇄ manual ⇄ disabled tanpa pasang ulang |
| **Hapus service** | bersihkan dari `services.msc` |
| **Buka Clipper** · **Lihat log** | buka aplikasi di browser · cek log service |
| Pilih folder… | kalau .exe ditaruh di luar folder Clipper |

Aksi yang butuh izin akan menawarkan **Run as administrator** otomatis (UAC sekali klik).

Bisa juga lewat terminal tanpa GUI:

```bat
ClipperService.exe --status
ClipperService.exe --install --start-type manual --port 8420
ClipperService.exe --start-type auto
ClipperService.exe --start
ClipperService.exe --stop
ClipperService.exe --remove
ClipperService.exe --log
```

**Penting — .exe ini hanya *manajer*.** Service-nya tetap dijalankan oleh
`.venv\Scripts\python.exe run.py --run-service`, jadi:

- `git pull` **langsung berlaku** tanpa perlu build ulang .exe,
- ukuran .exe kecil (± 10 MB) dan tidak menyimpan kode aplikasi,
- yang perlu ada di folder Clipper: `.venv` (dari `run.bat`) + `run.py`.

Kalau `pywin32`/PyInstaller belum ada, `build_service_exe.bat` memasangnya otomatis (butuh
internet sekali).

Jalankan juga langsung tanpa build: `python service_manager.py` (GUI yang sama).

**Cara tercepat:**

1. Double-click **`service.bat`** (minta izin Administrator sekali).
2. Pilih **1 — Pasang service**.

Selesai. Buka `http://localhost:8420` di browser kapan saja, tanpa cmd.

**Menu `service.bat`:**

| Pilihan | Fungsi |
|---|---|
| 1 | Pasang service (auto-start saat Windows nyala) |
| 2 / 3 | Mulai / hentikan service |
| 4 | Status (lihat juga `sc query Clipper`) |
| 5 | Hapus service |
| 6 | Buka Clipper di browser |
| 7 | Lihat 40 baris terakhir log service |
| 8 | Buat shortcut **Clipper** di Desktop |
| 9 | Alternatif **tanpa Administrator**: autostart lewat Task Scheduler |

Bisa juga tanpa menu: `service.bat install`, `service.bat status`, `service.bat log`, dst.

**Lewat terminal** (PowerShell/CMD **as Administrator**):

```bat
.venv\Scripts\python run.py --install-service      :: pasang + auto-start
.venv\Scripts\python run.py --start-service        :: mulai sekarang
.venv\Scripts\python run.py --service-status        :: cek status
.venv\Scripts\python run.py --service-log           :: lihat log
.venv\Scripts\python run.py --stop-service          :: hentikan
.venv\Scripts\python run.py --remove-service        :: hapus
.venv\Scripts\python run.py --shortcut              :: shortcut Desktop
```

**Yang perlu diketahui:**

| Hal | Penjelasan |
|---|---|
| Butuh `pywin32` | Terpasang otomatis oleh `service.bat` (`pip install pywin32`). Sudah ditambahkan ke `requirements.txt` untuk Windows. |
| Port & host | Disimpan di `data/service.json` (default `127.0.0.1:8420`), dipakai ulang setiap service start. Ubah dengan `--service-port 9000 --install-service`. |
| Log | `data/logs/clipper-service.log` (rotasi 2 MB × 3). Service tidak punya jendela, jadi error tercatat di sini. |
| Restart otomatis | Service dikonfigurasi `sc failure` → Windows menjalankannya ulang kalau berhenti karena error. |
| Tanpa Administrator | Pilih menu **9** (Task Scheduler + `pythonw.exe`) — jalan otomatis saat login, tetap tanpa jendela cmd. |
| Kembali ke mode manual | `service.bat remove`, lalu pakai `run.bat` seperti biasa. |
| Tidak bisa pasang service? | Pastikan `pywin32` terpasang dan jendela dijalankan sebagai Administrator. Pesan errornya sudah menjelaskan langkah perbaikannya. |

> Upah: kalau `service.bat` dipakai saat Clipper sedang jalan lewat `run.bat`, hentikan dulu
> jendela itu (`Ctrl+C`) supaya port 8420 tidak bentrok.

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

## Upload ke TikTok (gratis, tanpa API)

TikTok tidak memberi API upload gratis untuk perorangan (butuh aplikasi developer + review).
Clipper memakai cara yang tetap gratis dan tidak butuh persetujuan siapa pun: **mengemudikan
browser di komputer kamu sendiri**.

**Setup sekali:**

1. Buka tab **TikTok** di Clipper → **Pasang dukungan TikTok**
   (memasang paket `playwright` ± 1 MB; butuh internet sekali).
2. Clipper otomatis memakai **Chrome / Edge** yang sudah terpasang di Windows. Kalau tidak ada,
   klik **Unduh browser Clipper** (± 130 MB).
3. Klik **Buka TikTok & login** → jendela browser terbuka → login (email/kode atau scan QR).
   Cookie disimpan lokal di `data/tiktok_profile/`, jadi cukup sekali. Hapus dengan **Hapus sesi**.

**Upload:**

- Di halaman **Project**, setiap clip punya tombol **TikTok** (atau **Upload TikTok** untuk
  semua clip sekaligus).
- Clipper membuka TikTok Studio, memasukkan file, mengisi caption (judul + hashtag), memilih
  visibilitas, lalu menekan **Post**.
- Link video muncul di kartu clip begitu TikTok selesai memproses.

**Pengaturan** (tab TikTok):

| Setting | Arti |
|---|---|
| Visibilitas | Semua orang / Teman / Hanya saya (best effort — mengikuti pilihan yang tersedia di TikTok) |
| Hashtag default | Ditambahkan otomatis ke caption (default `#fyp #foryou #viral #ShortsIndonesia`) |
| Tekan Post otomatis | Matikan kalau kamu mau menambahkan suara/efek dulu, lalu Post manual |
| Upload TikTok setelah YouTube | Setelah upload YouTube sukses, clip yang sama langsung dikirim ke TikTok |
| Waktu tunggu login | Berapa lama Clipper menunggu kamu login saat upload pertama |

Catatan:

- TikTok membatasi upload untuk sebagian akun baru (perlu verifikasi/usia akun). Kalau muncul
  kendala, Clipper menyimpan **tangkapan layar terakhir** (`data/tiktok_terakhir.png`) dan
  menampilkan pesan apa yang diminta TikTok.
- Upload TikTok butuh **sesi desktop** (jendela browser harus bisa terbuka), jadi jalankan Clipper
  dengan `run.bat`, bukan sebagai Windows service tanpa desktop.
- Semua proses lokal: tidak ada kredensial yang dikirim ke server lain.

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
│   ├── editor.py           # engine video editor: spec JSON → render (timeline, teks, subtitle, template, 4K)
│   ├── discover.py        # pencarian video viral (YouTube API + yt-dlp) & skor viral
│   ├── youtube.py          # OAuth device flow + upload resumable
│   ├── tiktok.py           # upload TikTok lewat browser sendiri (Playwright, opsional)
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
