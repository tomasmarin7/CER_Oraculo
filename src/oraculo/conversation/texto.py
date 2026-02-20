from __future__ import annotations

import re
import time

from .modelos import SesionChat


def ahora_ts() -> int:
    return int(time.time())


def limpiar_texto(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "").strip())


def historial_corto(sesion: SesionChat, max_items: int = 10) -> str:
    if not sesion.mensajes:
        return "(vacio)"
    lineas = [f"{m.rol}: {m.texto}" for m in sesion.mensajes[-max_items:]]
    return "\n".join(lineas)
