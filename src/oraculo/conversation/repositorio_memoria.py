from __future__ import annotations

import logging
import threading

from .archive_store import close_session_archive
from .modelos import SesionChat
from .repositorio_sesiones import RepositorioSesiones
from .sesiones import iniciar_sesion, reiniciar_sesion, sesion_expirada
from .texto import ahora_ts

DEFAULT_CLEANUP_INTERVAL_SECONDS = 60
DEFAULT_MAX_SESIONES_EN_MEMORIA = 1000
logger = logging.getLogger(__name__)


class AlmacenSesionesMemoria(RepositorioSesiones):
    """Repositorio en RAM que simula persistencia por `user_id`."""

    def __init__(
        self,
        cleanup_interval_seconds: int = DEFAULT_CLEANUP_INTERVAL_SECONDS,
        max_sesiones_en_memoria: int = DEFAULT_MAX_SESIONES_EN_MEMORIA,
    ) -> None:
        self._sesiones: dict[str, SesionChat] = {}
        self._lock = threading.RLock()
        self._cleanup_interval_seconds = max(int(cleanup_interval_seconds), 1)
        self._max_sesiones_en_memoria = max(int(max_sesiones_en_memoria), 1)
        self._next_cleanup_ts = 0

    def obtener_o_crear(self, user_id: str, ahora: int | None = None) -> SesionChat:
        with self._lock:
            current = ahora or ahora_ts()
            self._cleanup_if_due_locked(current)

            sesion = self._sesiones.get(user_id)
            if not sesion:
                sesion = SesionChat(user_id=user_id)
                iniciar_sesion(sesion, current)
                sesion.flow_data["pending_intro"] = True
            elif sesion_expirada(sesion, current):
                close_session_archive(sesion, reason="session_expired")
                sesion = reiniciar_sesion(sesion, current)
                sesion.flow_data["pending_intro"] = True
            self._sesiones[user_id] = sesion
            self._enforce_size_limit_locked()
            return sesion

    def guardar(self, sesion: SesionChat) -> None:
        with self._lock:
            self._sesiones[sesion.user_id] = sesion
            self._enforce_size_limit_locked()

    def limpiar_expiradas(self, ahora: int | None = None) -> int:
        with self._lock:
            current = ahora or ahora_ts()
            return self._limpiar_expiradas_locked(current)

    def _limpiar_expiradas_locked(self, current: int) -> int:
        expiradas = [
            (uid, s)
            for uid, s in self._sesiones.items()
            if sesion_expirada(s, current)
        ]
        for uid, sesion in expiradas:
            close_session_archive(sesion, reason="session_expired_cleanup")
            del self._sesiones[uid]
        return len(expiradas)

    def _cleanup_if_due_locked(self, current: int) -> None:
        if current < self._next_cleanup_ts:
            return
        removidas = self._limpiar_expiradas_locked(current)
        self._next_cleanup_ts = current + self._cleanup_interval_seconds
        if removidas:
            logger.info("Limpieza de sesiones en RAM: %s expiradas removidas.", removidas)

    def _enforce_size_limit_locked(self) -> None:
        total = len(self._sesiones)
        if total <= self._max_sesiones_en_memoria:
            return

        overflow = total - self._max_sesiones_en_memoria
        victims = sorted(
            self._sesiones.items(),
            key=lambda item: int(item[1].last_activity_ts),
        )[:overflow]
        for user_id, sesion in victims:
            close_session_archive(sesion, reason="session_evicted_memory_cap")
            del self._sesiones[user_id]
        logger.warning(
            "Cap de sesiones en RAM alcanzado: removidas %s sesiones antiguas (cap=%s).",
            overflow,
            self._max_sesiones_en_memoria,
        )
