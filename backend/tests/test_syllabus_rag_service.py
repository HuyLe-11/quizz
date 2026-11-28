"""Mocked tests for syllabus RAG preparation and Supabase persistence."""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import UUID

import httpx

from backend.cloud_repository import (
    CloudRepository,
    CloudRepositoryError,
    CloudRepositoryHTTPError,
)
from backend.rag.syllabus_ingestion import (
    EMBEDDING_DIMENSIONS,
    EmbeddedSyllabusChunk,
    SyllabusIngestionResult,
)
from backend.rag.syllabus_parser import ExtractedPage
from backend.rag.syllabus_rag_service import (
    PreparedSyllabus,
    SyllabusRAGError,
    SyllabusRAGService,
)


USER_ID = "11111111-1111-4111-8111-111111111111"
DOC_ID = "22222222-2222-4222-8222-222222222222"
BASE_URL = "https://example.supabase.co"
PDF = b"%PDF-mocked"
VECTOR = [1.0 / (EMBEDDING_DIMENSIONS ** 0.5)] * EMBEDDING_DIMENSIONS


def make_prepared(document_id=DOC_ID):
    content = "Syllabus learning outcomes."
    chunk = EmbeddedSyllabusChunk(
        chunk_index=0,
        content=content,
        source_pages=(1, 2),
        section_heading="Learning outcomes",
        character_count=len(content),
        token_estimate=5,
        embedding=tuple(VECTOR),
    )
    ingestion = SyllabusIngestionResult(
        source_name="course.pdf",
        pages=(ExtractedPage(1, content), ExtractedPage(2, "More syllabus")),
        chunks=(chunk,),
        embedding_model="gemini-embedding-001",
        embedding_dimensions=EMBEDDING_DIMENSIONS,
    )
    return PreparedSyllabus(
        document_id=document_id,
        title="Course syllabus",
        subject="Biology",
        source_file="course.pdf",
        pdf_contents=PDF,
        ingestion=ingestion,
        metadata={"origin": "test"},
    )


class SyllabusRepositoryTests(unittest.TestCase):
    def repository(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return CloudRepository(
            USER_ID,
            "mock-user-token",
            client=client,
            base_url=BASE_URL,
            publishable_key="mock-publishable-key",
        )

    def test_upload_and_atomic_rpc_payload_and_retrieval(self):
        seen = []

        def handler(request):
            seen.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer mock-user-token")
            self.assertEqual(request.headers["apikey"], "mock-publishable-key")
            if request.url.path.startswith("/storage/v1/object/quizpdf-private/"):
                self.assertIn(f"{USER_ID}/syllabi/{DOC_ID}/course.pdf", request.url.path)
                self.assertEqual(request.content, PDF)
                return httpx.Response(200, json={"Key": "uploaded"})
            if request.url.path.endswith("quizpdf_create_syllabus_bundle"):
                import json
                body = json.loads(request.content)
                self.assertEqual(body["p_document"]["id"], DOC_ID)
                self.assertEqual(body["p_document"]["source_path"],
                                 f"{USER_ID}/syllabi/{DOC_ID}/course.pdf")
                self.assertNotIn("owner_id", body["p_document"])
                self.assertEqual(
                    set(body["p_chunks"][0]),
                    {"chunk_index", "content", "source_pages", "section_heading",
                     "character_count", "token_estimate", "embedding", "metadata"},
                )
                self.assertEqual(body["p_chunks"][0]["source_pages"], [1, 2])
                self.assertEqual(len(body["p_chunks"][0]["embedding"]), 1536)
                return httpx.Response(200, json={"document_id": DOC_ID, "chunk_count": 1})
            if request.url.path.endswith("quizpdf_match_syllabus_chunks"):
                import json
                body = json.loads(request.content)
                self.assertEqual(
                    set(body),
                    {"query_embedding", "p_match_count", "p_subject", "p_document_id"},
                )
                self.assertEqual(len(body["query_embedding"]), 1536)
                self.assertEqual(body["p_match_count"], 4)
                return httpx.Response(200, json=[{"chunk_id": "chunk-1", "similarity": 0.9}])
            return httpx.Response(404)

        with self.repository(handler) as repo:
            saved = repo.save_syllabus_bundle(
                {"id": DOC_ID, "title": "Course syllabus", "source_file": "course.pdf"},
                [{"chunk_index": 0, "content": "Syllabus learning outcomes.",
                  "source_pages": [1, 2], "section_heading": "Learning outcomes",
                  "character_count": len("Syllabus learning outcomes."), "token_estimate": 5,
                  "embedding": VECTOR}],
                PDF,
            )
            matches = repo.match_syllabus_chunks(VECTOR, match_count=4)
        self.assertEqual(saved["document_id"], DOC_ID)
        self.assertEqual(saved["chunk_count"], 1)
        self.assertEqual(matches[0]["similarity"], 0.9)
        self.assertEqual(len(seen), 3)

    def test_repository_and_migration_contract_names_and_storage_prefix_match(self):
        with self.repository(lambda request: httpx.Response(404)) as repo:
            object_path = repo._object_path("syllabi", f"{DOC_ID}/course.pdf")
        self.assertEqual(object_path, f"{USER_ID}/syllabi/{DOC_ID}/course.pdf")

        migration = Path(__file__).parents[2] / "supabase" / "migrations" / "20261127_quizpdf_syllabus_rag.sql"
        sql = migration.read_text(encoding="utf-8")
        self.assertIn("v_owner::text || '/syllabi/' || v_document_id::text || '/'", sql)
        self.assertIn("public.quizpdf_create_syllabus_bundle(\n    p_document jsonb,\n    p_chunks jsonb", sql)
        self.assertIn('"p_document": document_data, "p_chunks": chunk_data',
                      Path(__file__).parents[1].joinpath("cloud_repository.py").read_text(encoding="utf-8"))
        self.assertIn("embedding extensions.vector(1536)", sql)
        self.assertIn("query_embedding extensions.vector(1536)", sql)
        self.assertIn("extensions.vector_norm(v_embedding) = 0", sql)
        self.assertIn("v_existing.processing_status <> v_processing_status", sql)
        self.assertIn("jsonb_typeof(v_chunk->'source_pages') is distinct from 'array'", sql)
        self.assertIn("jsonb_typeof(v_chunk->'embedding') is distinct from 'array'", sql)
        self.assertIn("source_pages must be an array", sql)
        self.assertIn("embedding must be an array", sql)

    def test_document_listing_and_lookup_use_user_jwt_and_uuid_filter(self):
        seen = []

        def handler(request):
            seen.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer mock-user-token")
            self.assertEqual(request.headers["apikey"], "mock-publishable-key")
            if request.url.params.get("id") == f"eq.{DOC_ID}":
                self.assertIn("syllabus_chunks(count)", request.url.params["select"])
                return httpx.Response(200, json=[{"id": DOC_ID, "processing_status": "completed"}])
            self.assertEqual(request.url.params["order"], "created_at.desc")
            self.assertNotIn("owner_id", request.url.params)
            return httpx.Response(200, json=[{"id": DOC_ID, "title": "Owned"}])

        with self.repository(handler) as repo:
            documents = repo.list_syllabus_documents()
            document = repo.get_syllabus_document(DOC_ID)
        self.assertEqual(documents[0]["id"], DOC_ID)
        self.assertEqual(document["id"], DOC_ID)
        self.assertEqual(len(seen), 2)

    def test_rejects_wrong_owner_and_bad_vectors_before_network(self):
        requests = []
        with self.repository(lambda request: (requests.append(request), httpx.Response(500))[1]) as repo:
            with self.assertRaisesRegex(ValueError, "authenticated user"):
                repo.save_syllabus_bundle(
                    {"id": DOC_ID, "owner_id": "33333333-3333-4333-8333-333333333333",
                     "title": "Title", "source_file": "course.pdf"},
                    [{"chunk_index": 0, "content": "x", "source_pages": [1],
                      "character_count": 1, "embedding": VECTOR}], PDF,
                )
            with self.assertRaisesRegex(ValueError, "1536"):
                repo.match_syllabus_chunks([0.1] * 8)
            non_finite = list(VECTOR)
            non_finite[0] = float("inf")
            with self.assertRaisesRegex(ValueError, "finite"):
                repo.match_syllabus_chunks(non_finite)
        self.assertEqual(requests, [])

    def test_storage_failure_prevents_persistence(self):
        paths = []
        secret_marker = "PRIVATE-RESPONSE-DETAIL"

        def handler(request):
            paths.append(request.url.path)
            return httpx.Response(403, json={
                "code": "42501",
                "message": f"denied '{secret_marker}' Bearer eyJabc.def.ghi sb_secret_abcdefghijklmnop",
            })

        with self.repository(handler) as repo:
            with self.assertLogs("quizpdf", level="INFO") as captured:
                with self.assertRaises(CloudRepositoryHTTPError) as raised:
                    repo.save_syllabus_bundle(
                        {"id": DOC_ID, "title": "Title", "source_file": "course.pdf"},
                        [{"chunk_index": 0, "content": "x", "source_pages": [1],
                          "character_count": 1, "embedding": VECTOR}], PDF,
                    )
        self.assertEqual(len(paths), 1)
        self.assertNotIn("quizpdf_create_syllabus_bundle", paths[0])
        logs = "\n".join(captured.output)
        self.assertIn(f"stage=storage_upload status=failed document_id={DOC_ID}", logs)
        self.assertIn("http_status=403", logs)
        self.assertIn("error_code=42501", logs)
        self.assertNotIn(secret_marker, logs)
        self.assertNotIn("eyJabc.def.ghi", logs)
        self.assertNotIn("sb_secret_abcdefghijklmnop", logs)
        self.assertNotIn("mock-user-token", str(raised.exception))

    def test_definite_database_failure_cleans_uploaded_storage(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            if request.method == "POST" and "/storage/v1/object/" in request.url.path:
                return httpx.Response(200, json={"Key": "uploaded"})
            if request.url.path.endswith("quizpdf_create_syllabus_bundle"):
                return httpx.Response(422, json={"message": "invalid"})
            if request.method == "DELETE":
                return httpx.Response(200, json={"message": "removed"})
            return httpx.Response(404)

        with self.repository(handler) as repo:
            with self.assertLogs("quizpdf", level="INFO") as captured:
                with self.assertRaises(CloudRepositoryHTTPError):
                    repo.save_syllabus_bundle(
                        {"id": DOC_ID, "title": "Title", "source_file": "course.pdf"},
                        [{"chunk_index": 0, "content": "x", "source_pages": [1],
                          "character_count": 1, "embedding": VECTOR}], PDF,
                    )
        self.assertTrue(any(path.endswith("quizpdf_create_syllabus_bundle") for path in paths))
        self.assertTrue(any(path == "/storage/v1/object/quizpdf-private" for path in paths))
        logs = "\n".join(captured.output)
        self.assertIn("stage=rpc_persist status=started", logs)
        self.assertIn("stage=rpc_persist status=failed", logs)
        self.assertIn("http_status=422", logs)
        self.assertIn("stage=storage_cleanup status=completed", logs)
        self.assertIn(f"document_id={DOC_ID}", logs)

    def test_ambiguous_rpc_outcome_preserves_file_and_safe_retry_uses_same_ids(self):
        rpc_calls = 0
        requests = []

        def handler(request):
            nonlocal rpc_calls
            requests.append(request)
            path = request.url.path
            if request.method == "POST" and "/storage/v1/object/" in path:
                return httpx.Response(200, json={"Key": "uploaded"})
            if request.method == "GET" and "/storage/v1/object/authenticated/" in path:
                return httpx.Response(200, content=PDF)
            if path.endswith("quizpdf_create_syllabus_bundle"):
                rpc_calls += 1
                if rpc_calls == 1:
                    raise httpx.ConnectError("response lost", request=request)
                return httpx.Response(200, json={"document_id": DOC_ID, "chunk_count": 1,
                                                "already_persisted": True})
            return httpx.Response(404)

        with self.repository(handler) as repo:
            args = (
                {"id": DOC_ID, "title": "Title", "source_file": "course.pdf"},
                [{"chunk_index": 0, "content": "x", "source_pages": [1],
                  "character_count": 1, "embedding": VECTOR}],
                PDF,
            )
            with self.assertRaises(CloudRepositoryError):
                repo.save_syllabus_bundle(*args)
            self.assertFalse(any(request.method == "DELETE" for request in requests))
            result = repo.save_syllabus_bundle(*args, resume_existing=True)
        self.assertEqual(result["document_id"], DOC_ID)
        self.assertEqual(rpc_calls, 2)
        self.assertEqual(UUID(result["document_id"]).version, 4)
        self.assertTrue(any(request.method == "GET" for request in requests))


class SyllabusRAGServiceTests(unittest.TestCase):
    class FakeIngestion:
        def __init__(self):
            self.sources = []

        def ingest(self, source, *, source_name=None):
            self.sources.append((source, source_name))
            return make_prepared().ingestion

    class FakeRepository:
        def __init__(self, error=None):
            self.calls = []
            self.error = error

        def save_syllabus_bundle(self, document, chunks, pdf_contents, *, resume_existing=False):
            self.calls.append((document, chunks, pdf_contents, resume_existing))
            if self.error:
                raise self.error
            return {"document_id": document["id"],
                    "source_path": f"{USER_ID}/syllabi/{document['id']}/course.pdf",
                    "chunk_count": len(chunks)}

    def test_prepares_and_persists_full_result(self):
        repo = self.FakeRepository()
        ingestion = self.FakeIngestion()
        service = SyllabusRAGService(repo, ingestion_service=ingestion)
        prepared = service.prepare(PDF, title="Course", subject="Biology", document_id=DOC_ID)
        result = service.persist(prepared)
        self.assertEqual(result.document_id, DOC_ID)
        self.assertEqual(result.chunk_count, 1)
        self.assertEqual(repo.calls[0][0]["metadata"]["embedding_dimensions"], 1536)
        self.assertEqual(repo.calls[0][1][0]["source_pages"], [1, 2])
        self.assertEqual(repo.calls[0][2], PDF)
        self.assertEqual(ingestion.sources[0][0], PDF)

    def test_repository_failure_is_not_reported_as_success_and_prepared_can_retry(self):
        repo = self.FakeRepository(error=RuntimeError("network"))
        service = SyllabusRAGService(repo, ingestion_service=self.FakeIngestion())
        prepared = service.prepare(PDF, title="Course", document_id=DOC_ID)
        with self.assertRaises(SyllabusRAGError) as raised:
            service.persist(prepared)
        self.assertIs(raised.exception.prepared, prepared)
        repo.error = None
        result = service.persist(raised.exception.prepared, resume_existing=True)
        self.assertEqual(result.document_id, DOC_ID)
        self.assertEqual(repo.calls[0][0]["id"], repo.calls[1][0]["id"])


if __name__ == "__main__":
    unittest.main()
