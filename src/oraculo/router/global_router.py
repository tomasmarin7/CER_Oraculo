from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..aplicacion.utiles_prompt import cargar_plantilla_prompt, parsear_json_modelo
from ..config import Settings
from ..conversation.modelos import SesionChat
from ..conversation.texto import historial_corto, limpiar_texto
from ..providers.llm import generate_answer

GLOBAL_ROUTER_PROMPT_FILE = "global_router.md"
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GlobalRouterDecision:
    action: str
    query: str = ""
    rationale: str = ""
    selected_reports: list[str] | None = None
    selected_report_indexes: list[int] | None = None


VALID_ACTIONS = {
    "ASK_PROBLEM",
    "NEW_CER_QUERY",
    "DETAIL_FROM_LIST",
    "ASK_SAG",
    "CHAT_REPLY",
    "CLARIFY",
}


def route_global_action(
    sesion: SesionChat,
    user_message: str,
    settings: Settings,
    progress_callback: Callable[[str], None] | None = None,
) -> GlobalRouterDecision:
    started = time.perf_counter()
    text = limpiar_texto(user_message)

    prompt = _build_global_router_prompt(sesion, text)
    try:
        if progress_callback:
            progress_callback("Estoy entendiendo mejor tu pedido para responderte con precisión...")
        raw = generate_answer(prompt, settings, system_instruction="", profile="router")
        parsed = parsear_json_modelo(raw)
    except Exception:
        parsed = {}

    action = str(parsed.get("action") or "").strip().upper()
    if action not in VALID_ACTIONS:
        action = _fallback_action(text)

    query = limpiar_texto(str(parsed.get("query") or ""))
    rationale = limpiar_texto(str(parsed.get("rationale") or ""))
    raw_selected = parsed.get("selected_reports")
    if isinstance(raw_selected, list):
        selected_reports = [limpiar_texto(str(item)) for item in raw_selected if limpiar_texto(str(item))]
    elif isinstance(raw_selected, str) and limpiar_texto(raw_selected):
        selected_reports = [limpiar_texto(raw_selected)]
    else:
        selected_reports = []

    raw_indexes = parsed.get("selected_report_indexes")
    selected_report_indexes: list[int] = []
    if isinstance(raw_indexes, list):
        for item in raw_indexes:
            try:
                idx = int(item)
            except Exception:
                continue
            if idx > 0:
                selected_report_indexes.append(idx)
    elif isinstance(raw_indexes, int) and raw_indexes > 0:
        selected_report_indexes = [int(raw_indexes)]
    selected_report_indexes = sorted(set(selected_report_indexes))

    decision = GlobalRouterDecision(
        action=action,
        query=query,
        rationale=rationale,
        selected_reports=selected_reports,
        selected_report_indexes=selected_report_indexes,
    )
    decision = _normalize_ambiguous_detail_decision(decision)
    decision = _normalize_broad_problem_or_crop_query(decision, text, sesion)
    logger.info(
        "🧭 Router global | decisión=%s | motivo=%s | query=%s chars | selecciones=%s | indices=%s | tiempo=%sms",
        decision.action,
        decision.rationale or "-",
        len(decision.query),
        len(decision.selected_reports or []),
        ",".join(str(i) for i in (decision.selected_report_indexes or [])) or "-",
        int((time.perf_counter() - started) * 1000),
    )
    return decision


def _normalize_ambiguous_detail_decision(decision: GlobalRouterDecision) -> GlobalRouterDecision:
    if decision.action != "DETAIL_FROM_LIST":
        return decision
    has_indexes = bool(decision.selected_report_indexes)
    has_reports = bool(decision.selected_reports)
    if has_indexes or has_reports:
        return decision
    return GlobalRouterDecision(
        action="CLARIFY",
        query="",
        rationale=decision.rationale or "detalle ambiguo sin selección clara",
        selected_reports=[],
        selected_report_indexes=[],
    )


def _normalize_broad_problem_or_crop_query(
    decision: GlobalRouterDecision,
    text: str,
    sesion: SesionChat,
) -> GlobalRouterDecision:
    if decision.action != "CLARIFY":
        return decision

    normalized = text.lower()
    broad_scope_tokens = (
        "cualquier cultivo",
        "cualquier especie",
        "todos los cultivos",
        "todas las especies",
        "en general",
    )
    technical_tokens = (
        "ensayo",
        "ensayos",
        "producto",
        "productos",
        "plaga",
        "enfermedad",
        "problema",
        "calibre",
        "dosis",
        "eficacia",
        "resultado",
        "resultados",
        "manejo",
    )

    looks_broad_scope = any(tok in normalized for tok in broad_scope_tokens)
    looks_technical = any(tok in normalized for tok in technical_tokens)

    # Caso 1: consulta técnica explícita sin cultivo específico -> permitir búsqueda CER transversal.
    if looks_technical:
        return GlobalRouterDecision(
            action="NEW_CER_QUERY",
            query=text,
            rationale="consulta técnica válida sin cultivo específico",
            selected_reports=[],
            selected_report_indexes=[],
        )

    # Caso 2: respuesta de alcance ("de cualquier cultivo") tras una clarificación.
    if looks_broad_scope:
        recovered = _recover_recent_technical_user_intent(sesion)
        query = recovered or text
        return GlobalRouterDecision(
            action="NEW_CER_QUERY",
            query=query,
            rationale="alcance general confirmado para búsqueda CER",
            selected_reports=[],
            selected_report_indexes=[],
        )

    return decision


def _recover_recent_technical_user_intent(sesion: SesionChat) -> str:
    for msg in reversed(sesion.mensajes):
        if msg.rol != "user":
            continue
        candidate = limpiar_texto(msg.texto)
        if not candidate:
            continue
        low = candidate.lower()
        if any(tok in low for tok in ("hola", "buenas", "gracias", "ok", "dale", "si", "sí", "no")) and len(low.split()) <= 2:
            continue
        if any(tok in low for tok in ("ensayo", "ensayos", "producto", "productos", "plaga", "enfermedad", "calibre", "problema", "dosis", "eficacia", "resultado", "manejo")):
            return candidate
    return ""


def _fallback_action(text: str) -> str:
    return "CLARIFY"


def _build_global_router_prompt(sesion: SesionChat, user_message: str) -> str:
    template = cargar_plantilla_prompt(
        Path(__file__).resolve().parent / "prompts",
        GLOBAL_ROUTER_PROMPT_FILE,
    )
    offered_reports = sesion.flow_data.get("offered_reports") or []
    reports_lines: list[str] = []
    for i, report in enumerate(offered_reports, start=1):
        if not isinstance(report, dict):
            continue
        label = str(report.get("label") or "").strip()
        products = [str(p).strip() for p in (report.get("products") or []) if str(p).strip()]
        overview = str(report.get("overview") or "").strip()
        line = f"{i}. • {label}: {', '.join(products)}"
        if overview:
            line += f" | overview={overview}"
        reports_lines.append(line)

    offered_reports_text = "\n".join(reports_lines) if reports_lines else "sin opciones"
    history_items = max(1, len(sesion.mensajes))
    cer_router_context = str(sesion.flow_data.get("last_cer_router_context") or "").strip() or "sin contexto CER estructurado"
    cer_overview_context = (
        str(sesion.flow_data.get("last_cer_overview_router_context") or "").strip()
        or "sin overview CER reciente"
    )
    sag_router_context = str(sesion.flow_data.get("last_sag_router_context") or "").strip() or "sin contexto SAG estructurado"
    return (
        template.replace("{{estado_actual}}", str(sesion.estado))
        .replace("{{last_rag_used}}", sesion.last_rag_used)
        .replace("{{last_question}}", str(sesion.flow_data.get("last_question") or ""))
        .replace("{{last_assistant_message}}", _last_assistant_message(sesion))
        .replace("{{cer_router_context}}", cer_router_context)
        .replace("{{cer_overview_context}}", cer_overview_context)
        .replace("{{sag_router_context}}", sag_router_context)
        .replace("{{offered_reports}}", offered_reports_text)
        .replace("{{historial}}", historial_corto(sesion, max_items=history_items))
        .replace("{{mensaje_usuario}}", user_message)
    ).strip()


def _last_assistant_message(sesion: SesionChat) -> str:
    for msg in reversed(sesion.mensajes):
        if msg.rol == "assistant":
            return limpiar_texto(msg.texto)
    return ""
