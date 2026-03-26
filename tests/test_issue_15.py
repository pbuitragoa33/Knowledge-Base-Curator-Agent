import io
import os
import shutil
import sqlite3
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent / ".tmp_validation" / "issue15_tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATABASE_PATH", str(TEST_ROOT / "bootstrap.db"))
os.environ.setdefault("DOWNLOAD_DIR", str(TEST_ROOT / "bootstrap_uploads"))
os.environ.setdefault("CHROMA_PERSIST_DIR", str(TEST_ROOT / "bootstrap_chroma"))

import app as app_module
from vector_store import reset_vector_store_client


class Issue15ImplementationTests(unittest.TestCase):
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

    def login_as_user(self, username: str, role: str, course: str):
        with self.client.session_transaction() as session:
            session["user"] = username
            session["role"] = role
            session["selected_course"] = course
            session["session_id"] = "test-session"

    def upload_file(self, course: str, filename: str, content: bytes):
        self.login_as_user("admin", "admin", course)
        response = self.client.post(
            "/api/upload",
            data={"files[]": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def get_course_id(self, course_name: str) -> int:
        conn = sqlite3.connect(self.database_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM courses WHERE name = ?", (course_name,))
        course_id = cursor.fetchone()[0]
        conn.close()
        return int(course_id)

    def test_query_returns_top_n_results_with_source_metadata(self):
        self.upload_file(
            "Ingenieria de Software",
            "software-a.txt",
            b"arquitectura limpia capas entidades servicios " * 180,
        )
        self.upload_file(
            "Ingenieria de Software",
            "software-b.txt",
            b"historias usuario backlog sprint retrospectiva " * 180,
        )

        course_id = self.get_course_id("Ingenieria de Software")
        self.login_as_user("estudiante", "estudiante", "Ingenieria de Software")

        response = self.client.post(
            "/api/query",
            json={
                "course_id": course_id,
                "query": "que temas hay sobre arquitectura y sprint",
                "top_n": 3,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["course"]["id"], course_id)
        self.assertEqual(payload["course"]["course_code"], "ISW-101")
        self.assertEqual(payload["top_n"], 3)
        self.assertGreater(len(payload["results"]), 0)
        self.assertLessEqual(len(payload["results"]), 3)

        for result in payload["results"]:
            self.assertIn("chunk_text", result)
            self.assertIn("score", result)
            self.assertIn("source", result)
            self.assertIn("filename", result["source"])
            self.assertIn("upload_date", result["source"])
            self.assertIsInstance(result["score"], float)

    def test_query_scopes_results_to_selected_course_collection(self):
        self.upload_file(
            "Ingenieria de Software",
            "solo-software.txt",
            b"arquitectura requisitos modelado diseno " * 200,
        )
        self.upload_file(
            "Ingenieria de Sistemas",
            "solo-sistemas.txt",
            b"servidores redes virtualizacion infraestructura " * 200,
        )

        course_id = self.get_course_id("Ingenieria de Software")
        self.login_as_user("estudiante", "estudiante", "Ingenieria de Software")

        response = self.client.post(
            "/api/query",
            json={
                "course_id": course_id,
                "query": "busca informacion sobre servidores e infraestructura",
                "top_n": 5,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        filenames = {result["source"]["filename"] for result in payload["results"]}

        self.assertTrue(filenames)
        self.assertIn("solo-software.txt", filenames)
        self.assertNotIn("solo-sistemas.txt", filenames)

    def test_query_validates_required_fields(self):
        self.login_as_user("estudiante", "estudiante", "Ingenieria de Software")

        response = self.client.post(
            "/api/query",
            json={"course_id": self.get_course_id("Ingenieria de Software"), "query": "   "},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "La consulta es requerida")


if __name__ == "__main__":
    unittest.main()
