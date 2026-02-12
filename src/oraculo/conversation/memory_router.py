"""Fachada de compatibilidad para memoria conversacional y router.

Este modulo re-exporta la API publica para evitar romper imports existentes,
pero la implementacion real esta separada por responsabilidades.
"""

from .ejecutor import ejecutar_decision
from .modelos import AccionRouter, DecisionRouter, EstadoSesion, SesionChat
from .repositorio_dynamodb import RepositorioSesionesDynamoDB
from .repositorio_memoria import AlmacenSesionesMemoria
from .repositorio_sesiones import RepositorioSesiones
from .router import construir_prompt_router, routear_siguiente_accion
from .sesiones import (
    registrar_mensaje_asistente,
    registrar_mensaje_usuario,
    reiniciar_sesion,
    renovar_sesion,
    sesion_expirada,
)
from .texto import extraer_fuentes_desde_respuesta

__all__ = [
    "AccionRouter",
    "AlmacenSesionesMemoria",
    "DecisionRouter",
    "EstadoSesion",
    "SesionChat",
    "construir_prompt_router",
    "ejecutar_decision",
    "extraer_fuentes_desde_respuesta",
    "registrar_mensaje_asistente",
    "registrar_mensaje_usuario",
    "renovar_sesion",
    "reiniciar_sesion",
    "RepositorioSesiones",
    "RepositorioSesionesDynamoDB",
    "routear_siguiente_accion",
    "sesion_expirada",
]
