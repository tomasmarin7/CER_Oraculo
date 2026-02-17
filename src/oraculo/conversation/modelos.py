from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

TIEMPO_SESION_SEGUNDOS = 15 * 60
MAX_MENSAJES_MEMORIA = 12


class EstadoSesion(StrEnum):
    MENU = "MENU"
    ESPERANDO_PREGUNTA = "WAITING_QUESTION"
    ESPERANDO_PROBLEMA = "WAITING_PROBLEM"
    ESPERANDO_DETALLE_PRODUCTO = "WAITING_PRODUCT_DETAIL"
    ESPERANDO_CONFIRMACION_SAG = "WAITING_SAG_CONFIRMATION"
    CONVERSACION = "CONVERSATION"


@dataclass(slots=True)
class MensajeMemoria:
    rol: str
    texto: str
    ts: int


@dataclass(slots=True)
class SesionChat:
    user_id: str
    session_id: str = ""
    started_at_ts: int = field(default_factory=lambda: int(time.time()))
    estado: EstadoSesion = EstadoSesion.MENU
    last_activity_ts: int = field(default_factory=lambda: int(time.time()))
    expire_at: int = field(
        default_factory=lambda: int(time.time()) + TIEMPO_SESION_SEGUNDOS
    )
    mensajes: list[MensajeMemoria] = field(default_factory=list)
    resumen: str = ""
    last_rag_used: str = "none"
    last_sources: list[str] = field(default_factory=list)
    flow_data: dict[str, Any] = field(default_factory=dict)
