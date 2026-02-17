from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from ..config import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EdisonLiteratureResponse:
    answer: str
    formatted_answer: str
    has_successful_answer: bool | None
    task_id: str | None


class EdisonProviderError(RuntimeError):
    """Error controlado en integración con Edison."""


def run_literature_task(
    *,
    query: str,
    settings: Settings,
    continued_job_id: str | None = None,
) -> EdisonLiteratureResponse:
    # Evita fetch remoto del cost-map de LiteLLM (ruido en entornos sin red).
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
    # Reduce mensajes informativos de observabilidad opcional de edison-client.
    logging.getLogger("edison_client.utils.monitoring").setLevel(logging.WARNING)

    api_key = settings.edison_api_key.get_secret_value() if settings.edison_api_key else ""
    if not api_key:
        raise EdisonProviderError("EDISON_API_KEY no está configurada.")

    try:
        from edison_client import EdisonClient, JobNames
    except Exception as exc:  # pragma: no cover
        raise EdisonProviderError(
            "No se pudo importar edison_client. Instala la dependencia edison-client."
        ) from exc

    payload: dict[str, object] = {
        "name": JobNames.LITERATURE,
        "query": (query or "").strip(),
    }
    if continued_job_id:
        payload["runtime_config"] = {"continued_job_id": continued_job_id}

    try:
        logger.info(
            "📚 Edison literature | enviando tarea | query=%s chars | continued=%s",
            len(str(payload.get("query") or "")),
            bool(continued_job_id),
        )
        client = EdisonClient(api_key=api_key)
        raw_response = client.run_tasks_until_done(payload)
    except Exception as exc:
        logger.exception("Error al ejecutar Literature en Edison")
        raise EdisonProviderError("No se pudo completar la investigación en Edison.") from exc

    response = _pick_first_task_response(raw_response)
    if response is None:
        logger.warning("📚 Edison literature | respuesta vacia (None)")
        raise EdisonProviderError("Edison devolvió una respuesta vacía.")

    answer = str(getattr(response, "answer", "") or "").strip()
    formatted_answer = str(getattr(response, "formatted_answer", "") or "").strip()
    has_successful_answer = getattr(response, "has_successful_answer", None)
    task_id = (
        str(getattr(response, "task_id", "") or "")
        or str(getattr(response, "id", "") or "")
        or None
    )
    logger.info(
        "📚 Edison literature | tarea completada | task_id=%s | answer=%s chars | formatted=%s chars | success=%s",
        task_id or "-",
        len(answer),
        len(formatted_answer),
        has_successful_answer,
    )

    return EdisonLiteratureResponse(
        answer=answer,
        formatted_answer=formatted_answer,
        has_successful_answer=has_successful_answer,
        task_id=task_id,
    )


def _pick_first_task_response(raw_response: object) -> object | None:
    if isinstance(raw_response, list):
        if not raw_response:
            return None
        return raw_response[0]
    return raw_response
