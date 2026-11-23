"""Focused regression tests for Clipper Pro engine upgrades."""
from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch

from app import editor, framing
from app.analyze import (
    Candidate,
    Sentence,
    _clean_title,
    _ending_quality,
    candidate_pool_target,
    refine_with_gemini,
    refine_with_llm,
    select_candidate_subset,
    target_clip_count,
)


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

    def test_ai_default_render_has_hook_overlay_and_caption_fallback(self) -> None:
        clip = {
            "start": 5.0,
            "duration": 10.0,
            "title": "Rahasia besar yang jarang diketahui",
            "hook": "Ini rahasia besar yang jarang diketahui",
            "meta": {
                "origin": "ai",
                "transcript": "Ini rahasia besar yang jarang diketahui semua orang.",
            },
        }
        settings = {
            "captions": True,
            "hook_overlay": False,
            "ai_hook_overlay": True,
            "auto_framing": False,
            "layout": "fit",
        }

        spec = editor.default_spec(clip, settings, transcript=None, source_duration=30.0)

        self.assertTrue(spec["captions"]["enabled"])
        self.assertTrue(spec["subtitles"])
        self.assertTrue(any(item.get("id") == "hook" for item in spec["texts"]))
        self.assertEqual(next(item for item in spec["texts"] if item["id"] == "hook")["hook_source"], "title")

        manual_clip = {**clip, "meta": {**clip["meta"], "origin": "manual", "manual": True}}
        manual_spec = editor.default_spec(manual_clip, settings, transcript=None, source_duration=30.0)
        self.assertFalse(any(item.get("id") == "hook" for item in manual_spec["texts"]))

    def test_existing_unedited_ai_spec_recovers_missing_auto_text(self) -> None:
        clip = {
            "start": 0.0,
            "duration": 10.0,
            "title": "Rahasia usaha",
            "hook": "Ini rahasianya",
            "meta": {"origin": "ai", "transcript": "Ini rahasianya, ikuti langkah sederhana."},
        }
        settings = {
            "captions": True,
            "hook_overlay": False,
            "ai_hook_overlay": True,
            "auto_framing": False,
            "layout": "fit",
        }
        clip["spec"] = json.dumps({
            "version": 1,
            "segments": [{"start": 0.0, "end": 10.0, "speed": 1.0}],
            "captions": {"enabled": False},
            "subtitles": [],
            "texts": [],
        })

        spec = editor.load_or_create_spec(clip, transcript=None, source_duration=10.0, settings=settings)

        self.assertTrue(spec["captions"]["enabled"])
        self.assertTrue(spec["subtitles"])
        self.assertTrue(any(item.get("id") == "hook" for item in spec["texts"]))

    def test_punch_in_subtitles_map_from_output_time_per_segment(self) -> None:
        spec = {
            "punch_in": True,
            "subtitles": [{"start": 18.0, "end": 20.2, "text": "Ini rahasia penting"}],
        }

        intervals = editor._punch_in_intervals(spec, seg_out_start=10.0, seg_duration=12.0)

        self.assertEqual(intervals[0], (0.15, 2.35))
        self.assertIn((8.0, 10.2), intervals)

    def test_ai_pool_is_wider_only_when_a_ranker_is_configured(self) -> None:
        self.assertEqual(target_clip_count({"target_clips": 5}, 3600), 10)
        self.assertEqual(target_clip_count({"target_clips": 3}, 3600), 3)
        self.assertEqual(candidate_pool_target({"target_clips": 4}, 4), 4)
        self.assertEqual(
            candidate_pool_target({"gemini_enabled": True, "gemini_api_key": "test-gemini-key-12345"}, 4),
            8,
        )
        self.assertEqual(
            candidate_pool_target({"llm_enabled": True, "llm_url": "http://localhost:11434"}, 4),
            8,
        )
        self.assertEqual(
            candidate_pool_target({"gemini_enabled": True, "gemini_api_key": "short"}, 12),
            12,
        )

    def test_candidate_reranking_prefers_distinct_non_overlapping_moments(self) -> None:
        def make_candidate(start: float, score: float, text: str) -> Candidate:
            sentence = Sentence(start, start + 5.0, text)
            return Candidate(
                start=start,
                end=start + 20.0,
                score=score,
                text=text,
                title=text,
                hook="",
                description="",
                hashtags="#Shorts",
                sentences=[sentence],
            )

        first = make_candidate(0.0, 90.0, "Pekerjaan pertama memang sulit untuk semua orang.")
        duplicate = make_candidate(25.0, 88.0, "Pekerjaan pertama memang sulit untuk semua orang.")
        distinct = make_candidate(50.0, 75.0, "Berikut alasan berbeda kenapa usaha gagal total.")

        selected = select_candidate_subset([first, duplicate, distinct], 2)

        self.assertEqual(selected, [first, distinct])
        self.assertEqual(first.features["opening_novelty"], 1.0)
        self.assertEqual(first.features["selection_score"], 90.0)

        # A model's reranked scores can change which moments survive final selection.
        first.score, duplicate.score, distinct.score = 10.0, 95.0, 90.0
        reranked = select_candidate_subset([first, duplicate, distinct], 2)
        self.assertEqual(reranked, [duplicate, distinct])

    def test_local_ending_quality_flags_cutoff_clauses_and_fillers(self) -> None:
        self.assertEqual(_ending_quality("Inilah alasan utamanya."), 1.0)
        self.assertEqual(_ending_quality("Kita harus berhenti karena"), 0.0)
        self.assertEqual(_ending_quality("Terus kita lanjut gitu."), 0.0)
        self.assertEqual(_ending_quality("Ceritanya masih berjalan"), 0.55)
        self.assertLessEqual(len(_clean_title("Sebuah judul yang sangat panjang " * 6, 60)), 60)

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

        with patch("app.analyze.urllib.request.urlopen", return_value=BytesIO(response_body)) as mocked_urlopen:
            updated = refine_with_gemini(
                [candidate], {"gemini_enabled": True, "gemini_api_key": "test-gemini-key-12345"}
            )

        request = mocked_urlopen.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        prompt = request_body["contents"][0]["parts"][0]["text"]
        self.assertIn("transkrip satu-satunya sumber fakta", prompt)
        self.assertIn("konteks yang hilang", prompt)
        self.assertEqual(request_body["generationConfig"]["temperature"], 0.25)
        self.assertLessEqual(len(candidate.title), 60)
        self.assertTrue(updated)
        self.assertEqual(candidate.title, generated[0]["judul"])
        self.assertEqual(candidate.hook, generated[0]["hook"])
        self.assertAlmostEqual(candidate.score, 79.5)
        self.assertIn("#Podcast", candidate.hashtags)
        self.assertEqual(candidate.features["gemini"], 1.0)

    def test_local_llm_refinement_scores_candidates_and_handles_empty_description(self) -> None:
        candidate = Candidate(
            start=0.0, end=25.0, score=60.0,
            text="Penjelasan contoh tentang cara mengelola usaha kecil.",
            title="Clip 1", hook="", description="", hashtags="#Shorts",
        )
        generated = [{
            "i": 0,
            "judul": "Cara Mengelola Usaha Kecil",
            "hook": "Mulai dari langkah sederhana",
            "viral_score": 80,
            "alasan": "Klip berisi satu saran yang bisa langsung dipakai.",
            "hashtags": ["#Usaha", "#Shorts", "#usaha"],
        }]
        response_body = json.dumps({"response": json.dumps(generated)}).encode("utf-8")

        with (
            patch("app.analyze.ollama_available", return_value=True),
            patch("app.analyze.urllib.request.urlopen", return_value=BytesIO(response_body)) as mocked_urlopen,
        ):
            refine_with_llm(
                [candidate],
                {"gemini_enabled": False, "llm_enabled": True, "llm_url": "http://ollama", "llm_model": "test"},
            )

        request = mocked_urlopen.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertIn("skor relatif 0–100", request_body["prompt"])
        self.assertEqual(candidate.title, generated[0]["judul"])
        self.assertEqual(candidate.description, generated[0]["judul"])
        self.assertEqual(candidate.score, 73.0)
        self.assertEqual(candidate.hashtags, "#Usaha #Shorts")
        self.assertEqual(candidate.features["llm"], 1.0)


if __name__ == "__main__":
    unittest.main()
