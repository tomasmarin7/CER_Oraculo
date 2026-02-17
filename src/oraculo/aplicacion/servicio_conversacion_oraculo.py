from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
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
    get_guided_intro_text,
    try_handle_guided_flow,
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


@dataclass(slots=True)
class ServicioConversacionOraculo:
    repositorio_sesiones: RepositorioSesiones

    def procesar_mensaje(
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
        self._reportar_progreso(progress_callback, "Analizando tu mensaje...")
        logger.info(
            "🟢 Nuevo turno | estado=%s | entrada=%s chars",
            sesion.estado,
            len(texto),
        )
        if not texto:
            return RespuestaOraculo(texto="Por favor, escribe una consulta válida.")

        registrar_mensaje_usuario(sesion, texto)
        persist_session_archive(sesion)

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

        # Fast path para ahorrar una llamada LLM del router global cuando el estado
        # ya indica claramente seguimiento de detalle o confirmación SAG.
        if self._debe_ir_directo_a_flujo_guiado(sesion, texto):
            logger.info(
                "⚡ Fast path activado | flujo=seguimiento | estado=%s",
                sesion.estado,
            )
            resultado = try_handle_guided_flow(
                sesion,
                texto,
                settings,
                top_k,
                progress_callback=progress_callback,
            )
            if resultado.handled:
                logger.info("🧭 Resultado de seguimiento: respuesta directa desde contexto.")
                return self._cerrar_turno(
                    sesion,
                    resultado.response,
                    rag_usado=resultado.rag_tag,
                    fuentes=resultado.sources,
                    started=started,
                    fase="fastpath_guided",
                )

        self._reportar_progreso(
            progress_callback,
            "Definiendo si corresponde busqueda CER, consulta SAG o seguimiento...",
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
            resultado = try_handle_guided_flow(
                sesion,
                texto,
                settings,
                top_k,
                forced_action=decision.action,
                forced_query=decision.query,
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
            self._reportar_progreso(progress_callback, "Redactando respuesta...")
            return self._cerrar_turno(
                sesion,
                construir_respuesta_chat_basica(texto),
                rag_usado="none",
                started=started,
                fase="chat_reply",
            )

        if decision.action == "CLARIFY":
            self._reportar_progreso(progress_callback, "Aclarando tu consulta con el contexto actual...")
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

    def _debe_ir_directo_a_flujo_guiado(self, sesion: SesionChat, texto: str) -> bool:
        estado = sesion.estado
        if estado == EstadoSesion.ESPERANDO_CONFIRMACION_SAG:
            return True
        if estado != EstadoSesion.ESPERANDO_DETALLE_PRODUCTO:
            return False

        offered_reports = sesion.flow_data.get("offered_reports")
        if not isinstance(offered_reports, list) or not offered_reports:
            return False

        normalized = normalizar_texto(texto)
        if normalized in {"menu", "menú", "inicio", "/start"}:
            return False
        if any(
            token in normalized
            for token in (
                "nueva busqueda",
                "otro problema",
                "otro cultivo",
                "cambiar de tema",
                "nueva consulta",
            )
        ):
            return False
        # En estado de detalle, por defecto el siguiente mensaje se interpreta
        # como seguimiento del/los informes ya entregados.
        return True

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
                return response
        except Exception:
            logger.exception("No se pudo generar clarificación contextual; usando fallback.")
        return ACLARACION_ACCION
