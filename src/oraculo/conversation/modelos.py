from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

TIEMPO_SESION_SEGUNDOS = 15 * 60
MAX_MENSAJES_MEMORIA = 12


class EstadoSesion(StrEnum):
    MENU = "MENU"
    ESPERANDO_PREGUNTA = "WAITING_QUESTION"
    CONVERSACION = "CONVERSATION"


class AccionRouter(StrEnum):
    RAG_CER = "RUN_RAG_CER"
    RAG_SAG = "RUN_RAG_SAG"
    RAG_AMBAS = "RUN_RAG_BOTH"
    CHAT_NORMAL = "CHAT_ONLY"
    IR_MENU = "GO_MENU"


@dataclass(slots=True)
class MensajeMemoria:
    rol: str
    texto: str
    ts: int


@dataclass(slots=True)
class SesionChat:
    user_id: str
    estado: EstadoSesion = EstadoSesion.MENU
    last_activity_ts: int = field(default_factory=lambda: int(time.time()))
    expire_at: int = field(
        default_factory=lambda: int(time.time()) + TIEMPO_SESION_SEGUNDOS
    )
    mensajes: list[MensajeMemoria] = field(default_factory=list)
    resumen: str = ""
    last_rag_used: str = "none"
    last_sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DecisionRouter:
    accion: AccionRouter
    motivo: str
    consulta_rag: str = ""
    confianza: float = 0.0
