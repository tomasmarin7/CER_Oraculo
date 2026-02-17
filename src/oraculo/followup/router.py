from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..aplicacion.utiles_prompt import cargar_plantilla_prompt, parsear_json_modelo
from ..config import Settings
from ..providers.llm import generate_answer
from .prompting import render_report_options

GUIDED_FOLLOWUP_ROUTER_PROMPT_FILE = "guided_followup_router.md"
VALID_ACTIONS = {"DETAIL_REPORTS", "NEW_RAG_QUERY", "ASK_SAG", "CHAT_REPLY", "CLARIFY"}
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GuidedFollowupDecision:
    action: str
    rationale: str = ""
    query: str = ""
    sag_product: str = ""
    selected_reports: list[str] | None = None
    selected_report_indexes: list[int] | None = None


def route_guided_followup(
    *,
    last_question: str,
    last_assistant_message: str,
    user_message: str,
    offered_reports: list[dict[str, Any]],
    settings: Settings,
) -> GuidedFollowupDecision:
    started = time.perf_counter()
    prompt = _build_followup_router_prompt(
        last_question=last_question,
        last_assistant_message=last_assistant_message,
        user_message=user_message,
        offered_reports=offered_reports,
    )
    try:
        raw = generate_answer(prompt, settings, system_instruction="", profile="router")
    except Exception:
        return GuidedFollowupDecision(action="CLARIFY", rationale="fallback_error")

    parsed = parsear_json_modelo(raw)
    action = str(parsed.get("action") or "CLARIFY").strip().upper()
    if action not in VALID_ACTIONS:
        action = "CLARIFY"
    raw_selected = parsed.get("selected_reports")
    if isinstance(raw_selected, list):
        selected_reports = [str(item).strip() for item in raw_selected if str(item).strip()]
    elif isinstance(raw_selected, str) and raw_selected.strip():
        selected_reports = [raw_selected.strip()]
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

    decision = GuidedFollowupDecision(
        action=action,
        rationale=str(parsed.get("rationale") or "").strip(),
        query=str(parsed.get("query") or "").strip(),
        sag_product=str(parsed.get("sag_product") or "").strip(),
        selected_reports=selected_reports,
        selected_report_indexes=selected_report_indexes,
    )
    logger.info(
        "🧭 Router follow-up | decisión=%s | motivo=%s | query=%s chars | sag_product=%s | selecciones=%s | indices=%s | tiempo=%sms",
        decision.action,
        decision.rationale or "-",
        len(decision.query),
        decision.sag_product or "-",
        len(decision.selected_reports or []),
        ",".join(str(i) for i in (decision.selected_report_indexes or [])) or "-",
        int((time.perf_counter() - started) * 1000),
    )
    return decision


def _build_followup_router_prompt(
    *,
    last_question: str,
    last_assistant_message: str,
    user_message: str,
    offered_reports: list[dict[str, Any]],
) -> str:
    template = cargar_plantilla_prompt(
        Path(__file__).resolve().parent / "prompts",
        GUIDED_FOLLOWUP_ROUTER_PROMPT_FILE,
    )
    options_block = _render_report_options_indexed(offered_reports)
    return (
        template.replace("{{last_question}}", last_question or "sin pregunta")
        .replace("{{last_assistant_message}}", last_assistant_message or "sin mensaje")
        .replace("{{user_message}}", user_message.strip())
        .replace("{{offered_reports}}", options_block)
    ).strip()


def _render_report_options_indexed(offered_reports: list[dict[str, Any]]) -> str:
    rendered = render_report_options(offered_reports)
    if not rendered:
        return "• Sin opciones detectadas"
    lines = [line.strip() for line in rendered.splitlines() if line.strip()]
    indexed = [f"{i}. {line}" for i, line in enumerate(lines, start=1)]
    return "\n".join(indexed)
