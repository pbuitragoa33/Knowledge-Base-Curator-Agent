from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

# ─────────────────────────────────────────
# Configuración del logger estructurado
# ─────────────────────────────────────────

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,
)

logger = logging.getLogger("kbca.agent")

# ─────────────────────────────────────────
# Variable de entorno DEBUG_AGENT
# ─────────────────────────────────────────

def is_debug_agent() -> bool:
    """Retorna True si DEBUG_AGENT=True en el entorno."""
    return os.environ.get("DEBUG_AGENT", "False").strip().lower() == "true"


# ─────────────────────────────────────────
# Funciones de logging estructurado
# ─────────────────────────────────────────

def log_node_input(node_name: str, state: dict[str, Any]) -> None:
    """Registra la entrada de estado a un nodo del grafo."""

    logger.info(
        "NODE_INPUT | node=%s | course_id=%s | messages_count=%d | context_length=%d",
        node_name,
        state.get("course_id", "unknown"),
        len(state.get("messages", [])),
        len(str(state.get("extracted_context", ""))),
    )

    if is_debug_agent():
        print("\n" + "="*60)
        print(f"[DEBUG] NODE INPUT → {node_name}")
        print(f"  course_id       : {state.get('course_id')}")
        print(f"  messages_count  : {len(state.get('messages', []))}")
        print(f"  extracted_context (primeros 300 chars):")
        print(f"    {str(state.get('extracted_context', ''))[:300]}")
        print("="*60 + "\n")


def log_node_output(node_name: str, state: dict[str, Any]) -> None:
    """Registra la salida de estado de un nodo del grafo."""

    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    content_preview = ""

    if last_message:
        content = getattr(last_message, "content", "")
        content_preview = str(content)[:200]

    logger.info(
        "NODE_OUTPUT | node=%s | messages_count=%d | last_message_preview=%s",
        node_name,
        len(messages),
        content_preview,
    )

    if is_debug_agent():
        print("\n" + "="*60)
        print(f"[DEBUG] NODE OUTPUT ← {node_name}")
        print(f"  messages_count : {len(messages)}")
        print(f"  last_message (primeros 300 chars):")
        print(f"    {content_preview}")
        print("="*60 + "\n")


def log_prompt(node_name: str, prompt: str) -> None:
    """Registra el prompt exacto enviado al LLM cuando DEBUG_AGENT=True."""

    logger.info(
        "PROMPT_SENT | node=%s | prompt_length=%d",
        node_name,
        len(prompt),
    )

    if is_debug_agent():
        print("\n" + "="*60)
        print(f"[DEBUG] PROMPT ENVIADO AL LLM → nodo: {node_name}")
        print(f"{prompt}")
        print("="*60 + "\n")


def log_llm_response(node_name: str, response: Any) -> None:
    """Registra la respuesta cruda del LLM cuando DEBUG_AGENT=True."""

    content = getattr(response, "content", str(response))

    logger.info(
        "LLM_RESPONSE | node=%s | response_length=%d",
        node_name,
        len(str(content)),
    )

    if is_debug_agent():
        print("\n" + "="*60)
        print(f"[DEBUG] RESPUESTA CRUDA DEL LLM ← nodo: {node_name}")
        print(f"{content}")
        print("="*60 + "\n")


def log_tool_invocation(tool_name: str, arguments: dict[str, Any]) -> None:
    """Registra los argumentos pasados a una herramienta (tool) del agente."""

    logger.info(
        "TOOL_INVOKED | tool=%s | arguments=%s",
        tool_name,
        json.dumps(arguments, ensure_ascii=False),
    )

    if is_debug_agent():
        print("\n" + "="*60)
        print(f"[DEBUG] HERRAMIENTA INVOCADA → {tool_name}")
        print(f"  argumentos: {json.dumps(arguments, indent=2, ensure_ascii=False)}")
        print("="*60 + "\n")


def log_tool_result(tool_name: str, result: Any) -> None:
    """Registra el resultado retornado por una herramienta."""

    result_preview = str(result)[:300]

    logger.info(
        "TOOL_RESULT | tool=%s | result_preview=%s",
        tool_name,
        result_preview,
    )

    if is_debug_agent():
        print("\n" + "="*60)
        print(f"[DEBUG] RESULTADO DE HERRAMIENTA ← {tool_name}")
        print(f"  resultado (primeros 300 chars): {result_preview}")
        print("="*60 + "\n")


def log_agent_error(node_name: str, error: Exception) -> None:
    """Registra errores ocurridos durante la ejecución del agente."""

    logger.error(
        "AGENT_ERROR | node=%s | error_type=%s | error=%s",
        node_name,
        type(error).__name__,
        str(error),
    )

    if is_debug_agent():
        print("\n" + "="*60)
        print(f"[DEBUG] ERROR EN NODO → {node_name}")
        print(f"  tipo  : {type(error).__name__}")
        print(f"  error : {str(error)}")
        print("="*60 + "\n")