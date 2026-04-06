import io
import os
import shutil
import sqlite3
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent / ".tmp_validation" / "issue13_tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATABASE_PATH", str(TEST_ROOT / "bootstrap.db"))
os.environ.setdefault("DOWNLOAD_DIR", str(TEST_ROOT / "bootstrap_uploads"))
os.environ.setdefault("CHROMA_PERSIST_DIR", str(TEST_ROOT / "bootstrap_chroma"))

import app as app_module
from vector_store import build_course_collection_name, get_course_collection, reset_vector_store_client


class Issue13ImplementationTests(unittest.TestCase):
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

    def login_as_admin(self, course: str):
        with self.client.session_transaction() as session:
            session["user"] = "admin"
            session["role"] = "admin"
            session["selected_course"] = course
            session["session_id"] = "test-session"

    def upload_course_file(self, course: str, filename: str, content: bytes):
        self.login_as_admin(course)

        response = self.client.post(
            "/api/upload",
            data={"files[]": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_build_course_collection_name_is_deterministic_and_chroma_safe(self):
        collection_name = build_course_collection_name("  ISW 101 / 2026  ")

        self.assertEqual(collection_name, "kbca-course-isw-101-2026")
        self.assertRegex(collection_name, r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])$")
        self.assertNotIn("..", collection_name)

    def test_upload_persists_embeddings_in_the_course_collection(self):
        payload = self.upload_course_file(
            "Ingenieria de Software",
            "notas.txt",
            b"contenido software " * 200,
        )

        upload_hash = payload["upload_hash"]
        embedding_ids = app_module.UPLOAD_EMBEDDING_INDEX[upload_hash]
        collection = get_course_collection("ISW-101")
        records = collection.get(
            ids=embedding_ids,
            include=["documents", "metadatas", "embeddings"],
        )

        self.assertEqual(len(records["ids"]), len(embedding_ids))
        self.assertGreater(len(records["ids"]), 0)
        self.assertEqual(records["metadatas"][0]["course_code"], "ISW-101")
        self.assertEqual(records["metadatas"][0]["filename"], "notas.txt")
        self.assertEqual(records["metadatas"][0]["upload_hash"], upload_hash)
        self.assertTrue(records["documents"][0].startswith("contenido software"))
        self.assertEqual(len(records["embeddings"][0]), app_module.EMBEDDING_DIMENSION)

    def test_each_course_uses_a_separate_collection(self):
        software_payload = self.upload_course_file(
            "Ingenieria de Software",
            "software.txt",
            b"analisis diseno arquitectura " * 180,
        )
        systems_payload = self.upload_course_file(
            "Ingenieria de Sistemas",
            "sistemas.txt",
            b"bases datos sistemas distribuidos " * 180,
        )

        software_ids = set(app_module.UPLOAD_EMBEDDING_INDEX[software_payload["upload_hash"]])
        systems_ids = set(app_module.UPLOAD_EMBEDDING_INDEX[systems_payload["upload_hash"]])
        software_collection = get_course_collection("ISW-101")
        systems_collection = get_course_collection("ISI-102")
        software_records = software_collection.get(ids=list(software_ids), include=["metadatas"])
        systems_records = systems_collection.get(ids=list(systems_ids), include=["metadatas"])

        self.assertTrue(software_ids)
        self.assertTrue(systems_ids)
        self.assertTrue(software_ids.isdisjoint(systems_ids))
        self.assertTrue(all(metadata["course_code"] == "ISW-101" for metadata in software_records["metadatas"]))
        self.assertTrue(all(metadata["course_code"] == "ISI-102" for metadata in systems_records["metadatas"]))

    def test_query_results_stay_within_the_selected_course_collection(self):
        software_payload = self.upload_course_file(
            "Ingenieria de Software",
            "software.txt",
            b"analisis requisitos historias usuario " * 200,
        )
        self.upload_course_file(
            "Ingenieria de Sistemas",
            "sistemas.txt",
            b"servidores redes virtualizacion " * 200,
        )

        software_collection = get_course_collection("ISW-101")
        source_chunk_id = app_module.UPLOAD_EMBEDDING_INDEX[software_payload["upload_hash"]][0]
        source_record = software_collection.get(
            ids=[source_chunk_id],
            include=["embeddings", "documents", "metadatas"],
        )
        results = software_collection.query(
            query_embeddings=[source_record["embeddings"][0]],
            n_results=3,
            include=["documents", "metadatas", "distances"],
        )

        self.assertEqual(results["ids"][0][0], source_chunk_id)
        self.assertTrue(all(metadata["course_code"] == "ISW-101" for metadata in results["metadatas"][0]))
        self.assertTrue(all(metadata["filename"] == "software.txt" for metadata in results["metadatas"][0]))
        self.assertTrue(all("servidores" not in document for document in results["documents"][0]))

    def test_vector_store_failure_rolls_back_sqlite_files_and_vectors(self):
        original_upsert = app_module.upsert_course_embeddings

        def failing_upsert(course_code, embedding_payloads):
            raise app_module.VectorStoreError("Simulated vector store failure.")

        app_module.upsert_course_embeddings = failing_upsert

        try:
            self.login_as_admin("Ingenieria de Software")
            response = self.client.post(
                "/api/upload",
                data={"files[]": (io.BytesIO(b"contenido " * 200), "fallo.txt")},
                content_type="multipart/form-data",
            )
        finally:
            app_module.upsert_course_embeddings = original_upsert

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "Simulated vector store failure.")
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

        collection = get_course_collection("ISW-101")
        self.assertEqual(collection.count(), 0)


if __name__ == "__main__":
    unittest.main()
