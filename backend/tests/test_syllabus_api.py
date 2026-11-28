"""Authenticated API contract tests for syllabus RAG routes."""

from __future__ import annotations

import os
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient

from backend.network.cloud_api import app, authenticated_identity
from backend.rag.gemini_service import GeminiAPIError
from backend.rag.question_generation import QuestionOutputError
from backend.rag.syllabus_ingestion import SyllabusEmbeddingError
from backend.rag.syllabus_rag_service import SyllabusRAGError, SyllabusRAGPersistenceError


USER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"


def fake_identity(request: Request):
    del request
    return USER_ID, "test-user-jwt"


def sample_document(document_id=OTHER_DOCUMENT_ID):
    return {
        "id": document_id,
        "title": "Cell biology",
        "subject": "Biology",
        "source_file": "course.pdf",
        "processing_status": "completed",
        "created_at": "2026-11-28T00:00:00Z",
        "updated_at": "2026-11-28T00:00:00Z",
        "metadata": {"embedding_dimensions": 1536},
        "source_path": f"{USER_ID}/syllabi/{document_id}/course.pdf",
    }


class SyllabusAPITests(unittest.TestCase):
    def setUp(self):
        self.original_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[authenticated_identity] = fake_identity
        self.env_patcher = patch.dict(os.environ, {"VERCEL": "1"})
        self.env_patcher.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_overrides)
        self.env_patcher.stop()

    @patch("backend.network.cloud_api.SyllabusRAGService")
    @patch("backend.network.cloud_api.CloudRepository")
    def test_authenticated_upload_success(self, mock_repository, mock_rag_service):
        repository = mock_repository.return_value.__enter__.return_value
        mock_rag_service.return_value.ingest_and_persist.return_value = SimpleNamespace(
            document_id=OTHER_DOCUMENT_ID,
            chunk_count=3,
        )
        response = self.client.post(
            "/api/syllabi",
            data={"title": "Cell biology", "subject": "Biology"},
            files={"file": ("course.pdf", b"%PDF-test", "application/pdf")},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload, {
            "document_id": OTHER_DOCUMENT_ID,
            "title": "Cell biology",
            "subject": "Biology",
            "status": "completed",
            "chunk_count": 3,
        })
        self.assertNotIn("embedding", response.text)
        mock_repository.assert_called_once_with(USER_ID, "test-user-jwt")
        mock_rag_service.assert_called_once_with(repository)
        mock_rag_service.return_value.ingest_and_persist.assert_called_once_with(
            b"%PDF-test",
            title="Cell biology",
            subject="Biology",
            source_file="course.pdf",
            document_id=None,
            metadata={"source_sha256": hashlib.sha256(b"%PDF-test").hexdigest()},
        )

    def test_unauthenticated_request_is_rejected(self):
        app.dependency_overrides.pop(authenticated_identity, None)
        response = self.client.get("/api/syllabi")
        self.assertEqual(response.status_code, 401)

    def test_unauthenticated_question_generation_is_rejected(self):
        app.dependency_overrides.pop(authenticated_identity, None)
        response = self.client.post(
            f"/api/syllabi/{OTHER_DOCUMENT_ID}/generate",
            json={"topic": "Cell respiration"},
        )
        self.assertEqual(response.status_code, 401)

    def test_upload_rejects_non_pdf_media_type(self):
        response = self.client.post(
            "/api/syllabi",
            data={"title": "Cell biology"},
            files={"file": ("course.pdf", b"not a pdf", "text/plain")},
        )
        self.assertEqual(response.status_code, 415)

    @patch("backend.network.cloud_api.CloudRepository")
    def test_invalid_pdf_is_rejected_without_gemini_or_supabase(self, mock_repository):
        response = self.client.post(
            "/api/syllabi",
            data={"title": "Broken PDF"},
            files={"file": ("broken.pdf", b"not a PDF", "application/pdf")},
        )
        self.assertIn(response.status_code, {400, 422})
        self.assertNotIn("embedding", response.text.lower())
        mock_repository.assert_called_once_with(USER_ID, "test-user-jwt")
        mock_repository.return_value.__enter__.return_value.save_syllabus_bundle.assert_not_called()

    @patch("backend.network.cloud_api.CloudRepository")
    def test_owner_scoped_list_and_cross_owner_detail_is_not_found(self, mock_repository):
        repository = mock_repository.return_value.__enter__.return_value
        repository.list_syllabus_documents.return_value = [sample_document()]
        repository.get_syllabus_document.return_value = None

        listing = self.client.get("/api/syllabi")
        cross_owner = self.client.get(f"/api/syllabi/{OTHER_DOCUMENT_ID}")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["data"][0]["document_id"], OTHER_DOCUMENT_ID)
        self.assertNotIn("source_path", listing.text)
        self.assertEqual(cross_owner.status_code, 404)
        self.assertEqual(repository.get_syllabus_document.call_args.args[0], OTHER_DOCUMENT_ID)
        self.assertTrue(all(call.args == (USER_ID, "test-user-jwt") for call in mock_repository.call_args_list))

    @patch("backend.network.cloud_api.SyllabusRAGService")
    @patch("backend.network.cloud_api.CloudRepository")
    def test_retry_with_document_id_returns_existing_identical_upload(
        self, mock_repository, mock_rag_service
    ):
        repository = mock_repository.return_value.__enter__.return_value
        repository.get_syllabus_document.return_value = {
            **sample_document(),
            "metadata": {"source_sha256": hashlib.sha256(b"%PDF-test").hexdigest()},
            "syllabus_chunks": [{"count": 3}],
        }
        response = self.client.post(
            "/api/syllabi",
            data={"title": "Cell biology", "subject": "Biology",
                  "document_id": OTHER_DOCUMENT_ID},
            files={"file": ("course.pdf", b"%PDF-test", "application/pdf")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["chunk_count"], 3)
        mock_rag_service.assert_not_called()

    @patch("backend.network.cloud_api._ingest_syllabus_sync")
    def test_persistence_failure_returns_retry_document_id_header(self, work):
        work.side_effect = SyllabusRAGPersistenceError(SimpleNamespace(document_id=OTHER_DOCUMENT_ID))
        response = self.client.post(
            "/api/syllabi",
            data={"title": "Cell biology"},
            files={"file": ("course.pdf", b"%PDF-test", "application/pdf")},
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.headers["x-syllabus-document-id"], OTHER_DOCUMENT_ID)

    @patch("backend.network.cloud_api.CloudRepository")
    def test_detail_returns_owned_metadata_and_status(self, mock_repository):
        repository = mock_repository.return_value.__enter__.return_value
        repository.get_syllabus_document.return_value = sample_document()

        response = self.client.get(f"/api/syllabi/{OTHER_DOCUMENT_ID}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["processing_status"], "completed")
        self.assertNotIn("source_path", response.text)
        repository.get_syllabus_document.assert_called_once_with(OTHER_DOCUMENT_ID)

    @patch("backend.network.cloud_api.GeminiService")
    @patch("backend.network.cloud_api.CloudRepository")
    def test_search_uses_retrieval_query_and_hides_vectors(self, mock_repository, mock_gemini):
        repository = mock_repository.return_value.__enter__.return_value
        repository.get_syllabus_document.return_value = sample_document()
        repository.match_syllabus_chunks.return_value = [{
            "chunk_id": "chunk-1",
            "chunk_index": 0,
            "content": "Mitochondria generate cellular energy.",
            "similarity": 0.91,
            "source_pages": [2],
            "section_heading": "Cell structures",
            "embedding": [0.1] * 1536,
        }]
        mock_gemini.return_value.embed_text.return_value = [0.1] * 1536

        response = self.client.post(
            f"/api/syllabi/{OTHER_DOCUMENT_ID}/search",
            json={"query": "What produces cellular energy?", "top_k": 5},
        )

        self.assertEqual(response.status_code, 200)
        mock_gemini.return_value.embed_text.assert_called_once_with(
            "What produces cellular energy?",
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=1536,
        )
        repository.match_syllabus_chunks.assert_called_once_with(
            [0.1] * 1536,
            match_count=5,
            document_id=OTHER_DOCUMENT_ID,
        )
        self.assertNotIn("embedding", response.text)
        self.assertEqual(response.json()["data"]["matches"][0]["source_pages"], [2])

    @patch("backend.network.cloud_api.GeminiService")
    @patch("backend.network.cloud_api.CloudRepository")
    def test_search_maps_gemini_quota_error_to_retryable_response(
        self, mock_repository, mock_gemini
    ):
        mock_repository.return_value.__enter__.return_value.get_syllabus_document.return_value = sample_document()
        mock_gemini.return_value.embed_text.side_effect = GeminiAPIError(
            "quota exceeded", status_code=429
        )
        response = self.client.post(
            f"/api/syllabi/{OTHER_DOCUMENT_ID}/search",
            json={"query": "Cell energy"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "30")
        self.assertNotIn("quota exceeded", response.text)

    @patch("backend.network.cloud_api._generate_syllabus_questions_sync")
    def test_generate_questions_returns_validated_questions_without_vectors(self, work):
        work.return_value = SimpleNamespace(
            document_id=OTHER_DOCUMENT_ID,
            document_title="Cell biology",
            topic="Cell respiration",
            status="generated",
            message=None,
            questions=(SimpleNamespace(
                question="What releases energy?",
                options=("Mitochondria", "Ribosome", "Golgi", "Nucleus"),
                correct_answer_index=0,
                explanation="The cited section describes cellular respiration.",
                difficulty="medium",
                grounding_type="source_supported",
                source_references=({"chunk_id": "chunk-1", "source_pages": [2]},),
            ),),
        )
        response = self.client.post(
            f"/api/syllabi/{OTHER_DOCUMENT_ID}/generate",
            json={"topic": "Cell respiration", "question_count": 1},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["status"], "generated")
        self.assertEqual(data["questions"][0]["correct_answer"], "A")
        self.assertEqual(data["questions"][0]["options"][0], {
            "label": "A", "content": "Mitochondria"
        })
        self.assertEqual(data["questions"][0]["source_references"][0]["source_pages"], [2])
        self.assertNotIn("embedding", response.text)
        work.assert_called_once()
        self.assertEqual(work.call_args.args[0], (USER_ID, "test-user-jwt"))
        self.assertEqual(work.call_args.args[1], OTHER_DOCUMENT_ID)

    @patch("backend.network.cloud_api._generate_syllabus_questions_sync")
    def test_generate_questions_hides_cross_owner_document(self, work):
        work.return_value = None
        response = self.client.post(
            f"/api/syllabi/{OTHER_DOCUMENT_ID}/generate",
            json={"topic": "Cell respiration"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Syllabus not found.")

    @patch("backend.network.cloud_api._generate_syllabus_questions_sync")
    def test_generate_questions_maps_gemini_quota_to_retryable_response(self, work):
        work.side_effect = GeminiAPIError("quota exceeded", status_code=429)
        response = self.client.post(
            f"/api/syllabi/{OTHER_DOCUMENT_ID}/generate",
            json={"topic": "Cell respiration"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "30")
        self.assertNotIn("quota exceeded", response.text)

    @patch("backend.network.cloud_api._generate_syllabus_questions_sync")
    def test_generate_questions_maps_invalid_model_output_to_502(self, work):
        work.side_effect = QuestionOutputError("Generated question lacks citation.")
        response = self.client.post(
            f"/api/syllabi/{OTHER_DOCUMENT_ID}/generate",
            json={"topic": "Cell respiration"},
        )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("lacks citation", response.text)

    def test_generate_questions_validates_request(self):
        for request in (
            {"topic": ""},
            {"topic": "Cells", "question_count": 11},
            {"topic": "Cells", "difficulty": "expert"},
            {"topic": "Cells", "top_k": 21},
        ):
            with self.subTest(request=request):
                response = self.client.post(
                    f"/api/syllabi/{OTHER_DOCUMENT_ID}/generate", json=request
                )
                self.assertEqual(response.status_code, 422)

    @patch("backend.network.cloud_api._ingest_syllabus_sync")
    def test_upload_maps_embedding_quota_error_without_leaking_provider_detail(self, work):
        provider_error = SyllabusEmbeddingError(
            "Gemini embedding failed for chunk 0 (HTTP 429). Retry later.",
            chunk_index=0,
            source_pages=(1,),
        )
        rag_error = SyllabusRAGError(str(provider_error))
        rag_error.__cause__ = provider_error
        work.side_effect = rag_error
        response = self.client.post(
            "/api/syllabi",
            data={"title": "Cell biology"},
            files={"file": ("course.pdf", b"%PDF-test", "application/pdf")},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "30")
        self.assertNotIn("embedding failed for chunk", response.text)

    @patch("backend.network.cloud_api._ingest_syllabus_sync")
    def test_upload_maps_repository_failure(self, work):
        prepared = SimpleNamespace(document_id=OTHER_DOCUMENT_ID)
        work.side_effect = SyllabusRAGPersistenceError(prepared)
        response = self.client.post(
            "/api/syllabi",
            data={"title": "Cell biology"},
            files={"file": ("course.pdf", b"%PDF-test", "application/pdf")},
        )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("test-user-jwt", response.text)


if __name__ == "__main__":
    unittest.main()
