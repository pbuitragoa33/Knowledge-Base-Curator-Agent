import os
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_ROOT = Path(__file__).resolve().parent / ".tmp_validation" / "issue61_tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

os.environ.setdefault("DATABASE_PATH", str(TEST_ROOT / "bootstrap.db"))
os.environ.setdefault("DOWNLOAD_DIR", str(TEST_ROOT / "bootstrap_uploads"))
os.environ.setdefault("CHROMA_PERSIST_DIR", str(TEST_ROOT / "bootstrap_chroma"))

import app as app_module
from vector_store import reset_vector_store_client


class FakeEmbeddingProvider:
    def embed_texts(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeAiResponse:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChatLlm:
    def __init__(self, content):
        self.content = content

    def invoke(self, _messages):
        return FakeAiResponse(self.content)


class Issue61ImplementationTests(unittest.TestCase):
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
        app_module._AGENT_CHAT_EMBEDDING_PROVIDER = None
        app_module.init_db()
        self.client = app_module.app.test_client()

    def open_connection(self):
        connection = sqlite3.connect(str(self.database_path))
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def login_as_user(self, username="admin"):
        with self.client.session_transaction() as session:
            session["user"] = username
            session["selected_course"] = "Ingenieria de Software"
            session["session_id"] = "test-session"

    def get_course_id(self):
        connection = self.open_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT id FROM courses WHERE name = ?", ("Ingenieria de Software",))
        course_id = cursor.fetchone()[0]
        connection.close()
        return int(course_id)

    def post_chat(self, classifier_result, llm_text="Respuesta conversacional."):
        course_id = self.get_course_id()

        with (
            patch.object(app_module, "_get_agent_chat_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch.object(app_module, "query_course_embeddings", return_value=[{"score": 0.95}]),
            patch("agent_tools.get_llm_with_tools", return_value=FakeChatLlm(llm_text)),
            patch("agent_workflow.classify_chat_response_for_suggestion", return_value=classifier_result),
        ):
            return self.client.post(
                "/api/agent/chat",
                json={
                    "message": "Pregunta sobre el curso",
                    "course_id": course_id,
                    "conversation_id": "conversation-61",
                },
            )

    def test_normal_chat_response_is_not_suggestion_and_accepts_thumbs_feedback(self):
        self.login_as_user()

        response = self.post_chat({"is_suggestion": False, "suggestion": None})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["is_suggestion"])
        self.assertIsNone(payload["suggestion"])

        connection = self.open_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT is_suggestion, suggestion_id FROM agent_chat_history WHERE id = ?",
            (payload["agent_message_id"],),
        )
        row = cursor.fetchone()
        connection.close()

        self.assertEqual(row, (0, None))

        feedback_response = self.client.post(
            "/api/agent/chat/feedback",
            json={
                "message_id": payload["agent_message_id"],
                "course_id": self.get_course_id(),
                "feedback_value": "up",
            },
        )

        self.assertEqual(feedback_response.status_code, 200)
        self.assertEqual(feedback_response.get_json()["message"], "Feedback guardado")

    def test_actionable_chat_response_persists_and_returns_embedded_suggestion(self):
        self.login_as_user()
        classifier_result = {
            "is_suggestion": True,
            "suggestion": {
                "tipo": "redundancia",
                "input_context": "Eliminar el parrafo repetido de la unidad 2.",
                "razonamiento": "La respuesta detecta contenido duplicado en los materiales.",
                "evidencia_ids": ["upload-test:1:0"],
            },
        }

        response = self.post_chat(
            classifier_result,
            llm_text="Propongo eliminar el parrafo repetido de la unidad 2.",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["is_suggestion"])
        self.assertEqual(payload["suggestion"]["estado"], "pendiente")
        self.assertEqual(payload["suggestion"]["tipo"], "redundancia")
        self.assertIsInstance(payload["suggestion"]["id"], int)

        connection = self.open_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT is_suggestion, suggestion_id FROM agent_chat_history WHERE id = ?",
            (payload["agent_message_id"],),
        )
        chat_row = cursor.fetchone()
        cursor.execute(
            "SELECT estado, tipo FROM agent_suggestions WHERE id = ?",
            (payload["suggestion"]["id"],),
        )
        suggestion_row = cursor.fetchone()
        connection.close()

        self.assertEqual(chat_row, (1, payload["suggestion"]["id"]))
        self.assertEqual(suggestion_row, ("pendiente", "redundancia"))

        history_response = self.client.get(
            f"/api/agent/chat/history/{self.get_course_id()}?conversation_id=conversation-61"
        )
        self.assertEqual(history_response.status_code, 200)
        history = history_response.get_json()
        agent_entries = [entry for entry in history if entry["sender_type"] == "agente"]

        self.assertEqual(len(agent_entries), 1)
        self.assertTrue(agent_entries[0]["is_suggestion"])
        self.assertEqual(agent_entries[0]["suggestion_id"], payload["suggestion"]["id"])
        self.assertEqual(agent_entries[0]["suggestion"]["estado"], "pendiente")

    def test_resolve_suggestion_endpoint_uses_existing_review_validation(self):
        self.login_as_user()
        course_id = self.get_course_id()
        approved_id = app_module.save_agent_suggestion(
            course_id,
            "conflicto",
            "Revisar conflicto entre dos unidades.",
            "Hay criterios incompatibles.",
            [],
        )
        rejected_id = app_module.save_agent_suggestion(
            course_id,
            "deactualizacion",
            "Actualizar lectura antigua.",
            "La fuente ya no corresponde al programa vigente.",
            [],
        )

        approval = self.client.post(
            "/api/agent/resolve-suggestion",
            json={
                "suggestion_id": approved_id,
                "estado": "aprobado",
                "score_manual": 5,
            },
        )
        self.assertEqual(approval.status_code, 200)
        self.assertEqual(approval.get_json()["suggestion"]["estado"], "aprobado")

        missing_feedback = self.client.post(
            "/api/agent/resolve-suggestion",
            json={
                "suggestion_id": rejected_id,
                "estado": "rechazado",
                "score_manual": 2,
            },
        )
        self.assertEqual(missing_feedback.status_code, 400)
        self.assertIn("feedback_text", missing_feedback.get_json()["error"])

        rejection = self.client.post(
            "/api/agent/resolve-suggestion",
            json={
                "suggestion_id": rejected_id,
                "estado": "rechazado",
                "score_manual": 2,
                "feedback_text": "No corresponde al material actual.",
            },
        )
        self.assertEqual(rejection.status_code, 200)
        self.assertEqual(rejection.get_json()["suggestion"]["estado"], "rechazado")

    def test_chat_template_contains_suggestion_card_and_resolution_endpoint(self):
        self.login_as_user()

        response = self.client.get("/chat/Ingenieria%20de%20Software")

        self.assertEqual(response.status_code, 200)
        payload = response.data.decode("utf-8")
        self.assertIn("chat-suggestion-card", payload)
        self.assertIn("/api/agent/resolve-suggestion", payload)


if __name__ == "__main__":
    unittest.main()
