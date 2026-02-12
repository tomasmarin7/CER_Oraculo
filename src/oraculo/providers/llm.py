from __future__ import annotations

from typing import List

from google import genai
from google.genai import types

from ..config import Settings

GEN_MODEL_DEFAULT = "gemini-3-pro-preview"
GEN_MODEL_FALLBACK_DEFAULT = "gemini-2.5-flash"


def _candidate_models(settings: Settings) -> List[str]:
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


def generate_answer(prompt: str, settings: Settings, system_instruction: str = "") -> str:
    client = genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=types.HttpOptions(
            timeout=max(int(settings.gemini_timeout_ms), 1000),
        ),
    )

    # Configurar parámetros base
    config_params = {
        "temperature": 0.3,
        "thinking_config": types.ThinkingConfig(
            thinking_budget=max(int(settings.gemini_thinking_budget), 0),
            include_thoughts=False,
        ),
    }
    if int(settings.gemini_max_output_tokens) > 0:
        config_params["max_output_tokens"] = max(int(settings.gemini_max_output_tokens), 128)

    # Solo agregar system_instruction si no está vacío
    if system_instruction and system_instruction.strip():
        config_params["system_instruction"] = system_instruction

    errors: list[str] = []
    for model_name in _candidate_models(settings):
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(**config_params),
            )
            text = _extract_text(resp)
            if text:
                return text
            errors.append(f"{model_name}: respuesta vacia")
        except Exception as exc:
            errors.append(f"{model_name}: {type(exc).__name__}: {exc}")

    raise RuntimeError(
        "No se pudo generar respuesta con los modelos configurados. "
        + " | ".join(errors)
    )
