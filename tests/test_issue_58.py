import os
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent / ".tmp_validation" / "issue58_tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

os.environ.setdefault("DATABASE_PATH", str(TEST_ROOT / "bootstrap.db"))
os.environ.setdefault("DOWNLOAD_DIR", str(TEST_ROOT / "bootstrap_uploads"))
os.environ.setdefault("CHROMA_PERSIST_DIR", str(TEST_ROOT / "bootstrap_chroma"))

import app as app_module
from vector_store import reset_vector_store_client


class Issue58ImplementationTests(unittest.TestCase):
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

    def open_connection(self):
        connection = sqlite3.connect(str(self.database_path))
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def login_as_user(self, username: str):
        with self.client.session_transaction() as session:
            session["user"] = username
            session["selected_course"] = "Ingenieria de Software"
            session["session_id"] = "test-session"

    def get_course_id(self, course_name: str = "Ingenieria de Software") -> int:
        connection = self.open_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT id FROM courses WHERE name = ?", (course_name,))
        course_id = cursor.fetchone()[0]
        connection.close()
        return int(course_id)

    def insert_document(self, course_name: str, filename: str) -> int:
        connection = self.open_connection()
        cursor = connection.cursor()
        cursor.execute(
            '''INSERT INTO documents
               (course, doc_hash, filename, file_hash, upload_date, filepath, uploaded_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (
                course_name,
                f"doc-hash-{filename}",
                filename,
                f"file-hash-{filename}",
                "2026-04-29 10:00:00",
                str(self.download_dir / filename),
                "profesor",
            ),
        )
        document_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return int(document_id)

    def create_professor(self, username: str):
        connection = self.open_connection()
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO users (username, email, password, role) VALUES (?, ?, ?, ?)",
            (username, f"{username}@example.com", app_module.hash_password("password123"), "profesor"),
        )
        connection.commit()
        connection.close()

    def test_export_downloads_markdown_with_only_approved_suggestions(self):
        course_id = self.get_course_id()
        document_id = self.insert_document("Ingenieria de Software", "plan-curso.md")
        evidence_id = f"upload-test:{document_id}:0"

        app_module.save_agent_suggestion(
            course_id,
            "redundancia",
            "Eliminar parrafo repetido de la unidad 2.",
            "El material repite la misma explicacion en dos secciones.",
            [evidence_id],
            estado="aprobado",
            reviewed_by="profesor",
        )
        app_module.save_agent_suggestion(
            course_id,
            "conflicto",
            "Revisar criterio sin documento asociado.",
            "La evidencia no tiene un chunk resoluble.",
            ["chunk-sin-formato"],
            estado="aprobado",
            reviewed_by="admin",
        )
        app_module.save_agent_suggestion(
            course_id,
            "deactualizacion",
            "Pendiente que no debe exportarse.",
            "Esta sugerencia sigue pendiente.",
            [],
        )
        app_module.save_agent_suggestion(
            course_id,
            "conflicto",
            "Rechazada que no debe exportarse.",
            "Esta sugerencia fue rechazada.",
            [],
            estado="rechazado",
            reviewed_by="profesor",
        )

        self.login_as_user("admin")
        response = self.client.get(f"/api/agent/export-suggestions/{course_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/markdown")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("plan_accion_ISW-101.md", response.headers["Content-Disposition"])

        payload = response.data.decode("utf-8")
        self.assertIn("# Plan de Acción de Curaduría", payload)
        self.assertIn("Ingenieria de Software", payload)
        self.assertIn("Total de sugerencias aprobadas: 2", payload)
        self.assertIn("## plan-curso.md", payload)
        self.assertIn("## Documento no identificado", payload)
        self.assertIn("Eliminar parrafo repetido de la unidad 2.", payload)
        self.assertIn("El material repite la misma explicacion en dos secciones.", payload)
        self.assertIn(f"`{evidence_id}`", payload)
        self.assertIn("Aprobada por: profesor", payload)
        self.assertIn("Revisar criterio sin documento asociado.", payload)
        self.assertNotIn("Pendiente que no debe exportarse.", payload)
        self.assertNotIn("Rechazada que no debe exportarse.", payload)

    def test_export_returns_404_for_unknown_course(self):
        self.login_as_user("admin")

        response = self.client.get("/api/agent/export-suggestions/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Curso no encontrado")

    def test_review_page_exposes_export_link(self):
        course_id = self.get_course_id()
        self.login_as_user("admin")

        response = self.client.get("/review/Ingenieria%20de%20Software")

        self.assertEqual(response.status_code, 200)
        payload = response.data.decode("utf-8")
        self.assertIn("Exportar Plan de Acción", payload)
        self.assertIn(f"/api/agent/export-suggestions/{course_id}", payload)

    def test_export_rejects_student_role(self):
        self.login_as_user("estudiante")

        response = self.client.get(f"/api/agent/export-suggestions/{self.get_course_id()}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "Acceso denegado")

    def test_export_rejects_professor_without_course_permission(self):
        self.create_professor("profesor_sin_curso")
        self.login_as_user("profesor_sin_curso")

        response = self.client.get(f"/api/agent/export-suggestions/{self.get_course_id()}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "No es profesor de este curso")

    def test_export_redirects_unauthenticated_user_to_login(self):
        response = self.client.get(f"/api/agent/export-suggestions/{self.get_course_id()}")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("Location", ""))


if __name__ == "__main__":
    unittest.main()
