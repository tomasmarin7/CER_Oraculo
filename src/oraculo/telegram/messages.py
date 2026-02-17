"""
Templates de mensajes del bot.
Centraliza todos los textos que el bot envía a los usuarios.
"""


def get_welcome_message() -> str:
    """Mensaje de bienvenida del menú principal"""
    return (
        "*Bienvenido al Oráculo Agrónomo CER* \n\n"
        "Soy un asistente para consulta de ensayos e investigación académica. "
        "Selecciona una opción para comenzar:"
    )


def get_menu_message() -> str:
    """Mensaje simple para regresar al menú"""
    return "*Oráculo Agrónomo CER* \n\nSelecciona una opción:"


def get_academic_research_intro_message() -> str:
    """Mensaje introductorio de investigación académica"""
    return (
        "*Investigación Académica*\n\n"
        "Esta herramienta investiga entre una gigantesca variedad de literatura "
        "científica para elaborar informes según la consulta que solicites, "
        "sobre cualquier tema.\n\n"
        "*¿Qué conviene incluir en tu consulta?*\n"
        "• Qué quieres investigar\n"
        "• En qué cultivo, especie o contexto\n"
        "• Qué comparación o duda quieres resolver\n"
        "• Qué nivel de detalle esperas en la respuesta\n\n"
        "Mientras más claro y específico seas, mejor será la investigación.\n\n"
        "───────────────────────\n"
        "*Escribe tu consulta detallada:* "
    )


def get_database_intro_message() -> str:
    """Mensaje introductorio de consulta a la base de datos"""
    return (
        "*Consulta Ensayos*\n\n"
        "¿Tienes un problema con tu cultivo? Cuéntanos qué está pasando y revisamos si "
        "tenemos ensayos del CER relacionados con esa problemática y ese cultivo.\n\n"
        "También podemos consultar la base de datos del SAG para ver qué productos "
        "figuran como registrados para ese problema y para qué cultivos aplican.\n\n"
        "Después, si corresponde, podemos verificar si ese producto tiene respaldo en "
        "ensayos realizados por CER.\n\n"
        "*Importante sobre el alcance:*\n"
        "• Con datos SAG informamos productos registrados, no cuál es mejor\n"
        "• No aseguramos eficacia solo con registro SAG\n"
        "• La evidencia de funcionamiento la damos cuando existe ensayo CER\n\n"
        "*Recomendación:*\n"
        "Antes de aplicar decisiones en terreno, valida la información con tus "
        "condiciones productivas y criterios técnicos.\n\n"
        "*Ejemplos de consulta:*\n"
        "• \"Tengo una plaga de pulgones, ¿qué hago?\"\n"
        "• \"¿El producto Kelpac sirve para tratar el oídio?\"\n"
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
