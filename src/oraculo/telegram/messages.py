"""
Templates de mensajes del bot.
Centraliza todos los textos que el bot envía a los usuarios.
"""


def get_welcome_message() -> str:
    """Mensaje de bienvenida del menú principal"""
    return (
        "*Bienvenido al Oráculo Agrónomo CER* \n\n"
        "Soy un asistente especializado en ensayos agronómicos. "
        "Selecciona una opción para comenzar:"
    )


def get_menu_message() -> str:
    """Mensaje simple para regresar al menú"""
    return "*Oráculo Agrónomo CER* \n\nSelecciona una opción:"


def get_research_in_development_message() -> str:
    """Mensaje cuando se selecciona Generar Investigación"""
    return (
        "*Función en desarrollo*\n\n"
        "Esta funcionalidad estará disponible próximamente."
    )


def get_database_intro_message() -> str:
    """Mensaje introductorio de consulta a la base de datos"""
    return (
        "*Consulta la Base de Datos CER*\n\n"
        "Esta herramienta te permite buscar información específica en nuestra extensa "
        "base de datos de ensayos agronómicos realizados por CER "
        "(Centro de Evaluación Rosario).\n\n"
        "*¿Qué puedes consultar?*\n"
        "• Productos agronómicos y su eficacia\n"
        "• Tratamientos para plagas y enfermedades\n"
        "• Comparativas entre diferentes productos\n"
        "• Dosis y momentos de aplicación\n"
        "• Resultados de ensayos en diferentes cultivos\n\n"
        "*Ejemplos de consultas:*\n"
        "• \"¿Cómo funciona Kelpak para uvas?\"\n"
        "• \"Productos para arañita roja en cerezo\"\n"
        "• \"Dosis de aplicación de [producto] en [cultivo]\"\n\n"
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
