from __future__ import annotations

from typing import Any

from .modelos import SesionChat
from .repositorio_sesiones import RepositorioSesiones


class RepositorioSesionesDynamoDB(RepositorioSesiones):
    """Stub para futura persistencia real en DynamoDB."""

    def __init__(self, table_name: str, client: Any | None = None) -> None:
        self.table_name = table_name
        self.client = client

    def obtener_o_crear(self, user_id: str, ahora: int | None = None) -> SesionChat:
        raise NotImplementedError(
            "RepositorioSesionesDynamoDB.obtener_o_crear aun no implementado"
        )

    def guardar(self, sesion: SesionChat) -> None:
        raise NotImplementedError(
            "RepositorioSesionesDynamoDB.guardar aun no implementado"
        )

    def limpiar_expiradas(self, ahora: int | None = None) -> int:
        # DynamoDB TTL elimina de forma asincrona.
        return 0
