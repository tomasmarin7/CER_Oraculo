from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..config import Settings
from ..providers.llm import generate_answer
from .modelos import AccionRouter, DecisionRouter, EstadoSesion, SesionChat
from .texto import es_comando_menu, historial_corto, limpiar_texto

logger = logging.getLogger(__name__)
PROMPT_ROUTER_SYSTEM_FILE = "router_system.md"


def construir_prompt_router(sesion: SesionChat, mensaje_usuario: str) -> str:
    historial = historial_corto(sesion)
    return (
        "Decide la siguiente accion del asistente agronomico. "
        "Responde SOLO un JSON valido, sin markdown.\n\n"
        f"estado_actual: {sesion.estado}\n"
        f"ultimo_rag: {sesion.last_rag_used}\n"
        f"resumen: {sesion.resumen or 'sin resumen'}\n"
        f"historial_reciente:\n{historial}\n\n"
        f"mensaje_usuario: {mensaje_usuario}\n\n"
        "Acciones validas: RUN_RAG_CER, RUN_RAG_SAG, RUN_RAG_BOTH, CHAT_ONLY, GO_MENU.\n"
        "Salida JSON exacta:\n"
        '{"accion":"...","motivo":"...","consulta_rag":"...","confianza":0.0}'
    )


def routear_siguiente_accion(
    sesion: SesionChat,
    mensaje_usuario: str,
    settings: Settings,
) -> DecisionRouter:
    texto = limpiar_texto(mensaje_usuario)
    decision = _decision_por_reglas(sesion, texto)
    if decision:
        return decision

    prompt = construir_prompt_router(sesion, texto)
    try:
        system = _cargar_system_router(PROMPT_ROUTER_SYSTEM_FILE)
        salida = generate_answer(prompt, settings, system_instruction=system)
        return _parsear_decision_router(salida, fallback_consulta=texto)
    except Exception as exc:
        logger.warning("Router LLM fallo, aplicando fallback local: %s", exc)
        return _decision_fallback(texto)


def _decision_por_reglas(sesion: SesionChat, texto: str) -> DecisionRouter | None:
    if es_comando_menu(texto):
        return DecisionRouter(AccionRouter.IR_MENU, "Comando de menu", confianza=1.0)
    if sesion.estado == EstadoSesion.ESPERANDO_PREGUNTA:
        return DecisionRouter(
            AccionRouter.RAG_AMBAS,
            "Primera pregunta tras boton consultar",
            consulta_rag=texto,
            confianza=1.0,
        )
    return None


def _decision_fallback(texto: str) -> DecisionRouter:
    if "?" in texto or len(texto.split(" ")) >= 6:
        return DecisionRouter(
            AccionRouter.RAG_AMBAS,
            "Fallback: parece consulta nueva",
            consulta_rag=texto,
            confianza=0.55,
        )
    return DecisionRouter(
        AccionRouter.CHAT_NORMAL,
        "Fallback: seguimiento conversacional",
        consulta_rag="",
        confianza=0.45,
    )


def _cargar_system_router(filename: str) -> str:
    base_dir = Path(__file__).resolve().parents[1]  # .../oraculo
    prompt_path = base_dir / "prompts" / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _parsear_decision_router(texto_modelo: str, fallback_consulta: str) -> DecisionRouter:
    data = _extraer_json(texto_modelo)
    accion = _accion_valida(str(data.get("accion", "")).strip()) or AccionRouter.CHAT_NORMAL
    motivo = limpiar_texto(str(data.get("motivo", ""))) or "Sin motivo"
    consulta = limpiar_texto(str(data.get("consulta_rag", ""))) or fallback_consulta
    confianza = _normalizar_confianza(data.get("confianza"))

    if accion == AccionRouter.CHAT_NORMAL:
        consulta = ""
    return DecisionRouter(accion=accion, motivo=motivo, consulta_rag=consulta, confianza=confianza)


def _extraer_json(texto: str) -> dict[str, Any]:
    limpio = (texto or "").strip()
    if not limpio:
        return {}
    try:
        return json.loads(limpio)
    except json.JSONDecodeError:
        pass

    inicio = limpio.find("{")
    fin = limpio.rfind("}")
    if inicio == -1 or fin == -1 or fin <= inicio:
        return {}
    try:
        return json.loads(limpio[inicio : fin + 1])
    except json.JSONDecodeError:
        return {}


def _accion_valida(valor: str) -> AccionRouter | None:
    try:
        return AccionRouter(valor)
    except ValueError:
        return None


def _normalizar_confianza(valor: Any) -> float:
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, num))
