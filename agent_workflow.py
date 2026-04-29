from __future__ import annotations

import json
import os
import re
from typing import Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AnyMessage, BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import Annotated

from app import get_active_prompt, get_db_connection, save_agent_suggestion
from observability import (
    log_node_input,
    log_node_output,
    log_prompt,
    log_llm_response,
    log_agent_error,
)

load_dotenv(override=True)

DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


class AgentWorkflowError(RuntimeError):
    """Raised when the agent workflow cannot run safely."""


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    course_id: int
    conversation_id: str
    extracted_context: str
    analysis_output: str
    suggestions: list[dict[str, object]]


def get_agent_llm() -> ChatOpenAI:
    """Return the configured OpenAI chat model for the agent workflow."""

    api_key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
    model_name = str(os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL).strip()

    if not api_key:
        raise AgentWorkflowError("OPENAI_API_KEY is required to run the agent workflow.")

    return ChatOpenAI(
        api_key=api_key,
        model=model_name,
        temperature=0,
    )


def _resolve_course_name(course_id: int) -> str:
    """Resolve the course display name from the current SQLite database."""

    con = get_db_connection()
    c = con.cursor()

    try:
        c.execute("SELECT name FROM courses WHERE id = ?", (int(course_id),))
        row = c.fetchone()
    finally:
        con.close()

    if row is None:
        raise AgentWorkflowError(f"Course id {course_id} was not found.")

    return str(row[0])


def _render_analysis_prompt(state: AgentState) -> str:

    """Renderiza el prompt activo de analisis con el contexto recibido."""

    prompt_template = get_active_prompt("analisis")

    if not prompt_template:

        raise AgentWorkflowError("No active 'analisis' prompt is configured.")

    course_name = _resolve_course_name(state["course_id"])
    extracted_context = str(state.get("extracted_context", "") or "").strip() or "(sin contexto)"

    return (
        prompt_template
        .replace("{{course_name}}", course_name)
        .replace("{{contexto_recuperado}}", extracted_context)
    )


def _render_formatting_prompt(state: AgentState) -> str:

    """Renderiza el prompt de formateo para convertir hallazgos en sugerencias."""

    prompt_template = get_active_prompt("formateo")

    if not prompt_template:

        raise AgentWorkflowError("No active 'formateo' prompt is configured.")

    analysis_output = str(state.get("analysis_output", "") or "").strip() or "(sin hallazgos)"

    return prompt_template.replace("{{hallazgos}}", analysis_output)


def _normalize_tipo(raw_tipo: object) -> str | None:

    """Normaliza el tipo sugerido por el modelo al catalogo permitido."""

    value = str(raw_tipo or "").strip().lower()

    aliases = {
        "redundancia": "redundancia",
        "redundante": "redundancia",
        "deactualizacion": "deactualizacion",
        "desactualizacion": "deactualizacion",
        "obsolescencia": "deactualizacion",
        "conflicto": "conflicto",
        "inconsistencia": "conflicto",
    }
    normalized = aliases.get(value)

    if normalized in ("redundancia", "deactualizacion", "conflicto"):

        return normalized

    return None


def _extract_json_candidates(raw_text: str) -> list[object]:

    """Extrae lista JSON desde respuesta cruda del LLM."""

    content = str(raw_text or "").strip()

    if not content:

        return []

    try:

        parsed = json.loads(content)

        if isinstance(parsed, list):

            return parsed

    except json.JSONDecodeError:

        pass

    match = re.search(r"\[[\s\S]*\]", content)

    if not match:

        return []

    try:

        parsed = json.loads(match.group(0))

    except json.JSONDecodeError:

        return []

    return parsed if isinstance(parsed, list) else []


def _coerce_suggestion(raw_item: object, fallback_context: str) -> dict[str, object] | None:

    """Limpia y valida una sugerencia estructurada."""

    if not isinstance(raw_item, dict):

        return None

    normalized_tipo = _normalize_tipo(raw_item.get("tipo"))

    if not normalized_tipo:

        return None

    input_context = str(raw_item.get("input_context") or "").strip() or fallback_context
    razonamiento = str(raw_item.get("razonamiento") or "").strip()

    if not razonamiento:

        return None

    evidence_ids = raw_item.get("evidencia_ids")

    if not isinstance(evidence_ids, list):

        evidence_ids = []

    return {
        "tipo": normalized_tipo,
        "input_context": input_context,
        "razonamiento": razonamiento,
        "evidencia_ids": [str(item) for item in evidence_ids],
    }


def _analyze_course(state: AgentState) -> dict[str, object]:

    """Nodo de analisis principal sobre el contexto del curso."""

    log_node_input("analyze_course", state)

    try:

        rendered_prompt = _render_analysis_prompt(state)
        log_prompt("analyze_course", rendered_prompt)

        llm = get_agent_llm()
        input_messages = [SystemMessage(content = rendered_prompt)] + list(state.get("messages", []))
        ai_response = llm.invoke(input_messages)

        log_llm_response("analyze_course", ai_response)

        result = {
            "messages": [ai_response],
            "analysis_output": str(getattr(ai_response, "content", "") or "").strip(),
        }
        log_node_output("analyze_course", result)

        return result

    except Exception as e:

        log_agent_error("analyze_course", e)

        raise


def _analysis_next_edge(state: AgentState) -> Literal["generate_suggestions", "end"]:

    """Define arista condicional después del análisis."""

    has_analysis = bool(str(state.get("analysis_output", "") or "").strip())

    return "generate_suggestions" if has_analysis else "end"


def _generate_suggestions(state: AgentState) -> dict[str, object]:

    """Nodo que formatea y persiste sugerencias en estado pendiente."""

    log_node_input("generate_suggestions", state)

    try:

        rendered_prompt = _render_formatting_prompt(state)
        log_prompt("generate_suggestions", rendered_prompt)

        llm = get_agent_llm()
        ai_response = llm.invoke([SystemMessage(content = rendered_prompt)])

        log_llm_response("generate_suggestions", ai_response)

        raw_output = str(getattr(ai_response, "content", "") or "")
        raw_candidates = _extract_json_candidates(raw_output)
        fallback_context = str(state.get("extracted_context", "") or "").strip() or "(sin contexto)"
        normalized_suggestions: list[dict[str, object]] = []

        for raw_item in raw_candidates:

            suggestion_payload = _coerce_suggestion(raw_item, fallback_context)

            if not suggestion_payload:

                continue

            suggestion_id = save_agent_suggestion(
                course_id = int(state["course_id"]),
                conversation_id = str(state.get("conversation_id", "") or "").strip() or None,
                tipo = suggestion_payload["tipo"],
                input_context = suggestion_payload["input_context"],
                razonamiento = suggestion_payload["razonamiento"],
                evidencia_ids = suggestion_payload["evidencia_ids"],
                estado = "pendiente",
            )

            suggestion_payload["id"] = suggestion_id
            normalized_suggestions.append(suggestion_payload)

        result = {
            "messages": [ai_response],
            "suggestions": normalized_suggestions,
        }
        log_node_output("generate_suggestions", result)

        return result

    except Exception as e:

        log_agent_error("generate_suggestions", e)
        
        raise


def build_agent_workflow() -> CompiledStateGraph:
    """Build and compile the base LangGraph workflow for a single turn."""

    workflow = StateGraph(AgentState)
    workflow.add_node("analyze_course", _analyze_course)
    workflow.add_node("generate_suggestions", _generate_suggestions)
    workflow.add_edge(START, "analyze_course")
    workflow.add_conditional_edges(
        "analyze_course",
        _analysis_next_edge,
        {
            "generate_suggestions": "generate_suggestions",
            "end": END,
        },
    )
    workflow.add_edge("generate_suggestions", END)
    return workflow.compile()


def run_agent_once(
    course_id: int,
    messages: list[BaseMessage],
    extracted_context: str = "",
    suggestions: list[dict[str, object]] | None = None,
    conversation_id: str | None = None,
) -> dict[str, object]:
    """Run the base workflow once and return the updated in-memory state."""

    workflow = build_agent_workflow()
    initial_state: AgentState = {
        "messages": list(messages),
        "course_id": int(course_id),
        "conversation_id": str(conversation_id or "").strip(),
        "extracted_context": str(extracted_context or ""),
        "analysis_output": "",
        "suggestions": list(suggestions or []),
    }
    return workflow.invoke(initial_state)
