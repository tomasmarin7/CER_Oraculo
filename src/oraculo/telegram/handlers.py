"""
Handlers de Telegram.
Este modulo solo adapta el canal Telegram al servicio conversacional.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..aplicacion import ServicioConversacionOraculo
from ..config import get_settings
from ..conversation import (
    AlmacenSesionesMemoria,
    EstadoSesion,
    RepositorioSesiones,
    reiniciar_sesion,
)
from ..conversation.archive_store import close_session_archive
from ..observability.logging import log_actor_context
from .messages import get_database_intro_message
from .utils import normalizar_respuesta_para_telegram, split_message

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)
_runtime_guard = threading.Lock()
_runtime_services: tuple[RepositorioSesiones, ServicioConversacionOraculo] | None = None
PROCESSING_HEADER = "Estoy preparando tu respuesta."
INITIAL_STATUS = "Estoy revisando tu consulta para entender bien el problema..."


def _build_processing_message(status: str) -> str:
    detalle = (status or "").strip() or INITIAL_STATUS
    return f"{PROCESSING_HEADER}\n{detalle}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _resolver_settings(context)
    almacen_sesiones, _ = _get_runtime_services(settings)
    actor_id = _build_session_actor_id(update)
    with log_actor_context(actor_id):
        sesion = _obtener_sesion(update, almacen_sesiones)
        if sesion.mensajes:
            close_session_archive(sesion, reason="start_command")
        reiniciar_sesion(sesion)
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        almacen_sesiones.guardar(sesion)

        await update.message.reply_text(
            get_database_intro_message(),
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mensaje_usuario = (update.message.text or "").strip()
    actor_id = _build_session_actor_id(update)
    with log_actor_context(actor_id):
        user_id = actor_id

        settings = _resolver_settings(context)
        _, servicio_oraculo = _get_runtime_services(settings)
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
                servicio_oraculo.procesar_mensaje,
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


def _resolver_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    settings = context.application.bot_data.get("settings")
    if settings:
        return settings
    return get_settings()


def _obtener_sesion(update: Update, almacen_sesiones: RepositorioSesiones):
    actor_id = _build_session_actor_id(update)
    return almacen_sesiones.obtener_o_crear(actor_id)


def _get_runtime_services(
    settings: Settings,
) -> tuple[RepositorioSesiones, ServicioConversacionOraculo]:
    global _runtime_services
    if _runtime_services is not None:
        return _runtime_services

    with _runtime_guard:
        if _runtime_services is None:
            almacen_sesiones = AlmacenSesionesMemoria(
                cleanup_interval_seconds=settings.oraculo_session_cleanup_interval_seconds,
                max_sesiones_en_memoria=settings.oraculo_max_sesiones_en_memoria,
            )
            _runtime_services = (
                almacen_sesiones,
                ServicioConversacionOraculo(almacen_sesiones),
            )
    return _runtime_services


def cleanup_expired_sessions(settings: Settings) -> int:
    almacen_sesiones, _ = _get_runtime_services(settings)
    return almacen_sesiones.limpiar_expiradas()


def _build_session_actor_id(update: Update) -> str:
    user_part = str(update.effective_user.id) if update.effective_user else "anon"
    chat_part = str(update.effective_chat.id) if update.effective_chat else "chat_anon"
    return f"{chat_part}:{user_part}"
