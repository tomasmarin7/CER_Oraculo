"""
Bot de Telegram - Setup y configuración.
Registra handlers y mantiene el servicio activo.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from ..config import Settings
from . import handlers
from .messages import get_generic_error_message

logger = logging.getLogger(__name__)
DEFAULT_CONCURRENT_UPDATES = 64


class TelegramBot:
    """
    Bot de Telegram del Oráculo Agrónomo CER.
    
    Responsabilidad: Solo setup de Telegram y registro de handlers.
    La lógica de negocio está en otros módulos (rag/, providers/, etc).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.application = None

    def setup(self) -> Application:
        """
        Configura el bot y registra todos los handlers.
        
        Returns:
            Application configurada
        """
        application = (
            Application.builder()
            .token(self.settings.telegram_bot_token.get_secret_value())
            .concurrent_updates(DEFAULT_CONCURRENT_UPDATES)
            .build()
        )
        application.bot_data["settings"] = self.settings

        # ===== COMANDOS =====
        application.add_handler(CommandHandler("start", handlers.start_command))

        # ===== CALLBACKS (BOTONES) =====
        application.add_handler(
            CallbackQueryHandler(handlers.menu_callback, pattern="^menu$")
        )
        application.add_handler(
            CallbackQueryHandler(handlers.research_callback, pattern="^academic_research$")
        )
        application.add_handler(
            CallbackQueryHandler(handlers.database_callback, pattern="^consult_database$")
        )

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
