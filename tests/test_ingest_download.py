"""Regression coverage for resilient YouTube-link download attempts."""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ingest


class IngestDownloadTests(unittest.TestCase):
    def test_youtube_url_detection_uses_host_boundaries(self) -> None:
        for url in (
            "https://www.youtube.com/watch?v=abc",
            "https://m.youtube.com/shorts/abc",
            "https://youtu.be/abc?t=12",
            "https://www.youtube-nocookie.com/embed/abc",
        ):
            with self.subTest(url=url):
                self.assertTrue(ingest.is_youtube_url(url))

        self.assertFalse(ingest.is_youtube_url("https://youtube.com.example.org/watch?v=abc"))
        self.assertFalse(ingest.is_youtube_url("https://example.org/?next=youtube.com"))
        self.assertFalse(ingest.is_youtube_url("not a URL"))

    def test_transient_http_errors_are_retryable_network_errors(self) -> None:
        for message in (
            "HTTP Error 403: Forbidden",
            "HTTP Error 429: Too Many Requests",
            "HTTP Error 503: Service Unavailable",
        ):
            with self.subTest(message=message):
                self.assertEqual(ingest.classify_download_error(message), "network")

    def test_youtube_download_falls_back_to_web_safari_client(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_path = root / "source.mp4"
            observed_args: list[dict | None] = []

            def extract(_yt_dlp, _url, opts, _out_dir):
                extra = opts.get("extractor_args")
                observed_args.append(extra)
                if extra is None:
                    raise RuntimeError("HTTP Error 503: Service Unavailable")
                return result_path

            with (
                patch.dict(sys.modules, {"yt_dlp": types.SimpleNamespace()}),
                patch.object(ingest, "WORK_DIR", root),
                patch.object(ingest, "ensure_dirs"),
                patch.object(ingest, "load_settings", return_value={"ytdlp_player_client": "auto"}),
                patch.object(ingest, "cookie_path", return_value=None),
                patch.object(ingest, "ffmpeg_for_ytdlp", return_value="/ffmpeg"),
                patch.object(ingest, "_extract", side_effect=extract),
            ):
                downloaded = ingest.download("https://youtu.be/abc", job_label="youtube-test")

        self.assertEqual(downloaded, result_path)
        self.assertEqual(observed_args, [
            None,
            {"youtube": {"player_client": ["web_safari"]}},
        ])

    def test_early_network_error_still_restarts_after_format_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_path = root / "source.mp4"
            default_calls = 0

            def extract(_yt_dlp, _url, opts, _out_dir):
                nonlocal default_calls
                if opts.get("extractor_args") is None:
                    default_calls += 1
                    if default_calls == 1:
                        raise RuntimeError("HTTP Error 503: Service Unavailable")
                    return result_path
                raise RuntimeError("Requested format is not available")

            with (
                patch.dict(sys.modules, {"yt_dlp": types.SimpleNamespace()}),
                patch.object(ingest, "WORK_DIR", root),
                patch.object(ingest, "ensure_dirs"),
                patch.object(ingest, "load_settings", return_value={"ytdlp_player_client": "auto"}),
                patch.object(ingest, "cookie_path", return_value=None),
                patch.object(ingest, "ffmpeg_for_ytdlp", return_value="/ffmpeg"),
                patch.object(ingest, "_extract", side_effect=extract),
                patch.object(ingest.time, "sleep") as sleep,
            ):
                downloaded = ingest.download("https://youtube.com/watch?v=abc", job_label="mixed-errors")

        self.assertEqual(downloaded, result_path)
        self.assertEqual(default_calls, 2)
        sleep.assert_called_once_with(1.5)

    def test_default_client_retries_a_transient_network_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_path = root / "source.mp4"
            attempts = 0

            def extract(*_args):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("HTTP Error 429: Too Many Requests")
                return result_path

            with (
                patch.dict(sys.modules, {"yt_dlp": types.SimpleNamespace()}),
                patch.object(ingest, "WORK_DIR", root),
                patch.object(ingest, "ensure_dirs"),
                patch.object(ingest, "load_settings", return_value={"ytdlp_player_client": "default"}),
                patch.object(ingest, "cookie_path", return_value=None),
                patch.object(ingest, "ffmpeg_for_ytdlp", return_value="/ffmpeg"),
                patch.object(ingest, "_extract", side_effect=extract),
                patch.object(ingest.time, "sleep") as sleep,
            ):
                downloaded = ingest.download("https://www.youtube.com/watch?v=abc", job_label="retry-test")

        self.assertEqual(downloaded, result_path)
        self.assertEqual(attempts, 2)
        sleep.assert_called_once_with(1.5)


if __name__ == "__main__":
    unittest.main()
