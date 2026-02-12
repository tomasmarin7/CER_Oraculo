#!/usr/bin/env python
"""
Script para ejecutar el bot de Telegram del Oráculo Agrónomo CER
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Permite ejecutar desde la raiz del repo sin instalar el paquete.
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from oraculo.main import run_telegram_bot

logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point de ejecucion local del bot."""
    try:
        run_telegram_bot()

    except KeyboardInterrupt:
        logger.info("Bot detenido por el usuario")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
