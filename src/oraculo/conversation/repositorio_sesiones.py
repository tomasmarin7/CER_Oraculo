from __future__ import annotations

from typing import Protocol

from .modelos import SesionChat


class RepositorioSesiones(Protocol):
    """Contrato de persistencia de sesiones (memoria, DynamoDB, etc.)."""

    def obtener_o_crear(self, user_id: str, ahora: int | None = None) -> SesionChat:
        ...

    def guardar(self, sesion: SesionChat) -> None:
        ...

    def limpiar_expiradas(self, ahora: int | None = None) -> int:
        ...
