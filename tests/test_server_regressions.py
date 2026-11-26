"""Regression tests for mode-selection and manual-editor API routes."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


if importlib.util.find_spec("fastapi") is not None:
    from app import server
else:  # Keep the lightweight, standard-library-only suite runnable without app deps.
    server = None  # type: ignore[assignment]


@unittest.skipIf(server is None, "FastAPI is not installed")
class ServerRegressionTests(unittest.TestCase):
    def test_start_manual_creates_a_clip_when_optional_framing_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.mp4"
            source_path.write_bytes(b"test source")
            job_id = "manual-editor-regression"
            job = {
                "id": job_id,
                "title": "Full source",
                "source_path": str(source_path),
                "duration": 30.0,
                "meta": {},
            }
            clip = {
                "id": "manual-clip-regression",
                "job_id": job_id,
                "start": 0.0,
                "duration": 20.0,
                "title": "Full source · Manual #1",
                "meta": {"origin": "manual", "manual": True},
            }

            # Build the route table without touching the caller's persistent DB.
            with (
                patch.object(server, "ensure_dirs"),
                patch.object(server.db, "init_db"),
                patch.object(server.db, "reset_jobs_on_startup"),
            ):
                app = server.create_app()
            endpoint = next(
                route.endpoint for route in app.routes
                if getattr(route, "path", "") == "/api/jobs/{job_id}/start-manual"
            )

            with (
                patch.object(server.db, "get_job", return_value=job),
                patch.object(server.db, "list_clips", return_value=[]),
                patch.object(server.paths, "resolve_media_path", return_value=source_path),
                patch.object(server.framing, "available", return_value=False),
                patch.object(server.transcribe, "load_transcript", return_value=None),
                patch.object(server.editor, "load_or_create_spec", return_value={"segments": []}),
                patch.object(server.db, "create_clip", return_value=clip),
                patch.object(server.db, "update_clip", return_value=clip),
                patch.object(server, "load_settings", return_value={"yt_hashtags": "#Shorts"}),
            ):
                result = endpoint(job_id, {"duration": 20})

        self.assertTrue(result["created"])
        self.assertEqual(result["active_clip_id"], clip["id"])
        self.assertTrue(result["clip"]["is_manual"])
        self.assertFalse(result["clip"]["has_file"])


if __name__ == "__main__":
    unittest.main()
