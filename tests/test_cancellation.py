"""Tests for user-requested cancellation of downloads and FFmpeg renders."""
from __future__ import annotations

import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ingest, media, pipeline
from app.cancellation import OperationCancelled


class CancellationTests(unittest.TestCase):
    def test_download_hook_stops_attempt_and_cleans_partial_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            calls = 0

            def should_cancel() -> bool:
                nonlocal calls
                calls += 1
                return calls >= 2

            def fake_extract(_yt_dlp, _url, opts, out_dir):
                partial = out_dir / "source.mp4.part"
                partial.write_bytes(b"partial")
                opts["progress_hooks"][0]({
                    "status": "downloading",
                    "total_bytes": 100,
                    "downloaded_bytes": 25,
                })
                return partial

            with (
                patch.dict(sys.modules, {"yt_dlp": types.SimpleNamespace()}),
                patch.object(ingest, "WORK_DIR", root),
                patch.object(ingest, "ensure_dirs"),
                patch.object(ingest, "load_settings", return_value={"ytdlp_player_client": "default"}),
                patch.object(ingest, "cookie_path", return_value=None),
                patch.object(ingest, "ffmpeg_for_ytdlp", return_value="/ffmpeg"),
                patch.object(ingest, "_extract", side_effect=fake_extract),
            ):
                with self.assertRaises(OperationCancelled):
                    ingest.download(
                        "https://example.org/video",
                        job_label="cancel-test",
                        cancel_check=should_cancel,
                    )

            self.assertFalse((root / "cancel-test" / "source.mp4.part").exists())

    def test_ffmpeg_runner_terminates_subprocess_when_cancelled(self) -> None:
        calls = 0

        def should_cancel() -> bool:
            nonlocal calls
            calls += 1
            return calls >= 3

        started = time.monotonic()
        with self.assertRaises(OperationCancelled):
            media.run_with_progress(
                [sys.executable, "-u", "-c", "import time; print('started', flush=True); time.sleep(30)"],
                30,
                cancel_check=should_cancel,
            )
        self.assertLess(time.monotonic() - started, 3)

    def test_cancel_manager_seals_completed_operations(self) -> None:
        key = "render:cancel-test"
        self.assertTrue(pipeline._claim(key))
        try:
            pipeline._seal_cancellation(key)
            self.assertEqual(pipeline.cancel_render("cancel-test"), "finishing")
            pipeline._check_cancelled(key)
        finally:
            pipeline._release(key)

    def test_cancelled_prepare_is_not_recorded_as_an_error(self) -> None:
        job_id = "cancel-prepare-test"
        updates: list[dict] = []
        self.assertTrue(pipeline._claim(f"analyze:{job_id}"))
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(pipeline, "WORK_DIR", Path(temp_dir)),
                patch.object(pipeline.db, "get_job", return_value={"id": job_id, "source": ""}),
                patch.object(pipeline.db, "update_job", side_effect=lambda _id, **fields: updates.append(fields)),
            ):
                pipeline.cancel_job(job_id)
                pipeline._run_prepare(job_id)

        self.assertTrue(any(update.get("status") == "cancelled" for update in updates))
        self.assertFalse(any(update.get("status") == "error" for update in updates))
        self.assertEqual(updates[-1].get("message"), "Persiapan video dibatalkan")

    def test_cancelled_render_is_not_recorded_as_a_failure(self) -> None:
        clip_id = "cancel-render-test"
        job_id = "cancel-render-job"
        updates: list[dict] = []
        self.assertTrue(pipeline._claim(f"render:{clip_id}"))
        with (
            patch.object(pipeline.db, "get_clip", return_value={"id": clip_id, "job_id": job_id}),
            patch.object(pipeline.db, "get_job", return_value={"id": job_id}),
            patch.object(pipeline.db, "update_clip", side_effect=lambda _id, **fields: updates.append(fields)),
            patch.object(pipeline.editor, "cleanup_workdir"),
        ):
            pipeline.cancel_render(clip_id)
            pipeline._run_render(clip_id)

        self.assertTrue(any(update.get("status") == "cancelled" for update in updates))
        self.assertFalse(any(update.get("status") == "error" for update in updates))
        self.assertEqual(updates[-1].get("message"), "Render dibatalkan")


if __name__ == "__main__":
    unittest.main()
