from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Settings
from ..providers.llm import generate_answer
from ..rag.doc_context import build_doc_contexts_from_hits
from ..rag.prompting import build_answer_prompt_from_doc_contexts
from ..rag.retriever import retrieve, retrieve_sag
from ..sources.resolver import format_sources_from_hits
from .modelos import AccionRouter, DecisionRouter, EstadoSesion, SesionChat
from .texto import historial_corto, ultimo_mensaje_usuario

PROMPT_CONVERSACION_FILE = "conversation.md"


def ejecutar_decision(
    decision: DecisionRouter,
    sesion: SesionChat,
    settings: Settings,
    top_k: int = 8,
) -> str:
    if decision.accion == AccionRouter.IR_MENU:
        sesion.estado = EstadoSesion.MENU
        return "Volvamos al menú principal."

    if decision.accion == AccionRouter.CHAT_NORMAL:
        sesion.estado = EstadoSesion.CONVERSACION
        return _responder_chat_normal(sesion, settings)

    sesion.estado = EstadoSesion.CONVERSACION
    pregunta = decision.consulta_rag or ultimo_mensaje_usuario(sesion)

    if decision.accion == AccionRouter.RAG_CER:
        return _respuesta_rag_cer(pregunta, settings, top_k)
    if decision.accion == AccionRouter.RAG_SAG:
        return _respuesta_rag_sag(pregunta, settings, top_k)
    return _respuesta_rag_ambas(pregunta, settings, top_k)


def _responder_chat_normal(sesion: SesionChat, settings: Settings) -> str:
    prompt = _prompt_chat_con_memoria(sesion)
    return generate_answer(prompt, settings, system_instruction="")


def _prompt_chat_con_memoria(sesion: SesionChat) -> str:
    template = _cargar_template_prompt(PROMPT_CONVERSACION_FILE)
    historial = historial_corto(sesion)
    fuentes = "\n".join(sesion.last_sources) if sesion.last_sources else "sin fuentes"
    return (
        template.replace("{{resumen}}", sesion.resumen or "sin resumen")
        .replace("{{historial}}", historial)
        .replace("{{fuentes_ultima_busqueda}}", fuentes)
        .strip()
    )


def _cargar_template_prompt(filename: str) -> str:
    base_dir = Path(__file__).resolve().parents[1]  # .../oraculo
    prompt_path = base_dir / "prompts" / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _respuesta_rag_ambas(question: str, settings: Settings, top_k: int) -> str:
    return _respuesta_rag(question, settings, top_k, incluir_cer=True, incluir_sag=True)


def _respuesta_rag_cer(question: str, settings: Settings, top_k: int) -> str:
    return _respuesta_rag(question, settings, top_k, incluir_cer=True, incluir_sag=False)


def _respuesta_rag_sag(question: str, settings: Settings, top_k: int) -> str:
    return _respuesta_rag(question, settings, top_k, incluir_cer=False, incluir_sag=True)


def _respuesta_rag(
    question: str,
    settings: Settings,
    top_k: int,
    incluir_cer: bool,
    incluir_sag: bool,
) -> str:
    refined = question
    hits_cer: list[dict[str, Any]] = []
    hits_sag: list[dict[str, Any]] = []

    if incluir_cer:
        refined, hits_cer = retrieve(question, settings, top_k=top_k)
    if incluir_sag:
        query_sag = refined if refined else question
        hits_sag = retrieve_sag(query_sag, settings, top_k=max(1, int(settings.rag_sag_top_k)))

    doc_contexts = build_doc_contexts_from_hits(
        hits_cer,
        settings,
        top_docs=max(1, min(top_k, int(settings.rag_top_docs))),
    )
    prompt = build_answer_prompt_from_doc_contexts(
        question=question,
        refined_question=refined,
        doc_contexts=doc_contexts,
        sag_hits=hits_sag,
    )
    texto = generate_answer(prompt, settings, system_instruction="").rstrip()

    if not hits_cer:
        return texto
    fuentes = format_sources_from_hits(hits_cer)
    if not fuentes:
        return texto
    return texto + "\n\n" + fuentes
