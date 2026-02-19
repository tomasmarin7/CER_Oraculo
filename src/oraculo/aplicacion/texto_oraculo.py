from __future__ import annotations

import re
import unicodedata

INTRO_ORACULO_CER = (
    "*Oráculo Agrónomo CER*\n\n"
    "Soy un asistente del Centro de Evaluación Rosario (CER). "
    "Te ayudo a revisar qué productos hemos ensayado en cultivos y plagas específicas, "
    "y a resumirte resultados, dosis y condiciones de ensayo.\n\n"
    "Cuéntame cuál es tu problema con el cultivo o qué información necesitas."
)

ACLARACION_ACCION = (
    "Para avanzar necesito un poco más de contexto. "
    "Cuéntame el cultivo y el problema específico que quieres revisar en ensayos CER."
)


def normalizar_texto(texto: str) -> str:
    bruto = unicodedata.normalize("NFKD", texto or "")
    bruto = "".join(ch for ch in bruto if not unicodedata.combining(ch))
    bruto = bruto.lower().strip()
    return re.sub(r"\s+", " ", bruto)


def construir_respuesta_chat_basica(mensaje_usuario: str) -> str:
    normalizado = normalizar_texto(mensaje_usuario)
    if any(token in normalizado for token in ("hola", "buenas", "como estas", "que tal")):
        return (
            "Estoy muy bien, gracias. ¿Y tú?\n\n"
            "Estoy aquí para ayudarte con ensayos del CER. "
            "Si quieres, cuéntame tu problema de cultivo y lo revisamos."
        )
    if "gracias" in normalizado:
        return (
            "De nada. Si quieres, revisamos otro problema de cultivo con ensayos CER."
        )
    return (
        "Perfecto. Si quieres, te ayudo con otra consulta técnica de ensayos CER."
    )
