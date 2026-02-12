from __future__ import annotations

import re
import time

from .modelos import SesionChat


def ahora_ts() -> int:
    return int(time.time())


def limpiar_texto(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "").strip())


def historial_corto(sesion: SesionChat, max_items: int = 6) -> str:
    if not sesion.mensajes:
        return "(vacio)"
    lineas = [f"{m.rol}: {m.texto}" for m in sesion.mensajes[-max_items:]]
    return "\n".join(lineas)


def es_comando_menu(texto: str) -> bool:
    tokens = {"menu", "menú", "inicio", "salir", "/start"}
    return limpiar_texto(texto).lower() in tokens


def ultimo_mensaje_usuario(sesion: SesionChat) -> str:
    for msg in reversed(sesion.mensajes):
        if msg.rol == "user":
            return msg.texto
    return ""


def extraer_fuentes_desde_respuesta(respuesta: str) -> list[str]:
    if not respuesta:
        return []
    return [linea.strip() for linea in respuesta.splitlines() if "[ceresearch:" in linea]
