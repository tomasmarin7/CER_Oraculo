from __future__ import annotations

import re


def format_academic_response_for_telegram(text: str) -> str:
    """Normaliza respuestas largas para lectura en Telegram."""
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return "No se obtuvo contenido para mostrar."

    out = raw
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]+", " ", out)
    return out.strip()
