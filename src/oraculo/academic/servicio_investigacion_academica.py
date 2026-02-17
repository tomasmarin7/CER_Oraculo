from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from ..aplicacion.utiles_prompt import cargar_plantilla_prompt
from ..config import Settings
from ..providers.llm import generate_answer
from ..providers.edison import EdisonProviderError, run_literature_task
from .formatting import format_academic_response_for_telegram
from .modelos import AcademicResearchResult

logger = logging.getLogger(__name__)
PROMPT_ORGANIZAR_INFORME_FILE = "organizar_informe_telegram.md"
MIN_REORDERED_CHARS = 800
INVALID_REORDER_PATTERNS = (
    "por favor, proporciona",
    "por favor proporciona",
    "proporciona el informe",
    "para que pueda procesarlo",
    "no puedo",
    "faltan datos",
)


@dataclass(slots=True)
class ServicioInvestigacionAcademica:
    def ejecutar_consulta(
        self,
        *,
        query: str,
        settings: Settings,
    ) -> AcademicResearchResult:
        texto = (query or "").strip()
        if not texto:
            raise ValueError("Escribe una consulta para investigar.")

        response = run_literature_task(query=texto, settings=settings)
        base_text = response.formatted_answer or response.answer
        if not base_text.strip():
            logger.warning(
                "🎓 Servicio academico | Edison sin texto util | task_id=%s | success=%s",
                response.task_id or "-",
                response.has_successful_answer,
            )
        ordered_text = _ordenar_informe_para_telegram(
            raw_report=base_text,
            user_query=texto,
            settings=settings,
        )
        formatted = format_academic_response_for_telegram(ordered_text)
        return AcademicResearchResult(
            text=formatted,
            task_id=response.task_id,
            has_successful_answer=response.has_successful_answer,
        )


class AcademicResearchServiceError(RuntimeError):
    pass


def map_service_error(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, EdisonProviderError):
        return (
            "No pude completar la investigación académica en este momento. "
            "Intenta nuevamente en unos minutos."
        )
    return "Ocurrió un error procesando la investigación académica."


def _ordenar_informe_para_telegram(
    *,
    raw_report: str,
    user_query: str,
    settings: Settings,
) -> str:
    content = (raw_report or "").strip()
    if not content:
        return raw_report

    try:
        template = cargar_plantilla_prompt(
            Path(__file__).resolve().parent / "prompts",
            PROMPT_ORGANIZAR_INFORME_FILE,
        )
        prompt = (
            template.replace("{{user_query}}", user_query.strip())
            .replace("{{raw_report}}", content)
            .strip()
        )
        logger.info(
            "🎓 Reordenando informe academico con Gemini | entrada=%s chars",
            len(content),
        )
        ordered = (
            generate_answer(
                prompt,
                settings,
                system_instruction=(
                    "Debes devolver un informe final completo y legible. "
                    "No pidas mas informacion, no hagas preguntas, no converses. "
                    "Reorganiza unicamente el contenido recibido."
                ),
                profile="complex",
            )
            or ""
        ).strip()
        if _is_valid_reordered_report(ordered, original=content):
            logger.info(
                "🎓 Informe academico reordenado | salida=%s chars",
                len(ordered),
            )
            return ordered
        logger.warning(
            "🎓 Reordenamiento descartado por salida invalida | salida=%s chars",
            len(ordered),
        )
    except Exception:
        logger.exception("No se pudo reordenar el informe academico con Gemini.")
    return content


def _is_valid_reordered_report(candidate: str, *, original: str) -> bool:
    text = (candidate or "").strip()
    if not text:
        return False
    if len(original) >= 2500 and len(text) < MIN_REORDERED_CHARS:
        return False
    lower = text.lower()
    if any(pattern in lower for pattern in INVALID_REORDER_PATTERNS):
        return False
    if "referencias" not in lower and "references" not in lower:
        return False
    return True
