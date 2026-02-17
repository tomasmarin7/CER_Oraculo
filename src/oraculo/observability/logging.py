"""
Configuración de logging para el Oráculo Agrónomo CER.
"""
import os
import logging
import sys


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
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    
    # Reducir verbosidad de librerías externas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    logging.getLogger("google_genai.models").setLevel(logging.WARNING)
