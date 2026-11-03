#!/usr/bin/env python3
"""Backup clip hasil render ke YouTube (sebagai video PRIVATE).

Kenapa begini, bukan "upload folder data"
-----------------------------------------
YouTube **hanya menerima berkas video**. Database (`clipper.db`), `settings.json`,
`.env`, cookies, dan token YouTube tidak bisa diunggah ke YouTube — bukan karena
Clipper, tapi karena YouTube bukan layanan penyimpanan berkas.

Yang **bisa** dijadikan backup offsite adalah aset paling besar dan paling mahal
untuk dibuat ulang: **file clip yang sudah dirender** (`data/clips/*.mp4`).
Script ini mengunggahnya sebagai video **private**:

* tidak muncul di beranda channel, tidak muncul di pencarian,
* tetap bisa ditonton lewat link oleh akun sendiri,
* jadi semacam "cold storage" gratis untuk video Bang.

BATASAN PENTING — kuota YouTube API
-----------------------------------
Setiap project Google punya **10.000 unit kuota per hari** (reset 00:00 waktu
Pasifik). Satu unggahan video memakan **1.600 unit**, jadi maksimal **~6 video
per hari**. Karena itu:
  * default script hanya mengunggah **5 berkas per jalan** (bisa diubah `--limit`,
    maksimal tetap dibatasi 6 oleh `--max-per-run`),
  * sisa berkas otomatis dilanjutkan pada jalan berikutnya (buku besar / ledger
    menyimpan apa yang sudah terunggah),
  * berkas yang sudah pernah di-backup **dilewati**, jadi aman dijalankan berkali-kali.

Keamanan
--------
* **Default = DRY-RUN**: tidak mengunggah apa pun, hanya menampilkan rencana.
  Unggahan sungguhan butuh `--upload` secara eksplisit.
* Tidak pernah menghapus atau memindahkan berkas lokal.
* Tidak menyentuh `data/` selain menulis satu berkas ledger kecil
  `data/backup_youtube.json`.
* Butuh koneksi YouTube yang sudah terhubung di Clipper (`data/youtube_token.json`).

Contoh pemakaian
----------------
    .venv\\Scripts\\python.exe tools\\backup_youtube.py                # rencana saja
    .venv\\Scripts\\python.exe tools\\backup_youtube.py --upload       # kirim (maks 5)
    .venv\\Scripts\\python.exe tools\\backup_youtube.py --upload --limit 2 --privacy unlisted
    .venv\\Scripts\\python.exe tools\\backup_youtube.py --show         # lihat ledger
    .venv\\Scripts\\python.exe tools\\backup_youtube.py --json         # ringkasan JSON

Exit code: 0 = selesai/rencana tampil, 1 = sebagian gagal, 2 = tidak bisa jalan
(belum login YouTube / berkas belum ada).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, youtube  # noqa: E402
from app.config import CLIP_DIR, DATA_DIR  # noqa: E402

LEDGER_FILE = DATA_DIR / "backup_youtube.json"
DAILY_UPLOAD_LIMIT = 6          # kuota API: 10.000 unit/hari, 1 upload = 1.600 unit
DEFAULT_PER_RUN = 5             # sisakan 1 slot untuk upload Shorts sehari-hari
TITLE_PREFIX = "[Backup]"


# --------------------------------------------------------------------------- #
# buku besar (ledger): apa yang sudah diunggah
# --------------------------------------------------------------------------- #
def load_ledger() -> dict[str, Any]:
    if not LEDGER_FILE.exists():
        return {"uploads": []}
    try:
        data = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("uploads"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"uploads": []}


def save_ledger(ledger: dict[str, Any]) -> None:
    """Tulis ledger secara aman (tulis ke .tmp lalu ganti)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(LEDGER_FILE)


def key_for(path: Path) -> str:
    """Identitas berkas: nama + ukuran + waktu ubah (tetap walau drive/folder berubah)."""
    try:
        stat = path.stat()
    except OSError:
        return path.name
    return f"{path.name}|{stat.st_size}|{int(stat.st_mtime)}"


def uploads_today(ledger: dict[str, Any]) -> int:
    """Hitung unggahan pada hari kalender Pasifik (dasar reset kuota YouTube)."""
    cutoff = time.time() - 24 * 3600
    return sum(1 for item in ledger.get("uploads", []) if float(item.get("at") or 0) >= cutoff)


# --------------------------------------------------------------------------- #
# daftar berkas yang layak di-backup
# --------------------------------------------------------------------------- #
def collect_files(with_titles: bool = True) -> list[dict[str, Any]]:
    """Ambil clip hasil render dari database + berkas mp4 yang ada di folder clips."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    if with_titles:
        from app import paths  # dipakai supaya path yang dipindah tetap terbaca

        for clip in db.list_clips():
            raw = clip.get("file")
            if not raw:
                continue
            path = Path(raw)
            if not path.exists():
                continue
            if not paths.media_exists(raw):
                continue
            key = key_for(path)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "path": path,
                "key": key,
                "title": (clip.get("title") or path.stem)[:90],
                "clip_id": clip.get("id", ""),
                "job_id": clip.get("job_id", ""),
                "duration": clip.get("duration") or 0,
                "size": path.stat().st_size,
            })

    # berkas yang tidak tercatat di database (mis. hasil render manual) tetap ikut
    if CLIP_DIR.exists():
        for path in sorted(CLIP_DIR.glob("*.mp4")):
            key = key_for(path)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "path": path,
                "key": key,
                "title": path.stem,
                "clip_id": "",
                "job_id": "",
                "duration": 0,
                "size": path.stat().st_size,
            })

    # urut stabil: berkas terbaru dulu, nama sebagai penentu kalau waktunya sama
    rows.sort(key=lambda item: (item["path"].stat().st_mtime, item["path"].name), reverse=True)
    return rows


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


# --------------------------------------------------------------------------- #
# rencana & eksekusi
# --------------------------------------------------------------------------- #
def plan(files: list[dict[str, Any]], ledger: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    done = {item.get("key") for item in ledger.get("uploads", [])}
    todo = [item for item in files if item["key"] not in done]
    sudah = [item for item in files if item["key"] in done]
    return todo, sudah


def backup_youtube(
    per_run: int = DEFAULT_PER_RUN,
    privacy: str = "private",
    dry_run: bool = True,
    quiet: bool = False,
) -> dict[str, Any]:
    """Unggah clip yang belum pernah di-backup. Kembalikan ringkasan."""
    ledger = load_ledger()

    def say(text: str = "") -> None:
        if not quiet:
            print(text, flush=True)

    files = collect_files()
    todo, sudah = plan(files, ledger)
    today = uploads_today(ledger)
    sisa_kuota = max(0, DAILY_UPLOAD_LIMIT - today)

    say("=" * 62)
    say("  Backup clip ke YouTube (video private)")
    say("=" * 62)
    say(f"Folder clip      : {CLIP_DIR}")
    say(f"Berkas ditemukan : {len(files)} ({human_size(sum(f['size'] for f in files))})")
    say(f"Sudah di-backup  : {len(sudah)}")
    say(f"Belum di-backup  : {len(todo)}")
    say(f"Kuota hari ini   : {today}/{DAILY_UPLOAD_LIMIT} terpakai (sisa {sisa_kuota} unggahan)")
    say(f"Mode             : {'RENCANA (dry-run, tidak mengunggah)' if dry_run else 'UNGGAH SUNGGUHAN'}")
    say("")

    if not files:
        say("Belum ada clip yang dirender — tidak ada yang bisa di-backup.")
        say("Render dulu di halaman Project, lalu jalankan script ini lagi.")
        return {"ok": True, "uploaded": 0, "failed": 0, "skipped": 0, "pending": 0}

    if not todo:
        say(f"Semua {len(sudah)} clip sudah punya salinan di YouTube.")
        say("Tidak ada yang perlu dikirim. Lihat daftarnya dengan --show.")
        return {"ok": True, "uploaded": 0, "failed": 0, "skipped": len(sudah), "pending": 0}

    if dry_run and not youtube.is_connected():
        say("[!] YouTube belum terhubung di Clipper — hubungkan di tab YouTube dulu.")
        say("    (mode rencana tetap bisa menampilkan daftar di bawah)")
        say("")

    batas = min(per_run, sisa_kuota) if sisa_kuota else 0
    if not dry_run and batas == 0:
        say("[!] Kuota upload hari ini sudah habis (maksimal 6 video/hari).")
        say("    Jalankan lagi besok — script akan melanjutkan sisanya otomatis.")
        return {"ok": False, "uploaded": 0, "failed": 0, "skipped": len(sudah),
                "pending": len(todo), "reason": "kuota habis"}

    for item in todo[:max(0, batas)] if batas else todo:
        tanda = "RENCANA" if dry_run else "UNGGAH"
        say(f"[{tanda}] {item['path'].name}  ({human_size(item['size'])})  {item['title'][:50]}")

    if dry_run:
        if batas and len(todo) > batas:
            say("")
            say(f"Catatan: hanya {batas} berkas yang akan dikirim per jalan (kuota API),")
            say(f"sisanya {max(0, len(todo) - batas)} berkas menunggu jalan berikutnya.")
        say("")
        say("Ini baru rencana. Untuk benar-benar mengunggah jalankan dengan --upload.")
        return {"ok": True, "dry_run": True, "pending": len(todo),
                "would_upload": min(len(todo), batas) if batas else len(todo),
                "skipped": len(sudah)}

    if not youtube.is_connected():
        say("[!] YouTube belum terhubung. Buka Clipper → tab YouTube → Hubungkan akun.")
        return {"ok": False, "uploaded": 0, "failed": 0, "skipped": len(sudah),
                "pending": len(todo), "reason": "belum login"}

    uploaded = 0
    failed = 0
    say("")
    for item in todo[:batas]:
        name = item["path"].name
        say(f"→ {name} …")
        judul = f"{TITLE_PREFIX} {item['title']}".strip()[:95]
        deskripsi = (
            "Cadangan otomatis dari Clipper.\n"
            f"Berkas: {name}\n"
            + (f"Clip ID: {item['clip_id']}\n" if item["clip_id"] else "")
            + (f"Durasi: {item['duration']:.1f}s\n" if item["duration"] else "")
            + f"Diunggah: {time.strftime('%Y-%m-%d %H:%M')}\n\n"
            "Video ini PRIVATE — hanya untuk arsip, bukan publikasi."
        )
        terakhir = {"teks": ""}

        def progress(frac: float, msg: str) -> None:
            """Cetak progres hanya kalau teksnya berubah (hindari banjir baris)."""
            if not msg or msg == terakhir["teks"]:
                return
            terakhir["teks"] = msg
            say(f"   {msg}")

        try:
            result = youtube.upload_video(
                item["path"],
                title=judul,
                description=deskripsi,
                hashtags="",
                privacy=privacy,
                notify_subscribers=False,
                on_progress=progress,
            )
        except Exception as exc:  # noqa: BLE001 - pesan apa adanya dari API
            failed += 1
            say(f"   [!] gagal: {str(exc).splitlines()[0][:180]}")
            ledger.setdefault("failures", []).append(
                {"key": item["key"], "file": name, "at": time.time(), "error": str(exc)[:300]}
            )
            save_ledger(ledger)
            # kuota habis / otorisasi dicabut → hentikan, jangan buang sisa kuota
            teks = str(exc).lower()
            if "quota" in teks or "exceeded" in teks:
                say("   Kuota API habis. Sisa berkas dilanjutkan besok.")
                break
            continue

        uploaded += 1
        say(f"   [ok] {result.get('watch_url') or result.get('url')} (privacy: {privacy})")
        ledger.setdefault("uploads", []).append({
            "key": item["key"],
            "file": name,
            "clip_id": item["clip_id"],
            "video_id": result.get("id", ""),
            "url": result.get("watch_url") or result.get("url", ""),
            "privacy": privacy,
            "at": time.time(),
            "size": item["size"],
        })
        save_ledger(ledger)

    say("")
    say(f"Selesai: {uploaded} terunggah, {failed} gagal, {len(sudah)} sudah ada sebelumnya.")
    sisa = len(todo) - uploaded
    if sisa > 0:
        say(f"Masih ada {sisa} berkas menunggu — jalankan lagi besok (kuota API 6 video/hari).")
    say(f"Ledger: {LEDGER_FILE}")
    return {"ok": failed == 0, "uploaded": uploaded, "failed": failed,
            "skipped": len(sudah), "pending": max(0, sisa)}


def show_ledger() -> int:
    ledger = load_ledger()
    items = ledger.get("uploads", [])
    print("=" * 62)
    print("  Isi backup YouTube (ledger)")
    print("=" * 62)
    print(f"Berkas: {LEDGER_FILE}")
    print(f"Total : {len(items)} video")
    if not items:
        print("\nBelum ada yang di-backup. Jalankan tanpa argumen untuk melihat rencana.")
        return 0
    total = sum(int(item.get("size") or 0) for item in items)
    print(f"Ukuran: {human_size(total)}")
    print("")
    for item in sorted(items, key=lambda x: x.get("at") or 0, reverse=True):
        tanggal = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(item.get("at") or 0)))
        print(f"  {tanggal}  {item.get('privacy', '?'):8}  {item.get('url', '')}")
        print(f"      {item.get('file', '')}")
    if ledger.get("failures"):
        print("")
        print(f"Percobaan gagal tercatat: {len(ledger['failures'])}")
        for failure in ledger["failures"][-3:]:
            print(f"  - {failure.get('file')}: {str(failure.get('error'))[:100]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backup clip hasil render ke YouTube sebagai video private",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Contoh:\n"
            "  python tools/backup_youtube.py                  # rencana (dry-run)\n"
            "  python tools/backup_youtube.py --upload         # kirim, maks 5 berkas\n"
            "  python tools/backup_youtube.py --upload --limit 2 --privacy unlisted\n"
            "  python tools/backup_youtube.py --show\n"
        ),
    )
    parser.add_argument("--upload", action="store_true",
                        help="benar-benar mengunggah (tanpa ini hanya rencana)")
    parser.add_argument("--limit", type=int, default=DEFAULT_PER_RUN,
                        help=f"maksimal berkas per jalan (default {DEFAULT_PER_RUN})")
    parser.add_argument("--privacy", choices=("private", "unlisted"), default="private",
                        help="privasi video backup (default private)")
    parser.add_argument("--show", action="store_true", help="tampilkan ledger lalu keluar")
    parser.add_argument("--json", action="store_true", help="ringkasan JSON")
    args = parser.parse_args()

    try:
        if args.show:
            return show_ledger()

        limit = max(1, min(int(args.limit), DAILY_UPLOAD_LIMIT))
        result = backup_youtube(
            per_run=limit,
            privacy=args.privacy,
            dry_run=not args.upload,
            quiet=args.json,
        )
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        if result.get("reason") in ("kuota habis", "belum login"):
            return 2
        return 0 if result.get("ok") else 1
    except KeyboardInterrupt:
        print("\nDibatalkan.")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"[!] Error tak terduga: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
