@echo off
REM ===================================================================
REM  Clipper - PERBARUI ke versi terbaru (double-click file ini)
REM
REM  Kenapa perlu: kalau Bang menerima perbaikan dari saya, kode di folder
REM  ini harus diperbarui dulu. Kalau hanya menutup-lalu-membuka Clipper,
REM  versinya tetap yang lama.
REM
REM  Skrip ini:
REM    1. Menutup Clipper yang sedang jalan (supaya file tidak terkunci)
REM    2. Mengambil kode terbaru (git kalau ada, kalau tidak unduh zip)
REM    3. Memasang dependensi baru (kalau ada)
REM    4. Menampilkan versi lama -> versi baru
REM
REM  Repo Clipper bersifat PRIVATE. GitHub menolak unduhan tanpa login dan
REM  menjawab "404 Not Found" (bukan 403) supaya keberadaan repo tidak bocor.
REM  Karena itu skrip ini mendukung token GitHub:
REM    - simpan token di file  github_token.txt  (1 baris, di folder ini)
REM    - atau isi otomatis saat skrip menanyakannya
REM  Token TIDAK pernah ditulis ke log, dan file github_token.txt diabaikan git.
REM
REM  Data Bang TIDAK dihapus: folder data\ (video, klip, cookies, token,
REM  profil TikTok) tidak pernah ditimpa. File .env juga tidak.
REM ===================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Clipper - Perbarui
set "ROOT=%~dp0"
set "NEWVER="

echo.
echo ==========================================================
echo   Clipper - perbarui ke versi terbaru
echo ==========================================================
echo   Folder: %ROOT%
echo.

REM ---------- versi sebelum update ----------
set "OLDVER="
if exist ".venv\Scripts\python.exe" (
  for /f "usebackq delims=" %%V in (`".venv\Scripts\python.exe" -c "import re,pathlib;m=re.search(r'VERSION\s*=\s*.([0-9.]+).',pathlib.Path('app/config.py').read_text(encoding='utf-8'));print(m.group(1) if m else '?')" 2^>nul`) do set "OLDVER=%%V"
)
if not defined OLDVER (
  for /f "tokens=3" %%V in ('findstr /b /c:"VERSION = " app\config.py 2^>nul') do set "OLDVER=%%V"
  set "OLDVER=!OLDVER:"=!"
)
echo   Versi terpasang sekarang: !OLDVER!
echo.

REM ---------- 1. hentikan Clipper ----------
echo [1/4] Menghentikan Clipper yang sedang jalan...
taskkill /f /im "ClipperService.exe" >nul 2>nul
REM matikan proses run.py milik folder ini saja
for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*run.py*' -and $_.CommandLine -like '*%ROOT%*' } | Select-Object -ExpandProperty ProcessId" 2^>nul`) do (
  echo        menghentikan PID %%L
  taskkill /f /pid %%L >nul 2>nul
)
echo        selesai.

REM ---------- 2. ambil kode terbaru ----------
echo.
echo [2/4] Mengambil kode terbaru...
set "GOT=0"

where git >nul 2>nul
if exist ".git" (
  if not errorlevel 1 (
    echo        git terdeteksi - menjalankan git pull...
    git pull --ff-only
    if not errorlevel 1 set "GOT=1"
    if not errorlevel 1 echo        [OK] kode diperbarui lewat git.
  )
)

if "!GOT!"=="0" (
  set "OWNER=Aldi451"
  set "REPO=Clipper"
  set "BRANCH=main"
  set "TMPZIP=%TEMP%\clipper_update.zip"
  set "TMPDIR=%TEMP%\clipper_update"
  call :download || goto :failed

  powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path '!TMPDIR!') { Remove-Item '!TMPDIR!' -Recurse -Force }; Expand-Archive -Path '!TMPZIP!' -DestinationPath '!TMPDIR!' -Force" >nul 2>nul

  REM nama folder di dalam zip berbeda: Clipper-main (codeload) atau
  REM Aldi451-Clipper-<commit> (api). Jadi cari folder yang punya run.py.
  set "SRC="
  for /d %%D in ("!TMPDIR!\*") do (
    if exist "%%D\run.py" set "SRC=%%D"
  )
  if not defined SRC (
    if exist "!TMPDIR!\run.py" set "SRC=!TMPDIR!"
  )
  if not defined SRC (
    echo   [!] Isi unduhan tidak seperti yang diharapkan.
    goto :failed
  )
  echo        Menyalin kode baru ^(data\ tidak disentuh^)...
  for %%D in (app web tools assets) do (
    if exist "!SRC!\%%D" robocopy "!SRC!\%%D" "%%D" /e /nfl /ndl /njh /njs /nc /ns >nul
  )
  for %%F in (run.py requirements.txt README.md run.bat repair.bat service.bat build_service_exe.bat diagnosa_tiktok.bat perbaiki_tiktok.bat backup_youtube.bat service_manager.py) do (
    if exist "!SRC!\%%F" copy /y "!SRC!\%%F" "%%F" >nul
  )
  REM update.bat diperbarui paling akhir, setelah file lain selesai
  if exist "!SRC!\update.bat" copy /y "!SRC!\update.bat" "update.bat" >nul
  echo        [OK] kode diperbarui dari unduhan.
  rmdir /s /q "!TMPDIR!" >nul 2>nul
  del /f /q "!TMPZIP!" >nul 2>nul
)

REM ---------- 3. dependensi ----------
echo.
echo [3/4] Memasang dependensi (kalau ada yang baru)...
if not exist ".venv\Scripts\python.exe" (
  echo        .venv belum ada - jalankan run.bat dulu, dependensi akan dipasang di sana.
) else (
  ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
  if errorlevel 1 echo        [!] Sebagian dependensi gagal dipasang - jalankan run.bat untuk mencoba lagi.
  if not errorlevel 1 echo        [OK] dependensi siap.
)

REM ---------- 4. versi baru ----------
echo.
echo [4/4] Versi sekarang:
if exist ".venv\Scripts\python.exe" (
  for /f "usebackq delims=" %%V in (`".venv\Scripts\python.exe" -c "import re,pathlib;m=re.search(r'VERSION\s*=\s*.([0-9.]+).',pathlib.Path('app/config.py').read_text(encoding='utf-8'));print(m.group(1) if m else '?')" 2^>nul`) do set "NEWVER=%%V"
)
if not defined NEWVER (
  for /f "tokens=3" %%V in ('findstr /b /c:"VERSION = " app\config.py 2^>nul') do set "NEWVER=%%V"
  set "NEWVER=!NEWVER:"=!"
)
echo        sebelum: !OLDVER!
echo        sesudah: !NEWVER!
echo.
if "!OLDVER!"=="!NEWVER!" (
  echo   Versi tidak berubah. Kemungkinan folder ini sudah versi terbaru,
  echo   atau unduhan tidak berhasil. Coba lagi dengan koneksi internet aktif.
) else (
  echo   [OK] Clipper diperbarui ke versi !NEWVER!
)
echo.
echo ==========================================================
echo   Selesai. Double-click run.bat untuk menjalankan Clipper.
echo   Setelah terbuka, versi terlihat di kiri bawah layar (v...).
echo ==========================================================
echo.
pause
exit /b 0

REM ===================================================================
REM  :download - unduh zip kode terbaru, termasuk repo PRIVATE
REM  Hasil: errorlevel 0 = berhasil, 1 = gagal
REM ===================================================================
:download
set "GHTOKEN="
if exist "github_token.txt" (
  set /p GHTOKEN=<github_token.txt
)
if not defined GHTOKEN if defined GITHUB_TOKEN set "GHTOKEN=%GITHUB_TOKEN%"
REM buang spasi/enter yang tidak sengaja ikut tersalin
if defined GHTOKEN (
  for /f "tokens=* delims= " %%T in ("!GHTOKEN!") do set "GHTOKEN=%%T"
)

call :try_download
if not errorlevel 1 exit /b 0

REM 4 = jaringan/DNS/proxy, 3/5 = ditolak GitHub (repo private / token)
if "!DLERR!"=="4" (
  echo.
  echo   [!] Tidak bisa menghubungi GitHub sama sekali.
  echo       Periksa koneksi internet, firewall, atau proxy kantor.
  echo       Tidak ada file yang diubah.
  exit /b 1
)

REM --- gagal: mungkin repo private dan belum ada token ---
if not defined GHTOKEN (
  echo.
  echo   [!] GitHub menjawab 404 Not Found.
  echo       Repo Clipper bersifat PRIVATE, jadi unduhan tanpa login ditolak.
  echo.
  echo   Cara paling cepat - pilih salah satu:
  echo     1. Jadikan repo publik: buka github.com/Aldi451/Clipper
  echo        - Settings - General - paling bawah - Change visibility - Public
  echo     2. Atau tempel token GitHub di sini ^(sekali saja, disimpan
  echo        sebagai github_token.txt di folder ini^).
  echo        Bikin token: github.com/settings/tokens - Generate new token ^(classic^)
  echo        - centang scope "repo" - Generate - salin.
  echo.
  set "PASTE="
  set /p PASTE=   Tempel token GitHub (Enter = batal): 
  if defined PASTE (
    set "GHTOKEN=!PASTE!"
    > "github_token.txt" echo !PASTE!
    echo        token disimpan ke github_token.txt
    call :try_download
    if not errorlevel 1 exit /b 0
  )
)

echo.
echo   [!] Unduhan tetap gagal.
if defined GHTOKEN (
  echo       Token terpakai tapi GitHub menolak. Kemungkinan:
  echo         - token kedaluwarsa / sudah dicabut
  echo         - token tidak punya akses ke repo Aldi451/Clipper
  echo           ^(token classic butuh scope "repo"; fine-grained butuh
  echo            izin Contents: Read-only pada repo itu^)
  echo       Hapus github_token.txt lalu jalankan lagi untuk memasang token baru.
) else (
  echo       Periksa koneksi internet, lalu coba lagi.
)
echo       Tidak ada file yang diubah - Clipper tetap seperti semula.
exit /b 1

REM ===================================================================
REM  :try_download - coba semua cara unduh yang tersedia
REM ===================================================================
:try_download
set "CLIPPER_OWNER=Aldi451"
set "CLIPPER_REPO=Clipper"
set "CLIPPER_BRANCH=main"
set "CLIPPER_ZIP=%TEMP%\clipper_update.zip"
set "CLIPPER_TOKEN=!GHTOKEN!"
if exist "!CLIPPER_ZIP!" del /f /q "!CLIPPER_ZIP!" >nul 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $zip=$env:CLIPPER_ZIP; $tok=$env:CLIPPER_TOKEN; $base=$env:CLIPPER_OWNER+'/'+$env:CLIPPER_REPO; $br=$env:CLIPPER_BRANCH; $api='https://api.github.com/repos/'+$base+'/zipball/'+$br; $code='https://codeload.github.com/'+$base+'/zip/refs/heads/'+$br; $tries=@(); if ($tok) { $tries+=@($api,$true); $tries+=@($code,$true) } else { $tries+=@($code,$false) }; $last=0; $ok=$false; for ($i=0; $i -lt $tries.Count; $i+=2) { $u=$tries[$i]; $withTok=$tries[$i+1]; $h=@{'User-Agent'='Clipper-Updater'}; if ($withTok -and $tok) { $h['Authorization']='Bearer '+$tok }; try { if (Test-Path $zip) { Remove-Item $zip -Force }; Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $zip -Headers $h -TimeoutSec 180; if ((Get-Item $zip).Length -gt 20000) { $ok=$true; break } } catch { $last=0; try { $last=[int]$_.Exception.Response.StatusCode } catch {}; if (-not $last) { try { $last=[int]$_.Exception.Response.StatusCode.value__ } catch {} } } }; if ($ok) { exit 0 } if ($last -eq 401 -or $last -eq 403) { exit 5 } if ($last -eq 404) { exit 3 } exit 4"

set "DLERR=!errorlevel!"
if "!DLERR!"=="0" (
  echo        [OK] paket kode terunduh.
  exit /b 0
)
exit /b 1

:failed
echo.
echo ==========================================================
echo   Update gagal. Clipper TIDAK diubah (masih versi !OLDVER!).
echo   Jalankan run.bat seperti biasa - tidak ada yang rusak.
echo ==========================================================
echo.
pause
exit /b 1
