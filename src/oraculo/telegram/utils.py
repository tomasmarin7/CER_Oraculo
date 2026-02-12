"""
Utilidades para el bot de Telegram.
Funciones auxiliares que no son específicas de un handler.
"""


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
