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
    decision = GlobalRouterDecision(action=action, query=query, rationale=rationale)
    logger.info(
        "🧭 Router global | decisión=%s | motivo=%s | query=%s chars | tiempo=%sms",
        decision.action,
        decision.rationale or "-",
        len(decision.query),
        int((time.perf_counter() - started) * 1000),
    )
    return decision


def _fallback_action(text: str) -> str:
    return "CLARIFY"


def _build_global_router_prompt(sesion: SesionChat, user_message: str) -> str:
    template = cargar_plantilla_prompt(
        Path(__file__).resolve().parent / "prompts",
        GLOBAL_ROUTER_PROMPT_FILE,
    )
    offered_reports = sesion.flow_data.get("offered_reports") or []
    reports_lines: list[str] = []
    for report in offered_reports:
        if not isinstance(report, dict):
            continue
        label = str(report.get("label") or "").strip()
        products = [str(p).strip() for p in (report.get("products") or []) if str(p).strip()]
        reports_lines.append(f"• {label}: {', '.join(products)}")

    offered_reports_text = "\n".join(reports_lines) if reports_lines else "sin opciones"
    return (
        template.replace("{{estado_actual}}", str(sesion.estado))
        .replace("{{last_rag_used}}", sesion.last_rag_used)
        .replace("{{last_question}}", str(sesion.flow_data.get("last_question") or ""))
        .replace("{{offered_reports}}", offered_reports_text)
        .replace("{{historial}}", historial_corto(sesion))
        .replace("{{mensaje_usuario}}", user_message)
    ).strip()
