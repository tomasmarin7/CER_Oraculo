from __future__ import annotations

from pathlib import Path
from typing import Any

GUIDED_DETAIL_FOLLOWUP_PROMPT_FILE = "guided_detail_followup.md"
GUIDED_CHAT_FOLLOWUP_PROMPT_FILE = "guided_chat_followup.md"


def build_detail_followup_prompt(
    *,
    last_question: str,
    last_assistant_message: str,
    user_message: str,
    offered_reports: list[dict[str, Any]],
    context_block: str,
) -> str:
    template = _load_prompt_template(GUIDED_DETAIL_FOLLOWUP_PROMPT_FILE)
    options_block = render_report_options(offered_reports) or "• Sin opciones detectadas"
    return (
        template.replace("{{last_question}}", last_question or "sin pregunta")
        .replace("{{last_assistant_message}}", last_assistant_message or "sin mensaje")
        .replace("{{user_message}}", user_message.strip())
        .replace("{{offered_reports}}", options_block)
        .replace("{{context_block}}", context_block)
    ).strip()


def build_followup_chat_prompt(
    *,
    last_question: str,
    last_assistant_message: str,
    user_message: str,
    offered_reports: list[dict[str, Any]],
    context_block: str,
) -> str:
    template = _load_prompt_template(GUIDED_CHAT_FOLLOWUP_PROMPT_FILE)
    options_block = render_report_options(offered_reports) or "• Sin opciones detectadas"
    return (
        template.replace("{{last_question}}", last_question or "sin pregunta")
        .replace("{{last_assistant_message}}", last_assistant_message or "sin mensaje")
        .replace("{{user_message}}", user_message.strip())
        .replace("{{offered_reports}}", options_block)
        .replace("{{context_block}}", context_block)
    ).strip()


def render_report_options(report_options: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for option in report_options:
        label = str(option.get("label") or "").strip()
        products = [str(p).strip() for p in (option.get("products") or []) if str(p).strip()]
        if not label:
            continue
        if products:
            lines.append(f"• {label}: {', '.join(products)}")
        else:
            lines.append(f"• {label}")
    return "\n".join(lines)


def _load_prompt_template(filename: str) -> str:
    prompt_path = Path(__file__).resolve().parent / "prompts" / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()
