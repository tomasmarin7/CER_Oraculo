from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from google import genai
from google.genai import types

from ..config import Settings

REFINE_MODEL_DEFAULT = "gemini-3-flash-preview"
REFINE_PROMPT_FILE = "refine_question.md"
MAX_QUERY_WORDS = 60
MAX_TOKEN_REPETITIONS = 3
logger = logging.getLogger(__name__)


def _load_prompt_template(filename: str) -> str:
    """Carga template de prompts desde `src/oraculo/providers/prompts/`."""
    prompt_path = Path(__file__).resolve().parent / "prompts" / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _normalize_refined_query(text: str) -> str:
    """
    Limpia salida del modelo para reducir ruido en retrieval.
    """
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ""

    words = normalized.split(" ")
    if len(words) > MAX_QUERY_WORDS:
        words = words[:MAX_QUERY_WORDS]

    # Limita repeticiones excesivas del mismo token.
    counts: dict[str, int] = {}
    filtered: list[str] = []
    for word in words:
        key = re.sub(r"[^\w\-áéíóúÁÉÍÓÚñÑ]", "", word).lower()
        if not key:
            filtered.append(word)
            continue

        count = counts.get(key, 0)
        if count >= MAX_TOKEN_REPETITIONS:
            continue

        counts[key] = count + 1
        filtered.append(word)

    return " ".join(filtered).strip()


def refine_user_question(question: str, settings: Settings) -> str:
    """
    Usa Gemini para reescribir la pregunta del usuario en una consulta
    optimizada para búsqueda vectorial.

    Input:  question (str) — pregunta original del usuario
    Output: str — consulta optimizada (texto plano)
    """
    started = time.perf_counter()
    client = genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=types.HttpOptions(
            timeout=max(int(settings.gemini_refine_timeout_ms), 1000),
        ),
    )
    model_name = (
        settings.gemini_refine_model or REFINE_MODEL_DEFAULT
    ).strip() or REFINE_MODEL_DEFAULT
    system = _load_prompt_template(REFINE_PROMPT_FILE)
    logger.info("🧹 Refiner | enviando consulta para optimizar búsqueda...")

    resp = client.models.generate_content(
        model=model_name,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
        ),
    )

    rewritten = _normalize_refined_query(resp.text or "")
    refined = rewritten if rewritten else question.strip()
    logger.info(
        "✅ Refiner listo | tiempo=%sms | entrada=%s chars | salida=%s chars",
        int((time.perf_counter() - started) * 1000),
        len(question or ""),
        len(refined),
    )
    return refined
