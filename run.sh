#!/usr/bin/env bash
# ===================================================================
#  Clipper — jalankan di macOS / Linux
#  Otomatis: cek Python -> buat .venv -> install dependensi -> start
# ===================================================================
set -u
cd "$(dirname "$0")" || exit 1

echo
echo "=========================================================="
echo "  Clipper — persiapan"
echo "=========================================================="
echo

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "  [!] Python belum terpasang."
  echo
  echo "  macOS : brew install python@3.12"
  echo "  Ubuntu/Debian : sudo apt install python3 python3-venv python3-pip"
  echo
  exit 1
fi

if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "  [!] Python terlalu lama ($("$PY" -V 2>&1)). Butuh 3.10+."
  exit 1
fi
echo "  [OK] Python: $("$PY" -V 2>&1)"

if [ ! -x ".venv/bin/python" ]; then
  echo "  [..] Membuat virtualenv .venv (sekali saja)"
  if ! "$PY" -m venv .venv; then
    echo "  [!] Gagal membuat venv. Di Debian/Ubuntu coba: sudo apt install python3-venv"
    exit 1
  fi
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c "import datetime, sys, fastapi, uvicorn, imageio_ffmpeg; from importlib.metadata import version; y=tuple(int(p) for p in version('yt-dlp').split('.')[:3]); e=tuple(int(p) for p in version('yt-dlp-ejs').split('.')[:3]); d=tuple(int(p) for p in version('deno').split('.')[:3]); age=(datetime.date.today()-datetime.date(*y)).days; assert y >= (2026,10,21) and (sys.version_info < (3,11) or age <= 45) and e >= (0,8,0) and d >= (2,9,8)" >/dev/null 2>&1; then
  echo "  [..] Memasang / memperbarui dependensi (termasuk extractor YouTube)"
  python -m pip install --upgrade pip >/dev/null 2>&1
  if ! python -m pip install -r requirements.txt; then
    echo "  [!] Gagal memasang dependensi. Cek koneksi internet lalu coba lagi."
    exit 1
  fi
  if ! python -m pip install --upgrade "yt-dlp[default,deno]>=2026.10.21"; then
    echo "  [!] Gagal memperbarui yt-dlp. Cek koneksi internet lalu coba lagi."
    exit 1
  fi
else
  echo "  [OK] Dependensi dan runtime YouTube sudah siap"
fi

echo
echo "  Menjalankan Clipper..."
echo
python run.py "$@"
code=$?
if [ "$code" -ne 0 ]; then
  echo
  echo "  Clipper berhenti dengan kode $code."
  echo "  Kalau ini tidak terduga, jalankan: python run.py --doctor"
fi
exit "$code"
