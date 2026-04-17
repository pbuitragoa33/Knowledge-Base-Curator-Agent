from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from app import get_db_connection
from embedding_processing import (
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MODEL_NAME,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_DEVICE,
    EmbeddingGenerationError,
    LocalSentenceTransformerProvider,
)
from vector_store import VectorStoreError, query_course_embeddings
from observability import log_tool_invocation, log_tool_result, log_agent_error

load_dotenv()

# ─────────────────────────────────────────
# Proveedor de embeddings para las tools
# ─────────────────────────────────────────

_TOOL_EMBEDDING_PROVIDER = None


def _get_tool_embedding_provider() -> LocalSentenceTransformerProvider:
    """Inicializa el proveedor de embeddings para las herramientas."""

    global _TOOL_EMBEDDING_PROVIDER

    if _TOOL_EMBEDDING_PROVIDER is None:
        _TOOL_EMBEDDING_PROVIDER = LocalSentenceTransformerProvider(
            model_name=os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_EMBEDDING_MODEL_NAME),
            batch_size=int(os.environ.get("EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE)),
            device=os.environ.get("EMBEDDING_DEVICE", DEFAULT_EMBEDDING_DEVICE),
            embedding_dimension=DEFAULT_EMBEDDING_DIMENSION,
        )

    return _TOOL_EMBEDDING_PROVIDER


def _resolve_course_code(course_id: int) -> Optional[str]:
    """Resuelve el course_code desde la base de datos dado un course_id."""

    con = get_db_connection()
    c = con.cursor()

    try:
        c.execute("SELECT course_code FROM courses WHERE id = ?", (int(course_id),))
        row = c.fetchone()
    finally:
        con.close()

    return str(row[0]) if row else None


# ─────────────────────────────────────────
# Tool: búsqueda semántica vectorial
# ─────────────────────────────────────────

@tool
def search_course_documents(query: str, course_id: int, top_n: int = 5) -> str:
    """
    Busca documentos relevantes dentro de un curso usando similitud semántica vectorial.

    Usa esta herramienta cuando necesites recuperar evidencia o contexto desde los
    documentos del curso para responder una pregunta o analizar contenido.

    Args:
        query: La pregunta o texto de búsqueda en lenguaje natural.
        course_id: El identificador numérico del curso donde buscar.
        top_n: Número máximo de fragmentos a retornar (por defecto 5, máximo 20).

    Returns:
        Un texto con los fragmentos más relevantes encontrados, incluyendo
        su fuente (nombre del archivo) y puntaje de similitud.
    """

    # Registrar invocación de la herramienta
    log_tool_invocation(
        "search_course_documents",
        {"query": query, "course_id": course_id, "top_n": top_n},
    )

    try:
        # Validar top_n
        top_n = max(1, min(int(top_n), 20))

        # Resolver course_code
        course_code = _resolve_course_code(course_id)

        if not course_code:
            result = f"No se encontró el curso con id {course_id}."
            log_tool_result("search_course_documents", result)
            return result

        # Generar embedding de la consulta
        provider = _get_tool_embedding_provider()
        query_embedding = provider.embed_texts([query])[0]

        # Buscar en el vector store
        ranked_results = query_course_embeddings(
            course_code,
            query_embedding,
            top_n=top_n,
        )

        if not ranked_results:
            result = "No se encontraron documentos relevantes para esta consulta."
            log_tool_result("search_course_documents", result)
            return result

        # Formatear resultados como texto legible para el LLM
        output_lines = [f"Se encontraron {len(ranked_results)} fragmentos relevantes:\n"]

        for i, item in enumerate(ranked_results, start=1):
            metadata = item.get("metadata", {})
            filename = metadata.get("filename", "desconocido")
            score = round(item.get("score", 0.0), 4)
            text = item.get("text", "")
            output_lines.append(
                f"[{i}] Fuente: {filename} | Score: {score}\n{text}\n"
            )

        result = "\n".join(output_lines)
        log_tool_result("search_course_documents", result)
        return result

    except (EmbeddingGenerationError, VectorStoreError) as e:
        log_agent_error("search_course_documents", e)
        return f"Error al buscar documentos: {str(e)}"

    except Exception as e:
        log_agent_error("search_course_documents", e)
        return f"Error inesperado en la búsqueda: {str(e)}"


# ─────────────────────────────────────────
# Lista de tools disponibles para el agente
# ─────────────────────────────────────────

AGENT_TOOLS = [search_course_documents]


# ─────────────────────────────────────────
# Vincular tools al LLM
# ─────────────────────────────────────────

def get_llm_with_tools() -> ChatOpenAI:
    """Retorna el LLM con las herramientas vinculadas usando bind_tools."""

    api_key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
    model_name = str(os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini").strip()

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY es requerida para usar el agente con tools.")

    llm = ChatOpenAI(
        api_key=api_key,
        model=model_name,
        temperature=0,
    )

    return llm.bind_tools(AGENT_TOOLS)