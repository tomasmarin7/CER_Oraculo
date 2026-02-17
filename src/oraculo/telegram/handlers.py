"""
Handlers de Telegram.
Este modulo solo adapta el canal Telegram al servicio conversacional.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
import unicodedata

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..aplicacion import ServicioConversacionOraculo
from ..academic import ServicioInvestigacionAcademica, map_service_error
from ..config import get_settings
from ..conversation import (
    AlmacenSesionesMemoria,
    EstadoSesion,
    RepositorioSesiones,
    reiniciar_sesion,
    renovar_sesion,
)
from ..conversation.archive_store import close_session_archive
from ..observability.logging import log_actor_context
from .keyboards import get_main_menu_keyboard
from .messages import (
    get_academic_research_intro_message,
    get_database_intro_message,
    get_menu_message,
    get_welcome_message,
)
from .utils import normalizar_respuesta_para_telegram, split_message

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)
_almacen_sesiones: RepositorioSesiones = AlmacenSesionesMemoria()
_servicio_oraculo = ServicioConversacionOraculo(_almacen_sesiones)
_servicio_academico = ServicioInvestigacionAcademica()
PROCESSING_HEADER = "Estoy preparando tu respuesta."
INITIAL_STATUS = "Iniciando analisis de tu consulta..."
ACADEMIC_MODE = "academic"
ACADEMIC_WAITING_TEXT = (
    "Esto puede tardar unos minutos (~8), no envies nada."
)


def _build_processing_message(status: str) -> str:
    detalle = (status or "").strip() or INITIAL_STATUS
    return f"{PROCESSING_HEADER}\n{detalle}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor_id = _build_session_actor_id(update)
    with log_actor_context(actor_id):
        sesion = _obtener_sesion(update)
        if sesion.mensajes:
            close_session_archive(sesion, reason="start_command")
        reiniciar_sesion(sesion)
        _reset_academic_flags(sesion)
        _almacen_sesiones.guardar(sesion)

        await update.message.reply_text(
            get_welcome_message(),
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    actor_id = _build_session_actor_id(update)
    with log_actor_context(actor_id):
        sesion = _obtener_sesion(update)
        if sesion.mensajes:
            close_session_archive(sesion, reason="menu_callback")
        reiniciar_sesion(sesion)
        _reset_academic_flags(sesion)
        _almacen_sesiones.guardar(sesion)

        await query.edit_message_text(
            get_menu_message(),
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )


async def research_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    actor_id = _build_session_actor_id(update)
    with log_actor_context(actor_id):
        sesion = _obtener_sesion(update)
        sesion.flow_data["active_mode"] = ACADEMIC_MODE
        sesion.flow_data["academic_in_progress"] = False
        sesion.flow_data["pending_intro"] = False
        renovar_sesion(sesion)
        _almacen_sesiones.guardar(sesion)
        logger.info("🎓 Modo investigacion academica activado | user=%s", sesion.user_id)
        await query.edit_message_text(
            text=get_academic_research_intro_message(),
            parse_mode=ParseMode.MARKDOWN,
        )


async def database_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    actor_id = _build_session_actor_id(update)
    with log_actor_context(actor_id):
        sesion = _obtener_sesion(update)
        _reset_academic_flags(sesion)
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        renovar_sesion(sesion)
        _almacen_sesiones.guardar(sesion)

        await query.edit_message_text(
            text=get_database_intro_message(),
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mensaje_usuario = (update.message.text or "").strip()
    actor_id = _build_session_actor_id(update)
    with log_actor_context(actor_id):
        user_id = actor_id
        sesion = _obtener_sesion(update)

        if _debe_mostrar_menu_inicial(sesion, mensaje_usuario):
            sesion.flow_data["pending_intro"] = False
            sesion.estado = EstadoSesion.MENU
            _reset_academic_flags(sesion)
            renovar_sesion(sesion)
            _almacen_sesiones.guardar(sesion)
            await update.message.reply_text(
                get_welcome_message(),
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if sesion.flow_data.get("academic_in_progress"):
            await update.message.reply_text(ACADEMIC_WAITING_TEXT)
            return

        if sesion.flow_data.get("active_mode") == ACADEMIC_MODE:
            await _handle_academic_research_query(update, context, sesion, mensaje_usuario)
            return

        settings = _resolver_settings(context)
        processing_message = await update.message.reply_text(
            _build_processing_message(INITIAL_STATUS)
        )
        loop = asyncio.get_running_loop()
        progress_state = {"last": INITIAL_STATUS}

        def report_progress(status: str) -> None:
            next_status = (status or "").strip()
            if not next_status or next_status == progress_state["last"]:
                return
            progress_state["last"] = next_status

            async def _edit_progress() -> None:
                try:
                    await processing_message.edit_text(_build_processing_message(next_status))
                except Exception:
                    logger.debug("No se pudo actualizar el mensaje de progreso.")

            loop.call_soon_threadsafe(asyncio.create_task, _edit_progress())

        logger.info(
            "📩 Mensaje recibido: %r",
            mensaje_usuario[:180],
        )

        try:
            respuesta = await asyncio.to_thread(
                _servicio_oraculo.procesar_mensaje,
                user_id=user_id,
                mensaje_usuario=mensaje_usuario,
                settings=settings,
                top_k=8,
                progress_callback=report_progress,
            )
        finally:
            try:
                await processing_message.delete()
            except Exception:
                logger.debug("No se pudo eliminar el mensaje de procesamiento.")

        logger.info(
            "📤 Respuesta enviada (%s chars)",
            len(respuesta.texto),
        )
        await _send_telegram_response(update, respuesta.texto)


async def _handle_academic_research_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    sesion,
    mensaje_usuario: str,
) -> None:
    settings = _resolver_settings(context)
    sesion.flow_data["academic_in_progress"] = True
    renovar_sesion(sesion)
    _almacen_sesiones.guardar(sesion)
    logger.info(
        "🎓 Investigacion academica iniciada | user=%s | chars=%s",
        sesion.user_id,
        len((mensaje_usuario or "").strip()),
    )

    await update.message.reply_text(ACADEMIC_WAITING_TEXT)
    try:
        resultado = await asyncio.to_thread(
            _servicio_academico.ejecutar_consulta,
            query=mensaje_usuario,
            settings=settings,
        )
        logger.info(
            "🎓 Investigacion academica completada | user=%s | salida=%s chars | success=%s",
            sesion.user_id,
            len(resultado.text),
            resultado.has_successful_answer,
        )
        await _send_telegram_response(
            update,
            resultado.text,
            parse_mode=ParseMode.MARKDOWN,
            normalize=False,
        )
    except Exception as exc:
        logger.exception(
            "❌ Error en investigacion academica | user=%s | error=%s",
            sesion.user_id,
            type(exc).__name__,
        )
        await update.message.reply_text(map_service_error(exc))
    finally:
        sesion.estado = EstadoSesion.MENU
        _reset_academic_flags(sesion)
        renovar_sesion(sesion)
        _almacen_sesiones.guardar(sesion)
        logger.info("🎓 Modo investigacion academica finalizado | user=%s", sesion.user_id)

    await update.message.reply_text(
        get_menu_message(),
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _send_telegram_response(
    update: Update,
    response: str,
    *,
    parse_mode: str | None = None,
    normalize: bool = True,
) -> None:
    texto = normalizar_respuesta_para_telegram(response) if normalize else (response or "").strip()
    for chunk in split_message(texto, max_length=4096):
        try:
            await update.message.reply_text(
                chunk,
                disable_web_page_preview=True,
                parse_mode=parse_mode,
            )
        except Exception:
            # Fallback robusto: enviar sin parse_mode para no perder el contenido.
            await update.message.reply_text(
                normalizar_respuesta_para_telegram(chunk),
                disable_web_page_preview=True,
            )


async def default_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await handle_user_text(update, context)


def _resolver_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    settings = context.application.bot_data.get("settings")
    if settings:
        return settings
    return get_settings()


def _obtener_sesion(update: Update):
    actor_id = _build_session_actor_id(update)
    return _almacen_sesiones.obtener_o_crear(actor_id)


def _build_session_actor_id(update: Update) -> str:
    user_part = str(update.effective_user.id) if update.effective_user else "anon"
    chat_part = str(update.effective_chat.id) if update.effective_chat else "chat_anon"
    return f"{chat_part}:{user_part}"


def _reset_academic_flags(sesion) -> None:
    sesion.flow_data.pop("active_mode", None)
    sesion.flow_data.pop("academic_in_progress", None)


def _debe_mostrar_menu_inicial(sesion, mensaje_usuario: str) -> bool:
    if bool(sesion.flow_data.get("pending_intro")):
        return True
    texto = _normalizar_mensaje(mensaje_usuario)
    return texto in {"hola", "hello", "hi", "buenas", "inicio", "menu", "men"}


def _normalizar_mensaje(texto: str) -> str:
    base = (texto or "").strip().lower()
    sin_tildes = "".join(
        ch
        for ch in unicodedata.normalize("NFD", base)
        if unicodedata.category(ch) != "Mn"
    )
    return " ".join(sin_tildes.split())
