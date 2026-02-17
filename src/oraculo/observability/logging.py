"""
Configuración de logging para el Oráculo Agrónomo CER.
"""
from contextlib import contextmanager
import contextvars
import os
import logging
import sys

_LOG_ACTOR_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "oraculo_log_actor_id",
    default="-",
)


class _ActorContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.actor_id = _LOG_ACTOR_ID.get("-")
        return True


@contextmanager
def log_actor_context(actor_id: str):
    token = _LOG_ACTOR_ID.set((actor_id or "-").strip() or "-")
    try:
        yield
    finally:
        _LOG_ACTOR_ID.reset(token)


def _resolve_level(default_level: int) -> int:
    raw = (os.getenv("ORACULO_LOG_LEVEL") or "").strip().upper()
    if not raw:
        return default_level
    return getattr(logging, raw, default_level)


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configura el logging básico para la aplicación.
    
    Args:
        level: Nivel de logging (default: INFO)
    """
    resolved = _resolve_level(level)
    logging.basicConfig(
        level=resolved,
        format="%(asctime)s | %(levelname)s | actor=%(actor_id)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    root = logging.getLogger()
    actor_filter = _ActorContextFilter()
    root.addFilter(actor_filter)
    for handler in root.handlers:
        handler.addFilter(actor_filter)
    
    # Reducir verbosidad de librerías externas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    logging.getLogger("google_genai.models").setLevel(logging.WARNING)
