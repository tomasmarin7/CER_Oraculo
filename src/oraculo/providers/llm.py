from __future__ import annotations

import logging
import time
from typing import List

from google import genai
from google.genai import types

from ..config import Settings

GEN_MODEL_DEFAULT = "gemini-3-pro-preview"
GEN_MODEL_FALLBACK_DEFAULT = "gemini-2.5-flash"
logger = logging.getLogger(__name__)


def _candidate_models(settings: Settings, profile: str) -> List[str]:
    if profile == "router":
        primary = (
            settings.gemini_router_model
            or settings.gemini_model
            or GEN_MODEL_FALLBACK_DEFAULT
        ).strip() or GEN_MODEL_FALLBACK_DEFAULT
        fallback = (
            settings.gemini_router_fallback_model
            or settings.gemini_fallback_model
            or GEN_MODEL_FALLBACK_DEFAULT
        ).strip() or GEN_MODEL_FALLBACK_DEFAULT
    elif profile == "complex":
        primary = (
            settings.gemini_complex_model
            or settings.gemini_model
            or GEN_MODEL_DEFAULT
        ).strip() or GEN_MODEL_DEFAULT
        fallback = (
            settings.gemini_complex_fallback_model
            or settings.gemini_fallback_model
            or GEN_MODEL_FALLBACK_DEFAULT
        ).strip() or GEN_MODEL_FALLBACK_DEFAULT
    else:
        primary = (settings.gemini_model or GEN_MODEL_DEFAULT).strip() or GEN_MODEL_DEFAULT
        fallback = (
            settings.gemini_fallback_model or GEN_MODEL_FALLBACK_DEFAULT
        ).strip() or GEN_MODEL_FALLBACK_DEFAULT
    if primary == fallback:
        return [primary]
    return [primary, fallback]


def _extract_text(resp: object) -> str:
    """
    Extrae texto de forma robusta desde la respuesta de Gemini.
    `resp.text` a veces llega incompleto; este extractor intenta
    reconstruir el contenido completo desde candidates/parts.
    """
    text = (getattr(resp, "text", None) or "").strip()

    parts_joined: list[str] = []
    try:
        candidates = getattr(resp, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                ptext = getattr(part, "text", None)
                if isinstance(ptext, str) and ptext.strip():
                    parts_joined.append(ptext.strip())
    except Exception:
        pass

    merged = "\n".join(parts_joined).strip()
    if merged and len(merged) > len(text):
        return merged
    return text


def _profile_params(settings: Settings, profile: str) -> dict[str, int | float]:
    if profile == "router":
        return {
            "timeout_ms": max(int(settings.gemini_router_timeout_ms), 1000),
            "max_output_tokens": max(int(settings.gemini_router_max_output_tokens), 128),
            "thinking_budget": max(int(settings.gemini_router_thinking_budget), 0),
            "temperature": 0.0,
        }
    if profile == "complex":
        return {
            "timeout_ms": max(int(settings.gemini_complex_timeout_ms), 1000),
            "max_output_tokens": max(int(settings.gemini_complex_max_output_tokens), 128),
            "thinking_budget": max(int(settings.gemini_complex_thinking_budget), 0),
            "temperature": 0.25,
        }
    return {
        "timeout_ms": max(int(settings.gemini_timeout_ms), 1000),
        "max_output_tokens": max(int(settings.gemini_max_output_tokens), 128),
        "thinking_budget": max(int(settings.gemini_thinking_budget), 0),
        "temperature": 0.3,
    }


def generate_answer(
    prompt: str,
    settings: Settings,
    system_instruction: str = "",
    profile: str = "default",
) -> str:
    started = time.perf_counter()
    params = _profile_params(settings, profile)
    client = genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=types.HttpOptions(
            timeout=int(params["timeout_ms"]),
        ),
    )

    # Configurar parámetros base
    config_params = {
        "temperature": float(params["temperature"]),
        "thinking_config": types.ThinkingConfig(
            thinking_budget=int(params["thinking_budget"]),
            include_thoughts=False,
        ),
    }
    if int(params["max_output_tokens"]) > 0:
        config_params["max_output_tokens"] = int(params["max_output_tokens"])

    # Solo agregar system_instruction si no está vacío
    if system_instruction and system_instruction.strip():
        config_params["system_instruction"] = system_instruction

    errors: list[str] = []
    for model_name in _candidate_models(settings, profile):
        model_started = time.perf_counter()
        logger.info(
            "🧠 Gemini (%s) | perfil=%s | enviando prompt (%s chars)...",
            model_name,
            profile,
            len(prompt),
        )
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(**config_params),
            )
            text = _extract_text(resp)
            if text:
                elapsed_ms = int((time.perf_counter() - model_started) * 1000)
                total_ms = int((time.perf_counter() - started) * 1000)
                logger.info(
                    "✅ Gemini respondió | perfil=%s | modelo=%s | tiempo_modelo=%sms | tiempo_total=%sms | salida=%s chars",
                    profile,
                    model_name,
                    elapsed_ms,
                    total_ms,
                    len(text),
                )
                return text
            errors.append(f"{model_name}: respuesta vacia")
            logger.warning(
                "⚠️ Gemini devolvió vacío | perfil=%s | modelo=%s | tiempo=%sms",
                profile,
                model_name,
                int((time.perf_counter() - model_started) * 1000),
            )
        except Exception as exc:
            errors.append(f"{model_name}: {type(exc).__name__}: {exc}")
            logger.warning(
                "❌ Error en Gemini | perfil=%s | modelo=%s | tiempo=%sms | error=%s",
                profile,
                model_name,
                int((time.perf_counter() - model_started) * 1000),
                type(exc).__name__,
            )

    raise RuntimeError(
        "No se pudo generar respuesta con los modelos configurados. "
        + " | ".join(errors)
    )
