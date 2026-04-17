from __future__ import annotations

import os
from typing import TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AnyMessage, BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import Annotated

from app import get_active_prompt, get_db_connection
from observability import (
    log_node_input,
    log_node_output,
    log_prompt,
    log_llm_response,
    log_agent_error,
)

load_dotenv()

DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


class AgentWorkflowError(RuntimeError):
    """Raised when the agent workflow cannot run safely."""


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    course_id: int
    extracted_context: str
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


def _serialize_chat_history(messages: list[BaseMessage]) -> str:
    """Render prior chat messages into a plain-text trace for the active prompt."""

    if not messages:
        return "(sin historial)"

    serialized_messages: list[str] = []

    for message in messages:
        role = getattr(message, "type", message.__class__.__name__.lower())
        content = getattr(message, "content", "")
        serialized_messages.append(f"{role}: {content}")

    return "\n".join(serialized_messages)


def _render_chat_prompt(state: AgentState) -> str:
    """Render the active chat prompt with the current workflow state."""

    prompt_template = get_active_prompt("chat")

    if not prompt_template:
        raise AgentWorkflowError("No active 'chat' prompt is configured.")

    course_name = _resolve_course_name(state["course_id"])
    extracted_context = str(state.get("extracted_context", "") or "").strip() or "(sin contexto)"
    chat_history = _serialize_chat_history(list(state.get("messages", [])))

    return (
        prompt_template
        .replace("{{course_name}}", course_name)
        .replace("{{historial_chat}}", chat_history)
        .replace("{{contexto_recuperado}}", extracted_context)
    )


def _llm_call(state: AgentState) -> dict[str, object]:
    """Execute the minimal one-node agent turn with full observability logging."""

    # Registrar entrada al nodo
    log_node_input("llm_call", state)

    try:
        rendered_prompt = _render_chat_prompt(state)

        # Registrar prompt enviado al LLM
        log_prompt("llm_call", rendered_prompt)

        llm = get_agent_llm()
        input_messages = [SystemMessage(content=rendered_prompt)] + list(state.get("messages", []))
        ai_response = llm.invoke(input_messages)

        # Registrar respuesta cruda del LLM
        log_llm_response("llm_call", ai_response)

        result = {
            "messages": [ai_response],
            "suggestions": list(state.get("suggestions", [])),
        }

        # Registrar salida del nodo
        log_node_output("llm_call", result)

        return result

    except Exception as e:
        log_agent_error("llm_call", e)
        raise


def build_agent_workflow() -> CompiledStateGraph:
    """Build and compile the base LangGraph workflow for a single turn."""

    workflow = StateGraph(AgentState)
    workflow.add_node("llm_call", _llm_call)
    workflow.add_edge(START, "llm_call")
    workflow.add_edge("llm_call", END)
    return workflow.compile()


def run_agent_once(
    course_id: int,
    messages: list[BaseMessage],
    extracted_context: str = "",
    suggestions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Run the base workflow once and return the updated in-memory state."""

    workflow = build_agent_workflow()
    initial_state: AgentState = {
        "messages": list(messages),
        "course_id": int(course_id),
        "extracted_context": str(extracted_context or ""),
        "suggestions": list(suggestions or []),
    }
    return workflow.invoke(initial_state)