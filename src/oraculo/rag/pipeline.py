from __future__ import annotations

import logging

from ..config import get_settings
from ..providers.llm import generate_answer
from ..sources.resolver import format_sources_from_hits
from .doc_context import build_doc_contexts_from_hits
from .prompting import build_answer_prompt_from_doc_contexts
from .retriever import retrieve, retrieve_sag


logger = logging.getLogger(__name__)


def answer(question: str, top_k: int = 8) -> str:
    """
    Pipeline RAG: pregunta → respuesta.

    Flujo:
      1. Cargar configuración.
      2. Retrieve: reescribir pregunta + búsqueda vectorial → hits.
      3. Construir contexto por documento (agrupar chunks por informe).
      4. Construir prompt con contexto + reglas de atribución.
      5. Generar respuesta con LLM.
      6. Adjuntar fuentes.
    """
    settings = get_settings()

    # 1) Retrieve: reescribir pregunta + búsqueda vectorial
    rewritten_query, hits = retrieve(question, settings, top_k=top_k)

    # 2) Construir contexto a nivel de documento (alineado al top_k de retrieve)
    target_docs = max(1, min(top_k, int(settings.rag_top_docs)))
    doc_contexts = build_doc_contexts_from_hits(hits, settings, top_docs=target_docs)

    # 3) Recuperar SAG para que el LLM redacte una sección específica
    sag_hits = []
    try:
        sag_top_k = max(1, int(settings.rag_sag_top_k))
        sag_hits = retrieve_sag(rewritten_query, settings, top_k=sag_top_k)
    except Exception:
        logger.exception("No se pudo recuperar información de la colección SAG")

    # 4) Construir prompt
    prompt = build_answer_prompt_from_doc_contexts(
        question=question,
        refined_question=rewritten_query,
        doc_contexts=doc_contexts,
        sag_hits=sag_hits,
    )

    # 5) Generar respuesta con LLM
    out = generate_answer(prompt, settings, system_instruction="")

    # 6) Adjuntar fuentes CER
    sources_block = format_sources_from_hits(hits)
    if sources_block:
        out = out.rstrip() + "\n\n" + sources_block

    return out
