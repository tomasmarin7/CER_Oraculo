from __future__ import annotations

from .config import get_settings
from .observability.logging import setup_logging
from .telegram import TelegramBot


def run_telegram_bot() -> None:
    """Punto de entrada principal del servicio de Telegram."""
    setup_logging()
    settings = get_settings()
    TelegramBot(settings).run()


def main() -> int:
    run_telegram_bot()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
