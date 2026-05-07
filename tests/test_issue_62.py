import io
import os
import shutil
import sqlite3
import sys
import time
import unittest
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent / ".tmp_validation" / "issue62_tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

os.environ.setdefault("DATABASE_PATH", str(TEST_ROOT / "bootstrap.db"))
os.environ.setdefault("DOWNLOAD_DIR", str(TEST_ROOT / "bootstrap_uploads"))
os.environ.setdefault("CHROMA_PERSIST_DIR", str(TEST_ROOT / "bootstrap_chroma"))

import app as app_module
from vector_store import reset_vector_store_client


class Issue62ImplementationTests(unittest.TestCase):
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

    def login_as_admin(self):
        with self.client.session_transaction() as session:
            session["user"] = "admin"
            session["role"] = "admin"
            session["selected_course"] = "Ingenieria de Software"
            session["session_id"] = "test-session"

    def get_course_id(self):
        connection = self.open_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT id FROM courses WHERE name = ?", ("Ingenieria de Software",))
        course_id = cursor.fetchone()[0]
        connection.close()
        return int(course_id)

    def insert_document(self, filename="documento.md"):
        connection = self.open_connection()
        cursor = connection.cursor()
        cursor.execute(
            '''INSERT INTO documents
               (course, doc_hash, filename, file_hash, upload_date, filepath, uploaded_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (
                "Ingenieria de Software",
                f"doc-hash-{filename}",
                filename,
                f"file-hash-{filename}",
                "2026-05-07 10:00:00",
                str(self.download_dir / filename),
                "profesor",
            ),
        )
        document_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return int(document_id)

    def get_document_status(self, document_id):
        connection = self.open_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT status FROM documents WHERE id = ?", (document_id,))
        status = cursor.fetchone()[0]
        connection.close()
        return status

    def set_document_status(self, document_id, status):
        connection = self.open_connection()
        cursor = connection.cursor()
        cursor.execute("UPDATE documents SET status = ? WHERE id = ?", (status, document_id))
        connection.commit()
        connection.close()

    def upload_file(self, filename, content):
        self.login_as_admin()
        return self.client.post(
            "/api/upload",
            data={"files[]": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )

    def resolve_suggestion(self, suggestion_id, estado="aprobado"):
        self.login_as_admin()
        return self.client.post(
            "/api/agent/resolve-suggestion",
            json={
                "suggestion_id": suggestion_id,
                "estado": estado,
                "score_manual": 5,
                "feedback_text": "Resuelto en prueba." if estado == "rechazado" else "",
            },
        )

    def test_uploaded_document_status_is_updated_in_documents_api(self):
        upload_response = self.upload_file("estado-inicial.txt", b"contenido inicial " * 200)

        self.assertEqual(upload_response.status_code, 200)
        upload_payload = upload_response.get_json()
        self.assertEqual(upload_payload["files"][0]["status"], app_module.DOCUMENT_STATUS_UPDATED)

        documents_response = self.client.get("/api/documents/Ingenieria%20de%20Software")
        self.assertEqual(documents_response.status_code, 200)
        documents = documents_response.get_json()

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["status"], app_module.DOCUMENT_STATUS_UPDATED)

    def test_new_document_version_resets_status_to_updated(self):
        first_response = self.upload_file("versionado-estado.txt", b"version uno " * 200)
        self.assertEqual(first_response.status_code, 200)
        first_payload = first_response.get_json()
        document_id = first_payload["files"][0]["document_id"]

        self.set_document_status(document_id, app_module.DOCUMENT_STATUS_IN_REVIEW)
        time.sleep(1.1)

        second_response = self.upload_file("versionado-estado.txt", b"version dos " * 200)

        self.assertEqual(second_response.status_code, 200)
        second_payload = second_response.get_json()
        self.assertEqual(second_payload["files"][0]["document_id"], document_id)
        self.assertEqual(self.get_document_status(document_id), app_module.DOCUMENT_STATUS_UPDATED)

    def test_pending_suggestion_marks_document_in_review(self):
        document_id = self.insert_document("revision.md")
        evidence_id = f"upload-test:{document_id}:0"

        app_module.save_agent_suggestion(
            self.get_course_id(),
            "conflicto",
            "Revisar criterio conflictivo.",
            "La evidencia apunta a una inconsistencia.",
            [evidence_id],
        )

        self.assertEqual(self.get_document_status(document_id), app_module.DOCUMENT_STATUS_IN_REVIEW)

    def test_resolving_all_document_suggestions_marks_document_approved(self):
        document_id = self.insert_document("aprobado.md")
        evidence_id = f"upload-test:{document_id}:0"
        suggestion_id = app_module.save_agent_suggestion(
            self.get_course_id(),
            "conflicto",
            "Revisar criterio conflictivo.",
            "La evidencia apunta a una inconsistencia.",
            [evidence_id],
        )

        response = self.resolve_suggestion(suggestion_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_document_status(document_id), app_module.DOCUMENT_STATUS_APPROVED)

    def test_resolving_one_suggestion_keeps_document_in_review_if_another_is_pending(self):
        document_id = self.insert_document("pendientes.md")
        evidence_id = f"upload-test:{document_id}:0"
        first_suggestion_id = app_module.save_agent_suggestion(
            self.get_course_id(),
            "conflicto",
            "Primera sugerencia.",
            "Debe revisarse.",
            [evidence_id],
        )
        app_module.save_agent_suggestion(
            self.get_course_id(),
            "deactualizacion",
            "Segunda sugerencia.",
            "Sigue pendiente.",
            [evidence_id],
        )

        response = self.resolve_suggestion(first_suggestion_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_document_status(document_id), app_module.DOCUMENT_STATUS_IN_REVIEW)

    def test_unresolvable_evidence_does_not_change_document_status(self):
        document_id = self.insert_document("sin-evidencia.md")

        app_module.save_agent_suggestion(
            self.get_course_id(),
            "conflicto",
            "Sugerencia sin chunk resoluble.",
            "No debe afectar documentos.",
            ["chunk-sin-formato"],
        )

        self.assertEqual(self.get_document_status(document_id), app_module.DOCUMENT_STATUS_UPDATED)

    def test_upload_page_renders_document_status_badge(self):
        self.login_as_admin()

        response = self.client.get("/upload/Ingenieria%20de%20Software")

        self.assertEqual(response.status_code, 200)
        payload = response.data.decode("utf-8")
        self.assertIn("document-status-badge", payload)
        self.assertIn("buildDocumentStatusBadge", payload)


if __name__ == "__main__":
    unittest.main()
