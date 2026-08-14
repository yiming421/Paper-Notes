import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import update_citation_leaderboard as leaderboard


class FakeClient:
    def __init__(self, papers):
        self.papers = papers
        self.requested_batches = []

    def batch_papers(self, identifiers):
        self.requested_batches.append(list(identifiers))
        return [self.papers.get(identifier) for identifier in identifiers]


class InvalidBatchClient(leaderboard.SemanticScholarClient):
    def request(self, *args, **kwargs):
        raise leaderboard.SemanticScholarError(
            'Semantic Scholar HTTP 400 for paper/batch: {"error":"No valid paper ids given"}'
        )


class CitationLeaderboardTests(unittest.TestCase):
    def test_scan_note_extracts_title_and_identifiers(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary)
            note_dir = docs / "ACL2025" / "llm_agent"
            note_dir.mkdir(parents=True)
            (note_dir / "example.md").write_text(
                """---
title: metadata title
---

# Think, Then Verify: A Test

**链接**: [ACL Anthology](https://aclanthology.org/2025.acl-long.605/)
**arXiv**: [2501.12345](https://arxiv.org/abs/2501.12345v2)
""",
                encoding="utf-8",
            )

            notes = leaderboard.scan_notes(docs)

        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].title, "Think, Then Verify: A Test")
        self.assertEqual(notes[0].conference_year, 2025)
        self.assertEqual(
            notes[0].dois,
            (
                "10.18653/v1/2025.acl-long.605",
                "10.48550/arxiv.2501.12345",
            ),
        )

    def test_title_normalization_handles_markup_and_typography(self):
        left = "C$^2$FG: Control–Classifier-Free Guidance"
        right = "C2FG: Control-Classifier Free Guidance"
        self.assertEqual(
            leaderboard.normalize_title(left), leaderboard.normalize_title(right)
        )

    def test_semantic_scholar_identifiers_use_native_prefixes(self):
        self.assertEqual(
            leaderboard.semantic_scholar_identifier("10.48550/arxiv.2402.13616"),
            "ARXIV:2402.13616",
        )
        self.assertEqual(
            leaderboard.semantic_scholar_identifier("10.18653/v1/2025.acl-long.1"),
            "DOI:10.18653/v1/2025.acl-long.1",
        )

    def test_published_exact_match_wins_over_preprint(self):
        candidates = [
            {
                "paperId": "1" * 40,
                "title": "Interleaved-Modal Chain-of-Thought",
                "year": 2024,
                "externalIds": {
                    "ArXiv": "2411.19488",
                    "DOI": "10.48550/arxiv.2411.19488",
                },
                "citationCount": 50,
            },
            {
                "paperId": "2" * 40,
                "title": "Interleaved-Modal Chain-of-Thought",
                "year": 2025,
                "externalIds": {"DOI": "10.1109/cvpr.2025.1"},
                "citationCount": 8,
            },
        ]

        selected = leaderboard.choose_paper(
            leaderboard.normalize_title("Interleaved-Modal Chain-of-Thought"),
            2025,
            candidates,
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected[0]["paperId"], "2" * 40)
        self.assertEqual(selected[1], "title_exact")

    def test_fuzzy_match_rejects_materially_different_title(self):
        selected = leaderboard.choose_paper(
            leaderboard.normalize_title("Learning Robust Visual Representations"),
            2025,
            [
                {
                    "paperId": "3" * 40,
                    "title": "Learning Visual Representations for Robotics",
                    "year": 2025,
                }
            ],
        )
        self.assertIsNone(selected)

    def test_identifier_lookup_rejects_wrong_upstream_record_then_uses_arxiv(self):
        note = leaderboard.Note(
            path="ACL2025/llm/example.md",
            title="Smarter, Better, Faster, Longer: A Modern Bidirectional Encoder",
            title_key=leaderboard.normalize_title(
                "Smarter, Better, Faster, Longer: A Modern Bidirectional Encoder"
            ),
            conference="ACL2025",
            area="llm",
            conference_year=2025,
            dois=("10.1234/wrong", "10.48550/arxiv.2412.13663"),
        )
        correct_id = "a" * 40
        client = FakeClient(
            {
                "DOI:10.1234/wrong": {
                    "paperId": "b" * 40,
                    "title": "A Comparative Clinical Study on Another Dataset",
                    "year": 2024,
                },
                "ARXIV:2412.13663": {
                    "paperId": correct_id,
                    "title": (
                        "Smarter, Better, Faster, Longer: A Modern Bidirectional Encoder"
                    ),
                    "year": 2024,
                    "citationCount": 500,
                    "influentialCitationCount": 20,
                    "externalIds": {"ArXiv": "2412.13663"},
                },
            }
        )
        cache = leaderboard.empty_cache()

        matched = leaderboard.resolve_identifiers(
            client,
            cache,
            [note],
            attempted_on="2026-08-13",
            retrieved_at="2026-08-13T00:00:00Z",
        )

        self.assertEqual(matched, 1)
        self.assertEqual(cache["matches"][note.path]["paper_id"], correct_id)
        self.assertEqual(cache["matches"][note.path]["match_method"], "arxiv")
        self.assertEqual(len(client.requested_batches), 2)

    def test_identifier_match_accepts_versioned_title(self):
        selected = leaderboard.choose_paper(
            leaderboard.normalize_title(
                "AdaptiveAD: Decoupling Scene Perception and Ego Status for End-to-End Autonomous Driving"
            ),
            2026,
            [
                {
                    "paperId": "c" * 40,
                    "title": (
                        "Decoupling Scene Perception and Ego Status: A Multi-Context "
                        "Fusion Approach for Enhanced Generalization in End-to-End Autonomous Driving"
                    ),
                    "year": 2025,
                }
            ],
            identifier_match=True,
        )
        self.assertIsNotNone(selected)

    def test_identifier_match_uses_english_slug_for_translated_h1(self):
        note = leaderboard.Note(
            path="ICML2026/ai_safety/understanding_generalization_and_forgetting_in_in-context_continual_learning.md",
            title="理解上下文连续学习中的泛化与遗忘",
            title_key=leaderboard.normalize_title("理解上下文连续学习中的泛化与遗忘"),
            conference="ICML2026",
            area="ai_safety",
            conference_year=2026,
            dois=("10.48550/arxiv.2601.00001",),
        )
        selected = leaderboard.choose_identifier_paper(
            note,
            [
                {
                    "paperId": "d" * 40,
                    "title": (
                        "Understanding Generalization and Forgetting in "
                        "In-Context Continual Learning"
                    ),
                    "year": 2026,
                }
            ],
        )
        self.assertIsNotNone(selected)
        self.assertEqual(
            leaderboard.note_title_query(note),
            "understanding generalization and forgetting in in-context continual learning",
        )
        self.assertIsNotNone(
            leaderboard.choose_title_paper(
                note,
                [
                    {
                        "paperId": "d" * 40,
                        "title": (
                            "Understanding Generalization and Forgetting in "
                            "In-Context Continual Learning"
                        ),
                        "year": 2026,
                    }
                ],
            )
        )

    def test_batch_size_matches_semantic_scholar_limit(self):
        notes = [
            leaderboard.Note(
                path=f"CVPR2025/area/paper_{index}.md",
                title=f"Paper {index}",
                title_key=f"paper {index}",
                conference="CVPR2025",
                area="area",
                conference_year=2025,
                dois=(f"10.1234/{index}",),
            )
            for index in range(1001)
        ]
        client = FakeClient({})

        leaderboard.resolve_identifiers(
            client,
            leaderboard.empty_cache(),
            notes,
            attempted_on="2026-08-13",
            retrieved_at="2026-08-13T00:00:00Z",
        )

        self.assertEqual([len(batch) for batch in client.requested_batches], [500, 500, 1])

    def test_all_invalid_identifier_batch_is_treated_as_unmatched(self):
        client = InvalidBatchClient(request_interval=0)
        self.assertEqual(client.batch_papers(["ARXIV:invalid"]), [None])

    def test_public_data_deduplicates_and_exposes_influential_count(self):
        notes = [
            leaderboard.Note(
                path="CVPR2025/area_a/paper.md",
                title="One Paper",
                title_key="one paper",
                conference="CVPR2025",
                area="area_a",
                conference_year=2025,
                dois=(),
            ),
            leaderboard.Note(
                path="CVPR2025/area_b/paper.md",
                title="One Paper",
                title_key="one paper",
                conference="CVPR2025",
                area="area_b",
                conference_year=2025,
                dois=(),
            ),
        ]
        paper_id = "1" * 40
        cache = leaderboard.empty_cache()
        cache["matches"] = {
            note.path: {
                "title_key": note.title_key,
                "paper_id": paper_id,
                "match_method": "title_exact",
            }
            for note in notes
        }
        cache["papers"] = {
            paper_id: {
                "title": "One Paper",
                "citation_count": 12,
                "influential_citation_count": 4,
                "year": 2025,
                "doi": None,
                "url": f"https://www.semanticscholar.org/paper/{paper_id}",
            }
        }

        data = leaderboard.build_public_data(
            notes,
            cache,
            generated_at=dt.datetime(2026, 8, 13, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(data["source"], "Semantic Scholar")
        self.assertEqual(data["matched_notes"], 2)
        self.assertEqual(data["matched_works"], 1)
        self.assertEqual(data["papers"][0]["influential_citations"], 4)
        self.assertEqual(data["total_influential_citations"], 4)
        self.assertEqual(len(data["papers"][0]["notes"]), 2)
        self.assertEqual(json.loads(leaderboard.render_public_data(data))["version"], 2)


if __name__ == "__main__":
    unittest.main()
