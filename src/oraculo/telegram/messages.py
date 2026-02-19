"""
Templates de mensajes del bot.
Centraliza todos los textos que el bot envía a los usuarios.
"""

def get_database_intro_message() -> str:
    """Mensaje introductorio de consulta a la base de datos"""
    return (
        "*Oráculo Agrónomo CER | Consulta Ensayos*\n\n"
        "Soy el asistente del Centro de Evaluación Rosario (CER). Te ayudo a revisar "
        "qué se ha ensayado para tu problema y cultivo, y a resumir resultados, dosis "
        "y condiciones de ensayo.\n\n"
        "*Ejemplos de consulta:*\n"
        "• \"Tengo una plaga de pulgones, ¿qué hago?\"\n"
        "• \"¿Han ensayado algo para oídio en vid?\"\n"
        "• \"¿Han testeado en el CER un producto para mejorar el calibre en cerezos?\"\n\n"
        "───────────────────────\n"
        "*Escribe tu consulta:* "
    )


def get_processing_message() -> str:
    """Mensaje mientras se procesa una consulta"""
    return (
        "*Buscando en la base de datos...*\n\n" "Esto puede tomar unos segundos."
    )


def get_invalid_query_message() -> str:
    """Mensaje cuando la consulta está vacía"""
    return "Por favor, escribe una consulta válida."


def get_error_message() -> str:
    """Mensaje genérico de error"""
    return (
        "*Error procesando tu consulta*\n\n"
        "Ocurrió un error al buscar en la base de datos. "
        "Por favor, intenta nuevamente."
    )


def get_post_query_message() -> str:
    """Mensaje después de responder una consulta"""
    return "Puedes seguir preguntando en este chat o volver al menú."


def get_generic_error_message() -> str:
    """Mensaje de error genérico para el error handler"""
    return "Ocurrió un error inesperado. Por favor, intenta nuevamente."
