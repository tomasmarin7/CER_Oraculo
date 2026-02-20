"""
Flujo guiado de conversación: dispatch principal.

Orquesta las interacciones CER y SAG delegando a módulos especializados:
  - cer_response: búsqueda de ensayos, detalle y follow-up CER
  - sag_response: búsqueda en base de datos de etiquetas SAG
  - flow_helpers: utilidades compartidas (normalización, serialización, etc.)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..aplicacion.texto_oraculo import INTRO_ORACULO_CER
from ..config import Settings
from ..followup import render_report_options
from ..providers.llm import generate_answer
from ..rag.retriever import retrieve
from ..sources.cer_csv_lookup import build_cer_csv_hints_block
from .cer_response import (
    build_cer_first_response_from_hits,
    generate_cer_detail_followup_response,
    generate_conversational_followup_response,
)
from .flow_helpers import (
    build_followup_clarify_text,
    deserialize_doc_contexts,
    is_affirmative,
    is_negative,
    last_assistant_message,
    looks_like_problem_query,
    render_recent_history,
    serialize_seed_hits,
    serialize_doc_contexts,
)
from .modelos import EstadoSesion, SesionChat
from .sag_response import generate_sag_response

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GuidedFlowResult:
    handled: bool
    response: str = ""
    rag_tag: str = "none"
    sources: list[str] = field(default_factory=list)


def get_guided_intro_text() -> str:
    return INTRO_ORACULO_CER


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def try_handle_guided_flow(
    sesion: SesionChat,
    user_message: str,
    settings: Settings,
    top_k: int = 8,
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    text = (user_message or "").strip()
    if not text:
        return GuidedFlowResult(handled=False)

    if sesion.estado == EstadoSesion.ESPERANDO_DETALLE_PRODUCTO:
        return _handle_product_detail_followup(
            sesion, text, settings, top_k, progress_callback=progress_callback,
        )
    if sesion.estado == EstadoSesion.ESPERANDO_CONFIRMACION_SAG:
        return _handle_sag_followup(sesion, text, settings, progress_callback=progress_callback)
    if sesion.estado in {EstadoSesion.ESPERANDO_PROBLEMA, EstadoSesion.MENU}:
        return _handle_problem_query(sesion, text, settings, top_k, progress_callback=progress_callback)
    if looks_like_problem_query(text):
        return _handle_problem_query(sesion, text, settings, top_k, progress_callback=progress_callback)

    return GuidedFlowResult(handled=False)


def execute_guided_action_from_router(
    sesion: SesionChat,
    user_message: str,
    settings: Settings,
    *,
    action: str,
    query: str = "",
    selected_report_hints: list[str] | None = None,
    selected_report_indexes: list[int] | None = None,
    top_k: int = 8,
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    action_norm = (action or "").strip().upper()
    text = (user_message or "").strip()
    effective_query = (query or text).strip()

    if action_norm == "NEW_CER_QUERY":
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        return _handle_problem_query(sesion, effective_query, settings, top_k, progress_callback=progress_callback)

    if action_norm == "DETAIL_FROM_LIST":
        sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
        return _handle_product_detail_followup(
            sesion,
            text,
            settings,
            top_k,
            forced_action="DETAIL_FROM_LIST",
            selected_report_hints=selected_report_hints,
            selected_report_indexes=selected_report_indexes,
            progress_callback=progress_callback,
        )

    if action_norm == "CHAT_REPLY" and sesion.estado == EstadoSesion.ESPERANDO_DETALLE_PRODUCTO:
        return _handle_product_detail_followup(
            sesion, text, settings, top_k, forced_action="CHAT_REPLY", progress_callback=progress_callback,
        )

    if action_norm == "CLARIFY" and sesion.estado == EstadoSesion.ESPERANDO_DETALLE_PRODUCTO:
        return _handle_product_detail_followup(
            sesion, text, settings, top_k, forced_action="CLARIFY", progress_callback=progress_callback,
        )

    if action_norm == "ASK_SAG":
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        result = generate_sag_response(
            effective_query, settings, user_message=text, product_hint="", progress_callback=progress_callback,
        )
        sesion.flow_data["last_sag_router_context"] = result.router_context
        return GuidedFlowResult(
            handled=result.handled, response=result.response, rag_tag=result.rag_tag, sources=result.sources,
        )

    if action_norm == "CHAT_REPLY":
        contextual_reply = _build_contextual_chat_reply(
            sesion=sesion,
            user_message=text,
            settings=settings,
        )
        if contextual_reply:
            return GuidedFlowResult(handled=True, response=contextual_reply, rag_tag="none")
        return try_handle_guided_flow(sesion, text, settings, top_k=top_k, progress_callback=progress_callback)

    if action_norm == "CLARIFY":
        return try_handle_guided_flow(sesion, text, settings, top_k=top_k, progress_callback=progress_callback)

    return GuidedFlowResult(handled=False)


# ---------------------------------------------------------------------------
# Handlers internos
# ---------------------------------------------------------------------------

def _handle_problem_query(
    sesion: SesionChat,
    question: str,
    settings: Settings,
    top_k: int,
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    if progress_callback:
        progress_callback("Estoy revisando ensayos en la base de datos del CER para tu consulta...")
    logger.info("🔍 Flujo CER | búsqueda de ensayos...")

    csv_hints = build_cer_csv_hints_block(settings.cer_csv_path, question, limit=12)
    enhanced_conversation_context = (
        render_recent_history(sesion, max_items=10)
        + "\n\nSEÑALES_CSV_INICIALES:\n"
        + csv_hints
    )

    _refined_query, hits = retrieve(
        question, settings, top_k=top_k,
        conversation_context=enhanced_conversation_context,
    )
    sesion.flow_data["last_question"] = question
    sesion.flow_data["last_doc_contexts"] = []
    sesion.flow_data["last_detail_doc_contexts"] = []
    sesion.flow_data["last_cer_seed_hits"] = serialize_seed_hits(hits)
    sesion.flow_data["last_sag_router_context"] = ""

    if progress_callback:
        progress_callback("Estoy ordenando los ensayos encontrados para mostrártelos claro...")

    response_text, scenario, report_options, overview_contexts = build_cer_first_response_from_hits(
        question=question,
        refined_query=_refined_query,
        conversation_context=render_recent_history(sesion, max_items=12),
        hits=hits,
        settings=settings,
    )
    logger.info("📝 Flujo CER | listado preparado.")
    sesion.flow_data["offered_reports"] = report_options
    sesion.flow_data["last_doc_contexts"] = serialize_doc_contexts(overview_contexts)
    sesion.flow_data["last_cer_router_context"] = _build_cer_router_context(report_options)
    sesion.flow_data["last_cer_overview_router_context"] = _build_last_search_overview_context(
        report_options=report_options,
        overview_contexts=overview_contexts,
    )

    if report_options:
        sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
    elif scenario == "no_cer":
        sesion.estado = EstadoSesion.ESPERANDO_CONFIRMACION_SAG
    else:
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA

    if not response_text:
        response_text = (
            "No encontré una coincidencia clara en los ensayos CER para tu consulta. "
            "Si quieres, puedo buscar en nuestra base de datos de etiquetas para ese problema. ¿Lo hago?"
        )
        sesion.estado = EstadoSesion.ESPERANDO_CONFIRMACION_SAG

    return GuidedFlowResult(handled=True, response=response_text, rag_tag="cer")


def _handle_product_detail_followup(
    sesion: SesionChat,
    user_message: str,
    settings: Settings,
    top_k: int,
    forced_action: str | None = None,
    selected_report_hints: list[str] | None = None,
    selected_report_indexes: list[int] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    forced = (forced_action or "").strip().upper()
    offered_reports = [
        r for r in sesion.flow_data.get("offered_reports", []) if isinstance(r, dict)
    ]
    if not offered_reports:
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        return GuidedFlowResult(
            handled=True,
            response="Cuéntame nuevamente el problema de tu cultivo y lo revisamos en los ensayos CER.",
        )

    last_detail_items = sesion.flow_data.get("last_detail_doc_contexts") or []
    last_detail_contexts = deserialize_doc_contexts(last_detail_items)

    if forced == "CLARIFY":
        sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
        return GuidedFlowResult(
            handled=True,
            response=build_followup_clarify_text(user_message=user_message, offered_reports=offered_reports),
            rag_tag="none",
        )

    if forced == "CHAT_REPLY":
        base_contexts = last_detail_contexts or deserialize_doc_contexts(
            sesion.flow_data.get("last_doc_contexts") or []
        )
        chat_response = generate_conversational_followup_response(
            last_question=str(sesion.flow_data.get("last_question") or "").strip(),
            last_assistant_message=last_assistant_message(sesion),
            user_message=user_message,
            offered_reports=offered_reports,
            doc_contexts=base_contexts,
            settings=settings,
            progress_callback=progress_callback,
        )
        sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
        return GuidedFlowResult(handled=True, response=chat_response, rag_tag="none")

    # Default: detalle de ensayo (accion DETAIL_FROM_LIST del router global)
    detail_response = generate_cer_detail_followup_response(
        user_message=user_message,
        last_question=str(sesion.flow_data.get("last_question") or "").strip(),
        last_assistant_message=last_assistant_message(sesion),
        offered_reports=offered_reports,
        seed_doc_contexts=last_detail_contexts,
        settings=settings,
        top_k=top_k,
        sesion=sesion,
        selected_report_hints=selected_report_hints or [],
        selected_report_indexes=selected_report_indexes or [],
        progress_callback=progress_callback,
    )
    sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
    return GuidedFlowResult(handled=True, response=detail_response, rag_tag="cer")


def _handle_sag_followup(
    sesion: SesionChat,
    user_message: str,
    settings: Settings,
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    if is_negative(user_message):
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        return GuidedFlowResult(
            handled=True,
            response="Entendido. Si quieres, cuéntame otro problema del cultivo para revisarlo.",
        )
    if not is_affirmative(user_message):
        return GuidedFlowResult(
            handled=True,
            response=(
                "¿Quieres que busque ahora en nuestra base de datos de etiquetas para este problema?\n"
                "Si no, puedes escribirme otra consulta para buscar en ensayos CER."
            ),
            rag_tag="none",
        )

    last_question = str(sesion.flow_data.get("last_question") or "").strip()
    query = f"registro en base de datos de etiquetas y cultivos autorizados para: {last_question}"
    sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
    result = generate_sag_response(
        query, settings, user_message=user_message, product_hint="", progress_callback=progress_callback,
    )
    sesion.flow_data["last_sag_router_context"] = result.router_context
    return GuidedFlowResult(
        handled=result.handled, response=result.response, rag_tag=result.rag_tag, sources=result.sources,
    )


def _build_contextual_chat_reply(
    *,
    sesion: SesionChat,
    user_message: str,
    settings: Settings,
) -> str:
    history = render_recent_history(sesion, max_items=24)
    offered_reports = sesion.flow_data.get("offered_reports") or []
    cer_context = (
        render_report_options(offered_reports, include_inclusion_reason=True)
        if offered_reports else "sin lista CER activa"
    )
    sag_context = str(sesion.flow_data.get("last_sag_router_context") or "").strip() or "sin contexto SAG reciente"

    prompt = (
        "Eres el asistente CER.\n"
        "Responde usando SOLO el contexto entregado.\n"
        "Si el usuario pregunta por qué mostraste una lista, explica el criterio de búsqueda según la conversación.\n"
        "Si pregunta si la lista corresponde a un problema/cultivo, valida con contexto y responde directo.\n"
        "Si no hay evidencia suficiente, pide una aclaración concreta en una sola pregunta.\n\n"
        f"HISTORIAL:\n{history}\n\n"
        f"LISTA_CER_ACTUAL:\n{cer_context}\n\n"
        f"CONTEXTO_SAG_RECIENTE:\n{sag_context}\n\n"
        f"MENSAJE_USUARIO:\n{user_message}\n"
    )
    try:
        return (generate_answer(prompt, settings, system_instruction="", profile="router") or "").strip()
    except Exception:
        return ""


def _build_cer_router_context(report_options: list[dict[str, Any]]) -> str:
    if not report_options:
        return ""
    base = render_report_options(report_options, include_inclusion_reason=True)
    overview_lines: list[str] = []
    for option in report_options[:12]:
        label = str(option.get("label") or "").strip() or "N/D"
        overview = str(option.get("overview") or "").strip()
        if not overview:
            continue
        overview_lines.append(f"• {label} | overview={overview}")
    if not overview_lines:
        return base
    return f"{base}\n\nOVERVIEW_POR_INFORME:\n" + "\n".join(overview_lines)


def _build_last_search_overview_context(
    *,
    report_options: list[dict[str, Any]],
    overview_contexts: list[Any],
    limit: int = 12,
) -> str:
    lines: list[str] = []
    for i, report in enumerate(report_options[:max(1, int(limit))], start=1):
        label = str(report.get("label") or "").strip() or "N/D"
        overview = str(report.get("overview") or "").strip()
        if not overview:
            # fallback corto desde contextos si faltara resumen en la opcion
            if i - 1 < len(overview_contexts):
                ctx = overview_contexts[i - 1]
                chunks = getattr(ctx, "chunks", []) if ctx is not None else []
                for ch in chunks:
                    txt = str((ch or {}).get("text") or "").strip()
                    if txt:
                        overview = re.sub(r"\s+", " ", txt)[:260]
                        break
        lines.append(f"{i}. {label} | overview={overview or 'N/D'}")
    return "\n".join(lines) if lines else "sin overview CER reciente"
