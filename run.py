"""Launcher Clipper."""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Clipper — auto clip video jadi Shorts, offline & gratis")
    parser.add_argument("--host", default=os.environ.get("CLIPPER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CLIPPER_PORT", "8420")))
    parser.add_argument("--no-browser", action="store_true", help="Jangan buka browser otomatis")
    parser.add_argument("--reload", action="store_true", help="Mode development")
    args = parser.parse_args()

    import uvicorn

    from app.server import create_app

    app = create_app()
    url = f"http://{'localhost' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    print("\n" + "=" * 58)
    print("  Clipper — buka:", url)
    print("  Data lokal:", Path("data").resolve())
    print("=" * 58 + "\n")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
