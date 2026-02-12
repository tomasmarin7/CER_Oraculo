"""
Configuración de logging para el Oráculo Agrónomo CER.
"""
import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configura el logging básico para la aplicación.
    
    Args:
        level: Nivel de logging (default: INFO)
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Reducir verbosidad de librerías externas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
