import io
import os
import shutil
import sqlite3
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent / ".tmp_validation" / "issue12_tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATABASE_PATH", str(TEST_ROOT / "bootstrap.db"))
os.environ.setdefault("DOWNLOAD_DIR", str(TEST_ROOT / "bootstrap_uploads"))
os.environ.setdefault("CHROMA_PERSIST_DIR", str(TEST_ROOT / "bootstrap_chroma"))

import app as app_module
from document_processing import build_chunk_records
from embedding_processing import (
    DEFAULT_EMBEDDING_DIMENSION,
    EmbeddingGenerationError,
    build_embedding_payloads,
)
from vector_store import reset_vector_store_client


class StubEmbeddingProvider:
    def __init__(self, dimension: int = DEFAULT_EMBEDDING_DIMENSION):
        self.dimension = dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1)] * self.dimension for index, _ in enumerate(texts)]


class FailingEmbeddingProvider:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingGenerationError("Simulated embedding provider failure.")


class Issue12ImplementationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_dir = TEST_ROOT / "runtime"
        cls.base_dir.mkdir(parents=True, exist_ok=True)
        cls.database_path = cls.base_dir / "test.db"
        cls.download_dir = cls.base_dir / "uploads"
        cls.chroma_dir = cls.base_dir / "chroma"
        cls.download_dir.mkdir(parents=True, exist_ok=True)
        cls.chroma_dir.mkdir(parents=True, exist_ok=True)

        app_module.DATABASE = str(cls.database_path)
        app_module.DOWNLOAD_DIR = str(cls.download_dir)
        os.environ["CHROMA_PERSIST_DIR"] = str(cls.chroma_dir)
        os.makedirs(app_module.DOWNLOAD_DIR, exist_ok=True)
        app_module.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base_dir, ignore_errors=True)

    def setUp(self):
        reset_vector_store_client()

        if self.database_path.exists():
            self.database_path.unlink()

        if self.download_dir.exists():
            shutil.rmtree(self.download_dir)

        if self.chroma_dir.exists():
            shutil.rmtree(self.chroma_dir)

        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        app_module.CHUNK_REGISTRY.clear()
        app_module.UPLOAD_CHUNK_INDEX.clear()
        app_module.EMBEDDING_REGISTRY.clear()
        app_module.UPLOAD_EMBEDDING_INDEX.clear()
        app_module.EMBEDDING_PROVIDER = StubEmbeddingProvider(app_module.EMBEDDING_DIMENSION)
        app_module.init_db()
        self.client = app_module.app.test_client()

    def login_as_admin(self):
        with self.client.session_transaction() as session:
            session["user"] = "admin"
            session["role"] = "admin"
            session["selected_course"] = "Ingenieria de Software"
            session["session_id"] = "test-session"

    def test_build_embedding_payloads_preserves_chunk_order_and_metadata(self):
        records = build_chunk_records(
            ["primer chunk", "segundo chunk"],
            document_id=7,
            doc_hash="doc123",
            upload_hash="up456",
            course="Ingenieria de Software",
            upload_date="2026-03-22 10:00:00",
            filename="sample.txt",
            file_hash="file789",
        )

        payloads = build_embedding_payloads(records, provider=StubEmbeddingProvider())

        self.assertEqual([payload["chunk_id"] for payload in payloads], ["up456:7:0", "up456:7:1"])
        self.assertEqual(payloads[0]["text"], "primer chunk")
        self.assertEqual(len(payloads[0]["embedding"]), DEFAULT_EMBEDDING_DIMENSION)
        self.assertEqual(
            payloads[1]["metadata"],
            {
                "document_id": 7,
                "doc_hash": "doc123",
                "upload_hash": "up456",
                "course": "Ingenieria de Software",
                "upload_date": "2026-03-22 10:00:00",
                "filename": "sample.txt",
                "file_hash": "file789",
                "chunk_index": 1,
            },
        )

    def test_upload_registers_embedding_payload_for_each_chunk(self):
        self.login_as_admin()

        response = self.client.post(
            "/api/upload",
            data={"files[]": (io.BytesIO(b"contenido " * 200), "notas.txt")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        upload_hash = payload["upload_hash"]

        self.assertIn(upload_hash, app_module.UPLOAD_EMBEDDING_INDEX)
        embedding_ids = app_module.UPLOAD_EMBEDDING_INDEX[upload_hash]
        chunk_ids = app_module.UPLOAD_CHUNK_INDEX[upload_hash]
        self.assertEqual(len(embedding_ids), len(chunk_ids))
        self.assertGreater(len(embedding_ids), 0)

        first_embedding = app_module.EMBEDDING_REGISTRY[embedding_ids[0]]
        self.assertEqual(first_embedding["metadata"]["upload_hash"], upload_hash)
        self.assertEqual(first_embedding["metadata"]["filename"], "notas.txt")
        self.assertEqual(len(first_embedding["embedding"]), app_module.EMBEDDING_DIMENSION)

    def test_multi_file_upload_shares_one_upload_hash_for_embeddings(self):
        self.login_as_admin()

        response = self.client.post(
            "/api/upload",
            data={
                "files[]": [
                    (io.BytesIO(b"alpha " * 200), "alpha.txt"),
                    (io.BytesIO(b"beta " * 200), "beta.md"),
                ]
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        upload_hash = payload["upload_hash"]
        embedding_ids = app_module.UPLOAD_EMBEDDING_INDEX[upload_hash]

        self.assertTrue(embedding_ids)
        self.assertTrue(all(chunk_id.startswith(f"{upload_hash}:") for chunk_id in embedding_ids))
        self.assertTrue(
            all(
                app_module.EMBEDDING_REGISTRY[chunk_id]["metadata"]["upload_hash"] == upload_hash
                for chunk_id in embedding_ids
            )
        )

    def test_embedding_failure_rolls_back_state_and_cleans_files(self):
        self.login_as_admin()
        app_module.EMBEDDING_PROVIDER = FailingEmbeddingProvider()

        response = self.client.post(
            "/api/upload",
            data={"files[]": (io.BytesIO(b"contenido " * 200), "fallo.txt")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "Simulated embedding provider failure.")
        self.assertFalse(app_module.CHUNK_REGISTRY)
        self.assertFalse(app_module.UPLOAD_CHUNK_INDEX)
        self.assertFalse(app_module.EMBEDDING_REGISTRY)
        self.assertFalse(app_module.UPLOAD_EMBEDDING_INDEX)
        self.assertEqual(list(self.download_dir.iterdir()), [])

        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM documents")
        self.assertEqual(cursor.fetchone()[0], 0)
        cursor.execute("SELECT COUNT(*) FROM document_versions")
        self.assertEqual(cursor.fetchone()[0], 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
