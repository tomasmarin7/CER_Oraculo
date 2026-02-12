"""
Utilidades para el bot de Telegram.
Funciones auxiliares que no son específicas de un handler.
"""

import re


def normalizar_respuesta_para_telegram(text: str) -> str:
    """
    Convierte markdown libre del LLM a texto estable para Telegram.
    Evita negritas/cursivas falsas por parseo ambiguo.
    """
    if not text:
        return ""

    out = text
    out = out.replace("\r\n", "\n")

    # Quitar énfasis markdown para evitar render inconsistente.
    out = out.replace("**", "")
    out = out.replace("__", "")

    # Remover cursiva en patrones comunes, sin tocar palabras internas.
    out = re.sub(r"(^|\\s)_([^_\\n]+)_(?=\\s|$)", r"\\1\\2", out)
    out = re.sub(r"(^|\\s)\\*([^*\\n]+)\\*(?=\\s|$)", r"\\1\\2", out)

    # Normalizar listas estilo markdown a viñeta unicode.
    out = re.sub(r"(?m)^\\s*\\*\\s+", "• ", out)
    out = re.sub(r"(?m)^\\s*-\\s+", "• ", out)

    # Compactar espacios finales por línea.
    out = "\n".join(line.rstrip() for line in out.split("\n"))
    return out.strip()


def split_message(text: str, max_length: int = 4096) -> list[str]:
    """
    Divide un mensaje largo en chunks respetando el límite de Telegram.
    
    Args:
        text: Texto a dividir
        max_length: Longitud máxima por chunk (default: 4096, límite de Telegram)
    
    Returns:
        Lista de strings, cada uno con longitud <= max_length
    """
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current_chunk = ""

    for line in text.split("\n"):
        # Si la línea sola ya excede el límite, cortar en segmentos duros.
        while len(line) > max_length:
            if current_chunk:
                chunks.append(current_chunk.rstrip())
                current_chunk = ""
            chunks.append(line[:max_length])
            line = line[max_length:]

        candidate = f"{current_chunk}\n{line}" if current_chunk else line
        if len(candidate) > max_length:
            chunks.append(current_chunk.rstrip())
            current_chunk = line
        else:
            current_chunk = candidate

    if current_chunk:
        chunks.append(current_chunk.rstrip())

    return [chunk for chunk in chunks if chunk]
