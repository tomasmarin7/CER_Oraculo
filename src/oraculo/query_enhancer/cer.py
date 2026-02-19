from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types

from ..config import Settings
from ..sources.cer_csv_lookup import (
    build_cer_csv_hints_block,
    detect_cer_entities,
    find_cer_records_by_query,
)

ENHANCER_MODEL_DEFAULT = "gemini-3-flash-preview"
ENHANCER_FALLBACK_MODEL_DEFAULT = "gemini-2.5-flash"
MAX_QUERY_WORDS = 70
MAX_TOKEN_REPETITIONS = 4
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CerQueryEnhancement:
    enhanced_query: str
    matched_records_count: int
    csv_signals: dict[str, set[str]]
    exhaustive_hint: bool


def _normalize_query(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return ""

    words = normalized.split(" ")
    if len(words) > MAX_QUERY_WORDS:
        words = words[:MAX_QUERY_WORDS]

    counts: dict[str, int] = {}
    filtered: list[str] = []
    for word in words:
        key = re.sub(r"[^\w\-áéíóúÁÉÍÓÚñÑ]", "", word).lower()
        if not key:
            continue
        count = counts.get(key, 0)
        if count >= MAX_TOKEN_REPETITIONS:
            continue
        counts[key] = count + 1
        filtered.append(word)

    return " ".join(filtered).strip()


def _load_prompt_template() -> str:
    prompt_path = Path(__file__).resolve().parent / "cer_prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _is_exhaustive_intent(text: str) -> bool:
    norm = str(text or "").lower()
    return any(
        token in norm
        for token in (
            "todos los ensayos",
            "todas las pruebas",
            "todos los estudios",
            "todos los informes",
            "todos de",
            "todas de",
            "listado completo",
            "lista completa",
            "dame todos",
            "muestrame todos",
        )
    )


def _render_enhancer_input(*, user_message: str, conversation_context: str, csv_hints: str) -> str:
    prompt = _load_prompt_template()
    return (
        prompt.replace("{{user_message}}", (user_message or "").strip())
        .replace("{{conversation_context}}", (conversation_context or "").strip() or "(sin contexto adicional)")
        .replace("{{csv_hints_block}}", csv_hints)
    )


def enhance_cer_query(
    *,
    user_message: str,
    settings: Settings,
    conversation_context: str = "",
) -> CerQueryEnhancement:
    started = time.perf_counter()
    base_query = (user_message or "").strip()
    if not base_query:
        return CerQueryEnhancement("", 0, {}, False)

    combined_text = " ".join(part for part in [base_query, conversation_context] if str(part).strip())
    csv_hints = build_cer_csv_hints_block(settings.cer_csv_path, combined_text, limit=12)
    matched_records = find_cer_records_by_query(settings.cer_csv_path, combined_text, limit=60)
    csv_signals = detect_cer_entities(settings.cer_csv_path, combined_text)
    exhaustive_hint = _is_exhaustive_intent(combined_text)

    client = genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=types.HttpOptions(timeout=max(int(settings.gemini_refine_timeout_ms), 1000)),
    )
    model_name = (settings.gemini_refine_model or ENHANCER_MODEL_DEFAULT).strip() or ENHANCER_MODEL_DEFAULT
    fallback_model = (settings.gemini_fallback_model or ENHANCER_FALLBACK_MODEL_DEFAULT).strip() or ENHANCER_FALLBACK_MODEL_DEFAULT
    enhancer_input = _render_enhancer_input(
        user_message=base_query,
        conversation_context=conversation_context,
        csv_hints=csv_hints,
    )

    models: list[str] = [model_name]
    if fallback_model and fallback_model != model_name:
        models.append(fallback_model)

    for current_model in models:
        model_started = time.perf_counter()
        try:
            resp = client.models.generate_content(
                model=current_model,
                contents=enhancer_input,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                ),
            )
            enhanced = _normalize_query(resp.text or "")
            if not enhanced:
                enhanced = _normalize_query(base_query)
            logger.info(
                "✅ QueryEnhancer CER | modelo=%s | tiempo_modelo=%sms | total=%sms | csv_matches=%s | exhaustive=%s",
                current_model,
                int((time.perf_counter() - model_started) * 1000),
                int((time.perf_counter() - started) * 1000),
                len(matched_records),
                exhaustive_hint,
            )
            return CerQueryEnhancement(
                enhanced_query=enhanced,
                matched_records_count=len(matched_records),
                csv_signals=csv_signals,
                exhaustive_hint=exhaustive_hint,
            )
        except Exception as exc:
            logger.warning(
                "⚠️ QueryEnhancer CER falló | modelo=%s | tiempo=%sms | error=%s",
                current_model,
                int((time.perf_counter() - model_started) * 1000),
                type(exc).__name__,
            )

    fallback = _normalize_query(base_query)
    logger.warning(
        "⏭️ QueryEnhancer CER omitido por error | total=%sms | csv_matches=%s",
        int((time.perf_counter() - started) * 1000),
        len(matched_records),
    )
    return CerQueryEnhancement(
        enhanced_query=fallback,
        matched_records_count=len(matched_records),
        csv_signals=csv_signals,
        exhaustive_hint=exhaustive_hint,
    )
