"""Regression tests for FFmpeg progress stalls and render terminal states."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app import db, media, pipeline
from app.cancellation import OperationCancelled


class RenderProgressTests(unittest.TestCase):
    def test_render_is_marked_queued_before_background_worker_starts(self) -> None:
        clip_id = "queued-render-test"
        updates: list[dict[str, Any]] = []
        submitted: list[tuple[Any, tuple[Any, ...]]] = []

        class DeferredExecutor:
            def submit(self, fn: Any, *args: Any) -> object:
                submitted.append((fn, args))
                return object()

        try:
            with (
                patch.object(pipeline.db, "update_clip", side_effect=lambda _id, **fields: updates.append(fields)),
                patch.object(pipeline, "render_pool", DeferredExecutor()),
            ):
                self.assertTrue(pipeline.start_render(clip_id))
        finally:
            pipeline._release(f"render:{clip_id}")

        self.assertEqual(updates[0]["status"], "queued")
        self.assertEqual(updates[0]["progress"], 0.0)
        self.assertEqual(updates[0]["message"], "Menunggu render…")
        self.assertEqual(submitted, [(pipeline._run_render, (clip_id,))])

    def test_render_all_queues_only_clips_in_the_requested_mode(self) -> None:
        clips = [
            {"id": "ai-one", "status": "suggested", "meta": {"origin": "ai"}},
            {"id": "manual-one", "status": "suggested", "meta": {"origin": "manual", "manual": True}},
            {"id": "ai-uploaded", "status": "uploaded", "meta": {"origin": "ai"}},
        ]
        queued_ids: list[str] = []

        def queue(clip_id: str) -> bool:
            queued_ids.append(clip_id)
            return True

        with (
            patch.object(pipeline.db, "list_clips", return_value=clips),
            patch.object(pipeline, "start_render", side_effect=queue),
        ):
            queued = pipeline.render_job_all("job-render-mode-test", mode="ai")

        self.assertEqual(queued, 1)
        self.assertEqual(queued_ids, ["ai-one"])

    def test_queued_clips_are_reset_when_the_app_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "clipper.db"
            with (
                patch.object(db, "DB_FILE", db_path),
                patch.object(db, "ensure_dirs"),
            ):
                db.init_db()
                job = db.create_job("upload", title="Queue recovery test")
                clip = db.create_clip(
                    job["id"], title="Queued clip", duration=10,
                    status="queued", meta={"origin": "ai"},
                )
                db.update_clip(clip["id"], progress=0.37, message="Menunggu render…")

                db.reset_jobs_on_startup()
                recovered = db.get_clip(clip["id"])

        self.assertEqual(recovered["status"], "suggested")
        self.assertEqual(recovered["progress"], 0.0)
        self.assertEqual(recovered["message"], "")
        self.assertEqual(recovered["error"], "")

    def test_ffmpeg_runner_stops_a_render_that_stalls_after_92_percent(self) -> None:
        progress: list[float] = []
        script = (
            "import time; "
            "print('out_time_ms=92000000', flush=True); "
            "print('progress=continue', flush=True); "
            "time.sleep(30)"
        )
        started = time.monotonic()

        with self.assertRaisesRegex(
            media.FFmpegProgressTimeout, "tidak melaporkan kemajuan",
        ):
            media.run_with_progress(
                [sys.executable, "-u", "-c", script],
                total_seconds=100,
                on_progress=progress.append,
                progress_stall_timeout=0.3,
            )

        self.assertEqual(progress, [0.92])
        self.assertLess(time.monotonic() - started, 3)

    def test_cancellation_still_preempts_the_progress_stall_timeout(self) -> None:
        calls = 0

        def should_cancel() -> bool:
            nonlocal calls
            calls += 1
            return calls >= 4

        script = (
            "import time; "
            "print('out_time_ms=92000000', flush=True); "
            "print('progress=continue', flush=True); "
            "time.sleep(30)"
        )
        started = time.monotonic()

        with self.assertRaises(OperationCancelled):
            media.run_with_progress(
                [sys.executable, "-u", "-c", script],
                total_seconds=100,
                cancel_check=should_cancel,
                progress_stall_timeout=0.3,
            )

        self.assertLess(time.monotonic() - started, 3)

    def test_ffmpeg_end_marker_completes_progress_even_if_out_time_rounds_short(self) -> None:
        progress: list[float] = []
        script = (
            "print('out_time_ms=92000000', flush=True); "
            "print('progress=continue', flush=True); "
            "print('out_time_us=100000000', flush=True); "
            "print('progress=end', flush=True)"
        )

        media.run_with_progress(
            [sys.executable, "-u", "-c", script],
            total_seconds=100,
            on_progress=progress.append,
            progress_stall_timeout=1,
        )

        self.assertEqual(progress[0], 0.92)
        self.assertEqual(progress[-1], 1.0)

    def test_stalled_render_is_recorded_as_a_terminal_error(self) -> None:
        clip_id = "stalled-render-test"
        job_id = "stalled-render-job"
        key = f"render:{clip_id}"
        updates: list[dict[str, Any]] = []
        source_path = Path(tempfile.gettempdir()) / "stalled-render-source.mp4"

        def record_update(_clip_id: str, **fields: Any) -> None:
            updates.append(fields)

        self.assertTrue(pipeline._claim(key))

        try:
            with (
                patch.object(pipeline.db, "get_clip", return_value={
                    "id": clip_id, "job_id": job_id, "meta": {},
                }),
                patch.object(pipeline.db, "get_job", return_value={
                    "id": job_id, "source_path": str(source_path), "meta": {},
                }),
                patch.object(pipeline.db, "update_clip", side_effect=record_update),
                patch.object(pipeline, "resolve_media_path", return_value=source_path),
                patch.object(pipeline, "load_settings", return_value={"auto_framing": False}),
                patch.object(
                    pipeline.media,
                    "probe",
                    return_value=media.MediaInfo(duration=100, has_audio=True, has_video=True),
                ),
                patch.object(pipeline.transcribe, "load_transcript", return_value={}),
                patch.object(pipeline.editor, "load_or_create_spec", return_value={}),
                patch.object(
                    pipeline.editor,
                    "render_spec",
                    side_effect=media.FFmpegProgressTimeout("FFmpeg stopped advancing"),
                ),
                patch.object(pipeline.traceback, "print_exc"),
            ):
                pipeline._run_render(clip_id)
        finally:
            pipeline._release(key)

        terminal = [
            update for update in updates
            if update.get("status") in {"rendered", "cancelled", "error"}
        ]
        self.assertEqual(terminal[-1]["status"], "error")
        self.assertIn("stopped advancing", terminal[-1]["error"])
        self.assertEqual(terminal[-1]["message"], "Render gagal")


if __name__ == "__main__":
    unittest.main()
