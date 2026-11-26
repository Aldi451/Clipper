"""Regression tests for FFmpeg progress stalls and render terminal states."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app import media, pipeline
from app.cancellation import OperationCancelled


class RenderProgressTests(unittest.TestCase):
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
