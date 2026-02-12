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
from telegram.ext import ContextTypes

from ..config import get_settings
from ..conversation import (
    AccionRouter,
    AlmacenSesionesMemoria,
    EstadoSesion,
    RepositorioSesiones,
    ejecutar_decision,
    extraer_fuentes_desde_respuesta,
    registrar_mensaje_asistente,
    registrar_mensaje_usuario,
    reiniciar_sesion,
    renovar_sesion,
    routear_siguiente_accion,
)
from .keyboards import get_main_menu_keyboard
from .messages import (
    get_database_intro_message,
    get_error_message,
    get_invalid_query_message,
    get_menu_message,
    get_processing_message,
    get_research_in_development_message,
    get_welcome_message,
)
from .utils import normalizar_respuesta_para_telegram, split_message

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)
_almacen_sesiones: RepositorioSesiones = AlmacenSesionesMemoria()


# ============================================================================
# COMANDO /start Y MENÚ
# ============================================================================


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler del comando /start - Muestra menú principal"""
    sesion = _obtener_sesion(update)
    reiniciar_sesion(sesion)
    _almacen_sesiones.guardar(sesion)

    keyboard = get_main_menu_keyboard()
    message = get_welcome_message()

    await update.message.reply_text(
        message, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback para regresar al menú principal"""
    query = update.callback_query
    await query.answer()

    sesion = _obtener_sesion(update)
    reiniciar_sesion(sesion)
    _almacen_sesiones.guardar(sesion)

    keyboard = get_main_menu_keyboard()
    message = get_menu_message()

    await query.edit_message_text(
        message, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
    )


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

    sesion = _obtener_sesion(update)
    sesion.estado = EstadoSesion.ESPERANDO_PREGUNTA
    renovar_sesion(sesion)
    _almacen_sesiones.guardar(sesion)

    message = get_database_intro_message()
    await query.edit_message_text(text=message, parse_mode=ParseMode.MARKDOWN)

async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Procesa mensajes de texto usando estado de sesión + memory router.
    """
    user_question = update.message.text.strip()
    sesion = _obtener_sesion(update)

    if sesion.estado == EstadoSesion.MENU:
        await start_command(update, context)
        return

    if not user_question:
        await update.message.reply_text(
            get_invalid_query_message(), parse_mode=ParseMode.MARKDOWN
        )
        return

    settings = _resolver_settings(context)
    registrar_mensaje_usuario(sesion, user_question)
    decision = routear_siguiente_accion(sesion, user_question, settings)

    processing_msg = None
    if _requiere_procesamiento(decision.accion):
        processing_msg = await update.message.reply_text(
            get_processing_message(), parse_mode=ParseMode.MARKDOWN
        )

    try:
        result = await asyncio.to_thread(
            ejecutar_decision, decision, sesion, settings, 8
        )
        fuentes = extraer_fuentes_desde_respuesta(result)
        registrar_mensaje_asistente(
            sesion,
            result,
            fuentes=fuentes,
            rag_usado=_tag_rag(decision.accion),
        )
        _almacen_sesiones.guardar(sesion)

        if processing_msg:
            await processing_msg.delete()

        await _send_telegram_response(update, result)

    except Exception as e:
        logger.error(f"Error en consulta: {e}", exc_info=True)

        try:
            if processing_msg:
                await processing_msg.delete()
        except Exception:
            pass

        await update.message.reply_text(
            get_error_message(), parse_mode=ParseMode.MARKDOWN
        )


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
    texto = normalizar_respuesta_para_telegram(response)
    chunks = split_message(texto, max_length=4096)

    for chunk in chunks:
        await update.message.reply_text(chunk, disable_web_page_preview=True)


async def default_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Compatibilidad temporal: delega al nuevo handler de texto."""
    await handle_user_text(update, context)


def _resolver_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    settings = context.application.bot_data.get("settings")
    if settings:
        return settings
    return get_settings()


def _obtener_sesion(update: Update):
    user_id = str(update.effective_user.id if update.effective_user else "anon")
    return _almacen_sesiones.obtener_o_crear(user_id)


def _requiere_procesamiento(accion: AccionRouter) -> bool:
    return accion in {
        AccionRouter.RAG_CER,
        AccionRouter.RAG_SAG,
        AccionRouter.RAG_AMBAS,
    }


def _tag_rag(accion: AccionRouter) -> str:
    if accion == AccionRouter.RAG_CER:
        return "cer"
    if accion == AccionRouter.RAG_SAG:
        return "sag"
    if accion == AccionRouter.RAG_AMBAS:
        return "both"
    return "none"
