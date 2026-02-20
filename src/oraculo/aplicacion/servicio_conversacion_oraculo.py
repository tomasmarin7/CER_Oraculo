from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import threading
import time
from typing import Callable

from ..aplicacion.utiles_prompt import cargar_plantilla_prompt
from ..config import Settings
from ..conversation import (
    EstadoSesion,
    RepositorioSesiones,
    registrar_mensaje_asistente,
    registrar_mensaje_usuario,
    reiniciar_sesion,
    renovar_sesion,
)
from ..conversation.archive_store import close_session_archive, persist_session_archive
from ..conversation.modelos import SesionChat
from ..conversation.texto import historial_corto
from ..conversation.flujo_guiado import (
    execute_guided_action_from_router,
    get_guided_intro_text,
)
from ..providers.llm import generate_answer
from ..router import GlobalRouterDecision, route_global_action
from .modelos_oraculo import RespuestaOraculo
from .texto_oraculo import (
    ACLARACION_ACCION,
    construir_respuesta_chat_basica,
    normalizar_texto,
)

logger = logging.getLogger(__name__)
CLARIFY_PROMPT_FILE = "clarify_response.md"
MAX_LOG_TEXT_PREVIEW = 1200


@dataclass(slots=True)
class ServicioConversacionOraculo:
    repositorio_sesiones: RepositorioSesiones
    _user_locks_guard: threading.Lock = field(init=False, repr=False)
    _user_locks: dict[str, threading.Lock] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._user_locks_guard = threading.Lock()
        self._user_locks = {}

    def procesar_mensaje(
        self,
        *,
        user_id: str,
        mensaje_usuario: str,
        settings: Settings,
        top_k: int = 8,
        progress_callback: Callable[[str], None] | None = None,
    ) -> RespuestaOraculo:
        with self._get_user_lock(user_id):
            return self._procesar_mensaje_serializado(
                user_id=user_id,
                mensaje_usuario=mensaje_usuario,
                settings=settings,
                top_k=top_k,
                progress_callback=progress_callback,
            )

    def _procesar_mensaje_serializado(
        self,
        *,
        user_id: str,
        mensaje_usuario: str,
        settings: Settings,
        top_k: int = 8,
        progress_callback: Callable[[str], None] | None = None,
    ) -> RespuestaOraculo:
        started = time.perf_counter()
        texto = (mensaje_usuario or "").strip()
        sesion = self.repositorio_sesiones.obtener_o_crear(user_id)
        self._reportar_progreso(progress_callback, "Estoy revisando tu consulta para entender bien el problema...")
        logger.info(
            "🟢 Nuevo turno | estado=%s | entrada=%s chars",
            sesion.estado,
            len(texto),
        )
        logger.info("👤 Usuario: %s", _preview_for_log(texto))
        if not texto:
            return RespuestaOraculo(texto="Por favor, escribe una consulta válida.")

        if bool(sesion.flow_data.get("pending_intro")):
            sesion.flow_data["pending_intro"] = False
            sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
            renovar_sesion(sesion)
            intro = get_guided_intro_text()
            registrar_mensaje_asistente(sesion, intro, rag_usado="none")
            self.repositorio_sesiones.guardar(sesion)
            persist_session_archive(sesion)
            logger.info(
                "👋 Se responde introducción inicial | tiempo=%sms",
                int((time.perf_counter() - started) * 1000),
            )
            return RespuestaOraculo(texto=intro)

        registrar_mensaje_usuario(sesion, texto)
        persist_session_archive(sesion)

        self._reportar_progreso(
            progress_callback,
            "Definiendo el siguiente paso de la conversación...",
        )
        decision = route_global_action(
            sesion,
            texto,
            settings,
            progress_callback=progress_callback,
        )
        self._agregar_trace_router(sesion, decision)
        logger.info("🧠 Acción elegida por router global: %s", decision.action)

        if decision.action == "ASK_PROBLEM":
            close_session_archive(sesion, reason="router_ask_problem")
            reiniciar_sesion(sesion)
            sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
            renovar_sesion(sesion)
            respuesta = get_guided_intro_text()
            return self._cerrar_turno(
                sesion,
                respuesta,
                rag_usado="none",
                started=started,
                fase="ask_problem",
            )

        if self._debe_ejecutar_flujo_guiado(sesion.estado, decision.action):
            logger.info("🔎 Ejecutando flujo guiado CER/SAG según acción del router...")
            resultado = execute_guided_action_from_router(
                sesion,
                texto,
                settings,
                action=decision.action,
                query=decision.query,
                top_k=top_k,
                progress_callback=progress_callback,
            )
            if resultado.handled:
                logger.info("✅ Flujo guiado completado.")
                return self._cerrar_turno(
                    sesion,
                    resultado.response,
                    rag_usado=resultado.rag_tag,
                    fuentes=resultado.sources,
                    started=started,
                    fase="guided",
                )
            return self._cerrar_turno(
                sesion,
                ACLARACION_ACCION,
                rag_usado="none",
                started=started,
                fase="clarify_after_guided",
            )

        if decision.action == "CHAT_REPLY":
            self._reportar_progreso(progress_callback, "Estoy preparando la respuesta con lo que ya revisamos...")
            contextual = execute_guided_action_from_router(
                sesion,
                texto,
                settings,
                action="CHAT_REPLY",
                query="",
                top_k=top_k,
                progress_callback=progress_callback,
            )
            if contextual.handled and str(contextual.response or "").strip():
                return self._cerrar_turno(
                    sesion,
                    contextual.response,
                    rag_usado=contextual.rag_tag,
                    fuentes=contextual.sources,
                    started=started,
                    fase="chat_reply_contextual",
                )
            return self._cerrar_turno(
                sesion,
                construir_respuesta_chat_basica(texto),
                rag_usado="none",
                started=started,
                fase="chat_reply",
            )

        if decision.action == "CLARIFY":
            self._reportar_progreso(progress_callback, "Estoy preparando un mensaje para entenderte mejor...")
            return self._cerrar_turno(
                sesion,
                self._construir_clarificacion_contextual(
                    sesion,
                    texto,
                    decision,
                    settings,
                    progress_callback=progress_callback,
                ),
                rag_usado="none",
                started=started,
                fase="clarify_contextual",
            )

        return self._cerrar_turno(
            sesion,
            ACLARACION_ACCION,
            rag_usado="none",
            started=started,
            fase="clarify_fallback",
        )

    def _reportar_progreso(
        self,
        progress_callback: Callable[[str], None] | None,
        mensaje: str,
    ) -> None:
        if not progress_callback:
            return
        try:
            progress_callback(mensaje)
        except Exception:
            logger.debug("No se pudo reportar progreso al canal de salida.")

    def _cerrar_turno(
        self,
        sesion: SesionChat,
        texto: str,
        *,
        rag_usado: str,
        fuentes: list[str] | None = None,
        started: float | None = None,
        fase: str = "unknown",
    ) -> RespuestaOraculo:
        registrar_mensaje_asistente(
            sesion,
            texto,
            fuentes=fuentes,
            rag_usado=rag_usado,
        )
        self.repositorio_sesiones.guardar(sesion)
        persist_session_archive(sesion)
        elapsed_ms = None
        if started is not None:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "🏁 Turno finalizado | fase=%s | estado=%s | rag=%s | salida=%s chars | tiempo=%sms",
            fase,
            sesion.estado,
            rag_usado,
            len(texto),
            elapsed_ms if elapsed_ms is not None else -1,
        )
        logger.info("🤖 Bot: %s", _preview_for_log(texto))
        return RespuestaOraculo(
            texto=texto,
            rag_usado=rag_usado,
            fuentes=list(fuentes or []),
        )

    def _debe_ejecutar_flujo_guiado(self, estado: EstadoSesion, accion: str) -> bool:
        if accion in {"NEW_CER_QUERY", "DETAIL_FROM_LIST", "ASK_SAG"}:
            return True
        if estado in {
            EstadoSesion.ESPERANDO_DETALLE_PRODUCTO,
            EstadoSesion.ESPERANDO_CONFIRMACION_SAG,
        }:
            return accion in {"CHAT_REPLY", "CLARIFY"}
        return False

    def _agregar_trace_router(
        self,
        sesion: SesionChat,
        decision: GlobalRouterDecision,
    ) -> None:
        trace = sesion.flow_data.get("router_trace")
        if not isinstance(trace, list):
            trace = []
        trace.append(
            {
                "action": decision.action,
                "query": decision.query,
                "rationale": decision.rationale,
                "ts": sesion.last_activity_ts,
            }
        )
        sesion.flow_data["router_trace"] = trace[-50:]

    def _construir_clarificacion_contextual(
        self,
        sesion: SesionChat,
        mensaje_usuario: str,
        decision: GlobalRouterDecision,
        settings: Settings,
        progress_callback: Callable[[str], None] | None = None,
    ) -> str:
        try:
            self._reportar_progreso(
                progress_callback,
                "Preparando una aclaracion basada en el historial de la conversacion...",
            )
            template = cargar_plantilla_prompt(
                Path(__file__).resolve().parent / "prompts",
                CLARIFY_PROMPT_FILE,
            )
            offered_reports = sesion.flow_data.get("offered_reports") or []
            report_lines: list[str] = []
            for report in offered_reports:
                if not isinstance(report, dict):
                    continue
                label = str(report.get("label") or "").strip()
                products = [str(p).strip() for p in (report.get("products") or []) if str(p).strip()]
                report_lines.append(f"• {label}: {', '.join(products)}")
            offered_reports_text = "\n".join(report_lines) if report_lines else "sin opciones"

            prompt = (
                template.replace("{{estado_actual}}", str(sesion.estado))
                .replace("{{last_rag_used}}", sesion.last_rag_used)
                .replace("{{last_question}}", str(sesion.flow_data.get("last_question") or ""))
                .replace("{{router_rationale}}", decision.rationale or "sin motivo explícito")
                .replace("{{historial}}", historial_corto(sesion))
                .replace("{{offered_reports}}", offered_reports_text)
                .replace("{{mensaje_usuario}}", mensaje_usuario)
            ).strip()
            response = (
                generate_answer(prompt, settings, system_instruction="", profile="complex") or ""
            ).strip()
            if response:
                return self._evitar_repeticion_clarify(sesion, response, mensaje_usuario)
        except Exception:
            logger.exception("No se pudo generar clarificación contextual; usando fallback.")
        return self._evitar_repeticion_clarify(
            sesion,
            ACLARACION_ACCION,
            mensaje_usuario,
        )

    def _evitar_repeticion_clarify(
        self,
        sesion: SesionChat,
        candidate: str,
        mensaje_usuario: str,
    ) -> str:
        texto = (candidate or "").strip()
        if not texto:
            texto = ACLARACION_ACCION
        previo = self._ultimo_mensaje_asistente(sesion)
        if normalizar_texto(previo) != normalizar_texto(texto):
            return texto

        last_question = str(sesion.flow_data.get("last_question") or "").strip()
        if last_question:
            return (
                "Para entender bien tu contexto y no asumir mal: "
                f"cuando dices \"{last_question}\", "
                "¿qué cultivo y problema quieres priorizar?"
            )
        user_text = (mensaje_usuario or "").strip()
        if user_text:
            return (
                "Necesito un poco más de contexto para ayudarte bien. "
                f"Cuando dices \"{user_text}\", ¿te refieres a un cultivo, un problema específico "
                "o a un producto puntual?"
            )
        return (
            "Para avanzar, cuéntame el cultivo y el problema puntual que quieres revisar."
        )

    def _ultimo_mensaje_asistente(self, sesion: SesionChat) -> str:
        for msg in reversed(sesion.mensajes):
            if msg.rol == "assistant":
                return msg.texto
        return ""

    def _get_user_lock(self, user_id: str) -> threading.Lock:
        with self._user_locks_guard:
            lock = self._user_locks.get(user_id)
            if lock is None:
                lock = threading.Lock()
                self._user_locks[user_id] = lock
            return lock


def _preview_for_log(text: str, max_len: int = MAX_LOG_TEXT_PREVIEW) -> str:
    value = " ".join(str(text or "").strip().split())
    if len(value) <= max_len:
        return value
    return value[:max_len] + "…"
