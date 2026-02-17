from __future__ import annotations

import re

from .modelos import (
    MAX_MENSAJES_MEMORIA,
    EstadoSesion,
    MensajeMemoria,
    SesionChat,
    TIEMPO_SESION_SEGUNDOS,
)
from .texto import ahora_ts, limpiar_texto


def sesion_expirada(sesion: SesionChat, ahora: int | None = None) -> bool:
    current = ahora or ahora_ts()
    return current >= int(sesion.expire_at)


def renovar_sesion(sesion: SesionChat, ahora: int | None = None) -> SesionChat:
    current = ahora or ahora_ts()
    if not sesion.session_id:
        iniciar_sesion(sesion, current)
    sesion.last_activity_ts = current
    sesion.expire_at = current + TIEMPO_SESION_SEGUNDOS
    return sesion


def reiniciar_sesion(sesion: SesionChat, ahora: int | None = None) -> SesionChat:
    current = ahora or ahora_ts()
    sesion.estado = EstadoSesion.MENU
    sesion.mensajes.clear()
    sesion.resumen = ""
    sesion.last_rag_used = "none"
    sesion.last_sources.clear()
    sesion.flow_data.clear()
    iniciar_sesion(sesion, current)
    return renovar_sesion(sesion, current)


def iniciar_sesion(sesion: SesionChat, ahora: int | None = None) -> SesionChat:
    current = ahora or ahora_ts()
    safe_user = re.sub(r"[^a-zA-Z0-9_-]", "_", sesion.user_id)
    sesion.session_id = f"{safe_user}-{current}"
    sesion.started_at_ts = current
    sesion.last_activity_ts = current
    sesion.expire_at = current + TIEMPO_SESION_SEGUNDOS
    return sesion


def registrar_mensaje_usuario(sesion: SesionChat, texto: str, ahora: int | None = None) -> None:
    _agregar_mensaje(sesion, "user", texto, ahora)


def registrar_mensaje_asistente(
    sesion: SesionChat,
    texto: str,
    fuentes: list[str] | None = None,
    rag_usado: str = "none",
    ahora: int | None = None,
) -> None:
    _agregar_mensaje(sesion, "assistant", texto, ahora)
    sesion.last_rag_used = rag_usado
    sesion.last_sources = list(fuentes or [])


def _agregar_mensaje(sesion: SesionChat, rol: str, texto: str, ahora: int | None) -> None:
    limpio = limpiar_texto(texto)
    if not limpio:
        return
    ts = ahora or ahora_ts()
    sesion.mensajes.append(MensajeMemoria(rol=rol, texto=limpio, ts=ts))
    if len(sesion.mensajes) > MAX_MENSAJES_MEMORIA:
        sesion.mensajes = sesion.mensajes[-MAX_MENSAJES_MEMORIA:]
    renovar_sesion(sesion, ts)
