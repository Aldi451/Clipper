"""Focused regression tests for Clipper Pro engine upgrades."""
from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch

from app import editor, framing
from app.analyze import Candidate, refine_with_gemini


class ProEngineTests(unittest.TestCase):
    def test_split_speaker_detector_finds_coexisting_faces(self) -> None:
        records = []
        for at in range(6):
            records.extend([
                {"at": float(at), "live": 1.0, "nw": 0.12, "nx": 0.27, "ny": 0.42},
                {"at": float(at), "live": 0.9, "nw": 0.11, "nx": 0.73, "ny": 0.44},
            ])

        result = framing._evaluate_split_speakers(records, total_frames=6)

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["dual_frames"], 6)
        self.assertAlmostEqual(result["coexistence"], 1.0)
        self.assertLess(result["left_x"], result["right_x"])

    def test_split_filtergraph_stacks_two_centered_panels(self) -> None:
        chain = editor._layout_chain(
            "split", 0.5, {"type": "color"}, 30,
            split_spec={"split": 0.5, "left_x": 0.27, "left_y": 0.42,
                        "right_x": 0.73, "right_y": 0.44},
        )

        self.assertIn("[topsrc]", chain)
        self.assertIn("[botsrc]", chain)
        self.assertIn("vstack=inputs=2", chain)
        self.assertTrue(chain.endswith("[vbase]"))

    def test_punch_in_subtitles_map_from_output_time_per_segment(self) -> None:
        spec = {
            "punch_in": True,
            "subtitles": [{"start": 18.0, "end": 20.2, "text": "Ini rahasia penting"}],
        }

        intervals = editor._punch_in_intervals(spec, seg_out_start=10.0, seg_duration=12.0)

        self.assertEqual(intervals[0], (0.15, 2.35))
        self.assertIn((8.0, 10.2), intervals)

    def test_gemini_refinement_updates_candidate_and_blends_score(self) -> None:
        candidate = Candidate(
            start=0.0, end=25.0, score=60.0,
            text="Transkrip contoh tentang rahasia penting.",
            title="Judul lama", hook="Hook lama",
            description="Judul lama\nKeterangan lama", hashtags="#shorts",
        )
        generated = [{
            "i": 0,
            "judul": "Rahasia Penting yang Jarang Diketahui",
            "hook": "Ini rahasia penting",
            "viral_score": 90,
            "alasan": "Pembuka menjanjikan informasi bernilai.",
            "hashtags": ["#Podcast", "shorts"],
        }]
        response_body = json.dumps({
            "candidates": [{"content": {"parts": [{"text": json.dumps(generated)}]}}],
        }).encode("utf-8")

        with patch("app.analyze.urllib.request.urlopen", return_value=BytesIO(response_body)):
            updated = refine_with_gemini(
                [candidate], {"gemini_enabled": True, "gemini_api_key": "test-gemini-key-12345"}
            )

        self.assertTrue(updated)
        self.assertEqual(candidate.title, generated[0]["judul"])
        self.assertEqual(candidate.hook, generated[0]["hook"])
        self.assertAlmostEqual(candidate.score, 79.5)
        self.assertIn("#Podcast", candidate.hashtags)
        self.assertEqual(candidate.features["gemini"], 1.0)


if __name__ == "__main__":
    unittest.main()
