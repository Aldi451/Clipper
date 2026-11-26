"""Checks that the launcher diagnoses a stale/missing YouTube runtime."""
from __future__ import annotations

import sys
import unittest
from contextlib import redirect_stdout
from datetime import date
from importlib.metadata import PackageNotFoundError
from io import StringIO
from unittest.mock import patch

from run import MIN_YTDLP_VERSION, check_ytdlp_runtime


class _CurrentTestDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 17)


class _StaleTestDate(date):
    @classmethod
    def today(cls):
        return cls(2027, 1, 1)


def _lookup(installed: dict[str, str]):
    def get_version(package: str) -> str:
        try:
            return installed[package]
        except KeyError as exc:
            raise PackageNotFoundError(package) from exc

    return get_version


class YtDlpRuntimeTests(unittest.TestCase):
    def test_accepts_supported_yt_dlp_ejs_and_deno(self) -> None:
        installed = {
            "yt-dlp": ".".join(str(part) for part in MIN_YTDLP_VERSION),
            "yt-dlp-ejs": "0.8.0",
            "deno": "2.9.8",
        }
        with (
            patch("run.date", _CurrentTestDate),
            patch("importlib.metadata.version", side_effect=_lookup(installed)),
            redirect_stdout(StringIO()),
        ):
            self.assertTrue(check_ytdlp_runtime())

    def test_rejects_old_yt_dlp(self) -> None:
        installed = {
            "yt-dlp": "2024.04.09",
            "yt-dlp-ejs": "0.8.0",
            "deno": "2.9.8",
        }
        with (
            patch("importlib.metadata.version", side_effect=_lookup(installed)),
            redirect_stdout(StringIO()),
        ):
            self.assertFalse(check_ytdlp_runtime())

    @unittest.skipUnless(sys.version_info >= (3, 11), "fresh-release check applies to Python 3.11+")
    def test_rejects_stale_yt_dlp_release(self) -> None:
        installed = {
            "yt-dlp": ".".join(str(part) for part in MIN_YTDLP_VERSION),
            "yt-dlp-ejs": "0.8.0",
            "deno": "2.9.8",
        }
        with (
            patch("run.date", _StaleTestDate),
            patch("importlib.metadata.version", side_effect=_lookup(installed)),
            redirect_stdout(StringIO()),
        ):
            self.assertFalse(check_ytdlp_runtime())

    def test_rejects_missing_javascript_runtime_package(self) -> None:
        installed = {
            "yt-dlp": "2026.10.21",
            "yt-dlp-ejs": "0.8.0",
        }
        with (
            patch("importlib.metadata.version", side_effect=_lookup(installed)),
            redirect_stdout(StringIO()),
        ):
            self.assertFalse(check_ytdlp_runtime())


if __name__ == "__main__":
    unittest.main()
