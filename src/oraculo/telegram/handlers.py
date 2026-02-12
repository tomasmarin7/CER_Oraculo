"""
Handlers del bot de Telegram.
Adapters ligeros que conectan Telegram con la lógica de negocio existente.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ..rag.pipeline import answer as rag_answer
from .keyboards import get_main_menu_keyboard, get_post_query_keyboard
from .messages import (
    get_database_intro_message,
    get_error_message,
    get_invalid_query_message,
    get_menu_message,
    get_post_query_message,
    get_processing_message,
    get_research_in_development_message,
    get_welcome_message,
)
from .utils import split_message

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)


# ============================================================================
# COMANDO /start Y MENÚ
# ============================================================================


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler del comando /start - Muestra menú principal"""
    keyboard = get_main_menu_keyboard()
    message = get_welcome_message()

    await update.message.reply_text(
        message, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback para regresar al menú principal"""
    query = update.callback_query
    await query.answer()

    keyboard = get_main_menu_keyboard()
    message = get_menu_message()

    await query.edit_message_text(
        message, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
    )

    # Limpiar estado
    context.user_data["awaiting_query"] = False


# ============================================================================
# INVESTIGACIÓN (EN DESARROLLO)
# ============================================================================


async def research_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback del botón 'Generar Investigación'"""
    query = update.callback_query
    await query.answer()

    message = get_research_in_development_message()
    await query.edit_message_text(text=message, parse_mode=ParseMode.MARKDOWN)


# ============================================================================
# CONSULTA A BASE DE DATOS (RAG)
# ============================================================================


async def database_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback del botón 'Consultar Base de Datos CER'"""
    query = update.callback_query
    await query.answer()

    message = get_database_intro_message()
    await query.edit_message_text(text=message, parse_mode=ParseMode.MARKDOWN)

    # Marcar que esperamos una consulta
    context.user_data["awaiting_query"] = True


async def handle_user_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Procesa la consulta del usuario usando el pipeline RAG existente.
    
    Este handler solo adapta Telegram → Pipeline RAG → Telegram.
    La lógica real está en rag/pipeline.py
    """
    user_question = update.message.text.strip()

    # Validar
    if not user_question:
        await update.message.reply_text(
            get_invalid_query_message(), parse_mode=ParseMode.MARKDOWN
        )
        return

    # Mostrar procesamiento
    processing_msg = await update.message.reply_text(
        get_processing_message(), parse_mode=ParseMode.MARKDOWN
    )

    try:
        # ===== USAR PIPELINE RAG EXISTENTE =====
        # Ejecutar en thread separado para no bloquear el event loop
        # Así el bot sigue respondiendo a otros usuarios mientras procesa
        result = await asyncio.to_thread(rag_answer, user_question, 8)

        # Limpiar mensaje de procesamiento
        await processing_msg.delete()

        # Enviar respuesta (dividida si es necesaria)
        await _send_telegram_response(update, result)

        # Mostrar opciones
        keyboard = get_post_query_keyboard()
        await update.message.reply_text(
            get_post_query_message(), reply_markup=keyboard
        )

        # Limpiar estado
        context.user_data["awaiting_query"] = False

    except Exception as e:
        logger.error(f"Error en consulta: {e}", exc_info=True)

        try:
            await processing_msg.delete()
        except Exception:
            pass

        await update.message.reply_text(
            get_error_message(), parse_mode=ParseMode.MARKDOWN
        )

        context.user_data["awaiting_query"] = False


# ============================================================================
# UTILIDADES INTERNAS
# ============================================================================


async def _send_telegram_response(update: Update, response: str) -> None:
    """
    Envía respuesta a Telegram, dividiéndola si excede el límite.
    
    Args:
        update: Update de Telegram
        response: Texto a enviar
    """
    chunks = split_message(response, max_length=4096)

    for chunk in chunks:
        try:
            await update.message.reply_text(
                chunk, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
            )
        except BadRequest as exc:
            # El LLM puede devolver Markdown no válido para Telegram.
            # Fallback a texto plano para asegurar entrega.
            logger.warning("Markdown inválido en respuesta, enviando texto plano: %s", exc)
            await update.message.reply_text(chunk, disable_web_page_preview=True)


async def default_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handler por defecto para mensajes sin flujo activo"""
    if not context.user_data.get("awaiting_query"):
        await start_command(update, context)
