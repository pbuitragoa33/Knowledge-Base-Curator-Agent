import io
import os
import shutil
import sqlite3
import time
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent / ".tmp_validation" / "issue14_tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATABASE_PATH", str(TEST_ROOT / "bootstrap.db"))
os.environ.setdefault("DOWNLOAD_DIR", str(TEST_ROOT / "bootstrap_uploads"))
os.environ.setdefault("CHROMA_PERSIST_DIR", str(TEST_ROOT / "bootstrap_chroma"))

import app as app_module
from vector_store import get_course_collection, reset_vector_store_client


class Issue14ImplementationTests(unittest.TestCase):
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
        app_module.EMBEDDING_PROVIDER = None
        app_module.init_db()
        self.client = app_module.app.test_client()

    def login_as_admin(self, course: str = "Ingenieria de Software"):
        with self.client.session_transaction() as session:
            session["user"] = "admin"
            session["role"] = "admin"
            session["selected_course"] = course
            session["session_id"] = "test-session"

    def upload_file(self, course: str, filename: str, content: bytes):
        self.login_as_admin(course)
        response = self.client.post(
            "/api/upload",
            data={"files[]": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_version_upload_replaces_previous_vectors(self):
        first_payload = self.upload_file(
            "Ingenieria de Software",
            "versionado.txt",
            b"version uno arquitectura software " * 200,
        )
        second_payload = self.upload_file(
            "Ingenieria de Software",
            "versionado.txt",
            b"version dos arquitectura hexagonal " * 200,
        )

        first_doc = first_payload["files"][0]
        second_doc = second_payload["files"][0]
        self.assertEqual(first_doc["document_id"], second_doc["document_id"])
        self.assertEqual(first_doc["doc_hash"], second_doc["doc_hash"])

        doc_hash = first_doc["doc_hash"]
        second_upload_hash = second_payload["upload_hash"]
        expected_chunk_ids = set(app_module.UPLOAD_EMBEDDING_INDEX[second_upload_hash])

        collection = get_course_collection("ISW-101")
        records = collection.get(where={"doc_hash": doc_hash}, include=["metadatas"])
        current_ids = set(records["ids"])

        self.assertTrue(current_ids)
        self.assertEqual(current_ids, expected_chunk_ids)
        self.assertTrue(
            all(metadata["upload_hash"] == second_upload_hash for metadata in records["metadatas"])
        )

    def test_version_upload_rolls_back_sqlite_if_vector_delete_fails(self):
        first_payload = self.upload_file(
            "Ingenieria de Software",
            "versionado-falla.txt",
            b"version inicial estable " * 200,
        )

        document_id = first_payload["files"][0]["document_id"]
        doc_hash = first_payload["files"][0]["doc_hash"]
        first_file_hash = first_payload["files"][0]["file_hash"]
        first_upload_hash = first_payload["upload_hash"]
        expected_chunk_ids = set(app_module.UPLOAD_EMBEDDING_INDEX[first_upload_hash])
        files_before_failed_version = {path.name for path in self.download_dir.iterdir()}
        original_delete_by_metadata = app_module.delete_course_embeddings_by_metadata

        def failing_delete_by_metadata(course_code, where):
            if where.get("doc_hash") == doc_hash:
                raise app_module.VectorStoreError("Simulated vector-store delete-on-version failure.")
            return original_delete_by_metadata(course_code, where)

        app_module.delete_course_embeddings_by_metadata = failing_delete_by_metadata

        try:
            # Evita colisiones del sufijo timestamp (resolución a segundos) entre versiones consecutivas.
            time.sleep(1.1)
            self.login_as_admin("Ingenieria de Software")
            response = self.client.post(
                "/api/upload",
                data={"files[]": (io.BytesIO(b"version nueva no persistida " * 200), "versionado-falla.txt")},
                content_type="multipart/form-data",
            )
        finally:
            app_module.delete_course_embeddings_by_metadata = original_delete_by_metadata

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "Simulated vector-store delete-on-version failure.")

        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute("SELECT file_hash FROM documents WHERE id = ?", (document_id,))
        persisted_document = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM document_versions WHERE document_id = ?", (document_id,))
        version_count = cursor.fetchone()[0]
        conn.close()

        self.assertIsNotNone(persisted_document)
        self.assertEqual(persisted_document[0], first_file_hash)
        self.assertEqual(version_count, 1)

        collection = get_course_collection("ISW-101")
        records = collection.get(where={"doc_hash": doc_hash}, include=["metadatas"])
        current_ids = set(records["ids"])

        self.assertEqual(current_ids, expected_chunk_ids)
        self.assertTrue(
            all(metadata["upload_hash"] == first_upload_hash for metadata in records["metadatas"])
        )

        files_after_failed_version = {path.name for path in self.download_dir.iterdir()}
        self.assertEqual(files_after_failed_version, files_before_failed_version)

    def test_delete_document_removes_vectors_and_sqlite_rows(self):
        upload_payload = self.upload_file(
            "Ingenieria de Software",
            "eliminar.txt",
            b"contenido para eliminar " * 200,
        )

        document_id = upload_payload["files"][0]["document_id"]
        doc_hash = upload_payload["files"][0]["doc_hash"]

        self.login_as_admin("Ingenieria de Software")
        response = self.client.delete(f"/api/delete-document/{document_id}")

        self.assertEqual(response.status_code, 200)

        collection = get_course_collection("ISW-101")
        records = collection.get(where={"doc_hash": doc_hash}, include=["metadatas"])
        self.assertEqual(records["ids"], [])

        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (document_id,))
        self.assertEqual(cursor.fetchone()[0], 0)
        cursor.execute("SELECT COUNT(*) FROM document_versions WHERE document_id = ?", (document_id,))
        self.assertEqual(cursor.fetchone()[0], 0)
        conn.close()

    def test_delete_document_rolls_back_sqlite_if_vector_store_fails(self):
        upload_payload = self.upload_file(
            "Ingenieria de Software",
            "fallo-borrado.txt",
            b"contenido estable " * 200,
        )

        document_id = upload_payload["files"][0]["document_id"]
        doc_hash = upload_payload["files"][0]["doc_hash"]
        original_delete_by_metadata = app_module.delete_course_embeddings_by_metadata

        def failing_delete_by_metadata(course_code, where):
            raise app_module.VectorStoreError("Simulated vector-store delete failure.")

        app_module.delete_course_embeddings_by_metadata = failing_delete_by_metadata

        try:
            self.login_as_admin("Ingenieria de Software")
            response = self.client.delete(f"/api/delete-document/{document_id}")
        finally:
            app_module.delete_course_embeddings_by_metadata = original_delete_by_metadata

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "Simulated vector-store delete failure.")

        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (document_id,))
        self.assertEqual(cursor.fetchone()[0], 1)
        cursor.execute("SELECT COUNT(*) FROM document_versions WHERE document_id = ?", (document_id,))
        self.assertEqual(cursor.fetchone()[0], 1)
        conn.close()

        collection = get_course_collection("ISW-101")
        records = collection.get(where={"doc_hash": doc_hash}, include=["metadatas"])
        self.assertGreater(len(records["ids"]), 0)


if __name__ == "__main__":
    unittest.main()
