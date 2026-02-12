from __future__ import annotations

from .modelos import SesionChat
from .repositorio_sesiones import RepositorioSesiones
from .sesiones import reiniciar_sesion, sesion_expirada
from .texto import ahora_ts


class AlmacenSesionesMemoria(RepositorioSesiones):
    """Repositorio en RAM que simula persistencia por `user_id`."""

    def __init__(self) -> None:
        self._sesiones: dict[str, SesionChat] = {}

    def obtener_o_crear(self, user_id: str, ahora: int | None = None) -> SesionChat:
        current = ahora or ahora_ts()
        sesion = self._sesiones.get(user_id)
        if not sesion:
            sesion = SesionChat(user_id=user_id)
        elif sesion_expirada(sesion, current):
            sesion = reiniciar_sesion(sesion, current)
        self._sesiones[user_id] = sesion
        return sesion

    def guardar(self, sesion: SesionChat) -> None:
        self._sesiones[sesion.user_id] = sesion

    def limpiar_expiradas(self, ahora: int | None = None) -> int:
        current = ahora or ahora_ts()
        expiradas = [uid for uid, s in self._sesiones.items() if sesion_expirada(s, current)]
        for uid in expiradas:
            del self._sesiones[uid]
        return len(expiradas)
