"""
Teclados inline (keyboards) para el bot de Telegram.
Centraliza todos los botones y menús del bot.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Teclado del menú principal.
    
    Botones:
    - Generar Investigación (en desarrollo)
    - Consultar Base de Datos CER
    """
    keyboard = [
        [
            InlineKeyboardButton(
                "Generar Investigación", callback_data="generate_research"
            )
        ],
        [
            InlineKeyboardButton(
                "Consultar Base de Datos CER", callback_data="consult_database"
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_post_query_keyboard() -> InlineKeyboardMarkup:
    """
    Teclado que se muestra después de responder una consulta.
    
    Botones:
    - Nueva consulta
    - Menú principal
    """
    keyboard = [
        [
            InlineKeyboardButton("Nueva consulta", callback_data="consult_database")
        ],
        [InlineKeyboardButton("Menú principal", callback_data="menu")],
    ]
    return InlineKeyboardMarkup(keyboard)
