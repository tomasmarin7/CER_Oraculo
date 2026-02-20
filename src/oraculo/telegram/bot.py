"""
Bot de Telegram - Setup y configuración.
Registra handlers y mantiene el servicio activo.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from ..config import Settings
from . import handlers
from .messages import get_generic_error_message

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Bot de Telegram del Oráculo Agrónomo CER.
    
    Responsabilidad: Solo setup de Telegram y registro de handlers.
    La lógica de negocio está en otros módulos (rag/, providers/, etc).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.application: Application | None = None
        self._worker_executor: ThreadPoolExecutor | None = None
        self._cleanup_task: asyncio.Task | None = None

    def setup(self) -> Application:
        """
        Configura el bot y registra todos los handlers.
        
        Returns:
            Application configurada
        """
        concurrent_updates = max(int(self.settings.telegram_concurrent_updates), 1)
        application = (
            Application.builder()
            .token(self.settings.telegram_bot_token.get_secret_value())
            .concurrent_updates(concurrent_updates)
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
            .build()
        )
        application.bot_data["settings"] = self.settings

        # ===== COMANDOS =====
        application.add_handler(CommandHandler("start", handlers.start_command))

        # ===== MENSAJES DE TEXTO =====
        # Router: decide si es consulta o mensaje por defecto
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._route_text_message,
            )
        )

        # ===== ERROR HANDLER =====
        application.add_error_handler(self._error_handler)

        self.application = application
        return application

    async def _post_init(self, application: Application) -> None:
        max_workers = max(int(self.settings.oraculo_worker_threads), 1)
        self._worker_executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="oraculo-worker",
        )
        asyncio.get_running_loop().set_default_executor(self._worker_executor)
        logger.info(
            "Worker pool de ejecución configurado | max_workers=%s | concurrent_updates=%s",
            max_workers,
            max(int(self.settings.telegram_concurrent_updates), 1),
        )
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _post_shutdown(self, application: Application) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        if self._worker_executor:
            self._worker_executor.shutdown(wait=False, cancel_futures=True)
            self._worker_executor = None

    async def _cleanup_loop(self) -> None:
        interval = max(int(self.settings.oraculo_session_cleanup_interval_seconds), 1)
        while True:
            try:
                await asyncio.sleep(interval)
                removidas = await asyncio.to_thread(
                    handlers.cleanup_expired_sessions,
                    self.settings,
                )
                if removidas:
                    logger.info(
                        "Housekeeping sesiones: %s expiradas removidas de RAM.",
                        removidas,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error en limpieza periódica de sesiones.")

    async def _route_text_message(self, update: Update, context) -> None:
        """
        Router para mensajes de texto.
        Delega al handler de conversación con memoria.
        """
        await handlers.handle_user_text(update, context)

    async def _error_handler(self, update: Update, context) -> None:
        """Handler global de errores"""
        logger.error(f"Error en bot: {context.error}", exc_info=context.error)

        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    get_generic_error_message()
                )
            except Exception as e:
                logger.error(f"No se pudo enviar mensaje de error: {e}")

    def run(self) -> None:
        """
        Inicia el bot en modo polling (mantiene el servicio activo).
        Bloquea hasta Ctrl+C.
        """
        if not self.application:
            self.setup()

        logger.info("🚀 Bot de Telegram iniciado.")
        logger.info("🛑 Presiona Ctrl+C para detener.")

        self.application.run_polling(allowed_updates=Update.ALL_TYPES)
