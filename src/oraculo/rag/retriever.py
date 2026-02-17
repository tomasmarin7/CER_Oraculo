from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Tuple

from ..config import Settings
from ..providers.embeddings import embed_retrieval_query
from ..providers.query_refiner import refine_user_question
from ..vectorstore.qdrant_client import get_qdrant_client
from ..vectorstore.search import query_top_chunks

logger = logging.getLogger(__name__)


def _adapt_query_vector_dim(
    query_vector: List[float],
    target_dim: int,
) -> List[float]:
    current_dim = len(query_vector)
    if current_dim == target_dim:
        return query_vector
    if current_dim < target_dim:
        padded = list(query_vector) + [0.0] * (target_dim - current_dim)
        return padded
    return list(query_vector[:target_dim])


def _select_top_unique_docs(
    hits: List[Dict[str, Any]],
    top_k_docs: int,
) -> List[Dict[str, Any]]:
    """
    Conserva solo el mejor hit por documento (doc_id).

    Se asume que `hits` viene ordenado por score descendente desde Qdrant.
    """
    selected: List[Dict[str, Any]] = []
    seen_doc_ids: set[str] = set()

    for hit in hits:
        payload = hit.get("payload") or {}
        doc_id = str(payload.get("doc_id", "")).strip()

        # Si no hay doc_id, no podemos deduplicar por documento.
        # En ese caso, dejamos pasar el hit para no perder recall.
        if not doc_id:
            selected.append(hit)
        elif doc_id not in seen_doc_ids:
            seen_doc_ids.add(doc_id)
            selected.append(hit)

        if len(selected) >= top_k_docs:
            break

    return selected


def retrieve(
    question: str,
    settings: Settings,
    top_k: int = 8,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Recupera documentos relevantes para la pregunta del usuario.

    Flujo:
      1. Gemini reescribe la pregunta en una consulta optimizada para embeddings.
      2. Se genera el embedding de la consulta optimizada.
      3. Búsqueda vectorial en Qdrant (sin filtros).
      4. Deduplicación por `doc_id` para mantener 1 hit por documento.

    Input:
      - question: pregunta original del usuario
      - settings: configuración global
      - top_k: cantidad de documentos únicos a recuperar
    Output:
      - rewritten_query: consulta optimizada (str)
      - hits: lista de hits (1 por documento) ordenados por score
    """
    started = time.perf_counter()

    # 1) Reescribir la pregunta solo si aporta valor; evita una llamada LLM extra
    # en consultas cortas y directas.
    if _debe_refinar_consulta(question, settings):
        rewritten_query = refine_user_question(question, settings)
    else:
        rewritten_query = (question or "").strip()
        logger.info(
            "RAG CER | Se omite refiner (consulta simple) | entrada=%s chars",
            len(question or ""),
        )

    # 2) Generar embedding de la consulta optimizada
    query_vector = embed_retrieval_query(rewritten_query, settings)
    query_vector = _adapt_query_vector_dim(
        query_vector,
        int(settings.qdrant_cer_chunks_vector_dim),
    )

    # 3) Búsqueda vectorial en Qdrant
    qdrant = get_qdrant_client(settings)
    # Pedimos más chunks para luego quedarnos con top documentos únicos.
    candidate_k = max(top_k * 4, top_k)
    logger.info(
        "📚 Qdrant CER | colección=%s | consultando top_k=%s (candidatos=%s)...",
        settings.qdrant_collection,
        top_k,
        candidate_k,
    )
    raw_hits = query_top_chunks(
        client=qdrant,
        collection=settings.qdrant_collection,
        query_vector=query_vector,
        top_k=candidate_k,
    )
    hits = _select_top_unique_docs(raw_hits, top_k_docs=top_k)

    logger.info(
        "✅ RAG CER OK | tiempo=%sms | colección=%s | hits=%s | documentos_unicos=%s",
        int((time.perf_counter() - started) * 1000),
        settings.qdrant_collection,
        len(raw_hits),
        len(hits),
    )

    return rewritten_query, hits


def _select_top_unique_sag_rows(
    hits: List[Dict[str, Any]],
    top_k_rows: int,
) -> List[Dict[str, Any]]:
    """
    Deduplica resultados SAG por combinación producto/cultivo/objetivo.
    """
    selected: List[Dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for hit in hits:
        payload = hit.get("payload") or {}
        key = (
            str(payload.get("producto_id") or payload.get("nombre_comercial") or "")
            .strip()
            .lower(),
            str(payload.get("cultivo") or "").strip().lower(),
            str(payload.get("objetivo") or "").strip().lower(),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        selected.append(hit)
        if len(selected) >= top_k_rows:
            break

    return selected


def retrieve_sag(
    refined_query: str,
    settings: Settings,
    top_k: int = 8,
) -> List[Dict[str, Any]]:
    """
    Recupera coincidencias relevantes desde la colección SAG.
    """
    started = time.perf_counter()
    query_vector = embed_retrieval_query(refined_query, settings)
    query_vector = _adapt_query_vector_dim(
        query_vector,
        int(settings.qdrant_sag_vector_dim),
    )
    qdrant = get_qdrant_client(settings)
    candidate_k = max(top_k * 3, top_k)
    logger.info(
        "📚 Qdrant SAG | colección=%s | consultando top_k=%s (candidatos=%s)...",
        settings.qdrant_sag_collection,
        top_k,
        candidate_k,
    )
    raw_hits = query_top_chunks(
        client=qdrant,
        collection=settings.qdrant_sag_collection,
        query_vector=query_vector,
        top_k=candidate_k,
    )
    deduped = _select_top_unique_sag_rows(raw_hits, top_k_rows=top_k)
    logger.info(
        "✅ RAG SAG OK | tiempo=%sms | colección=%s | hits=%s | filas_unicas=%s",
        int((time.perf_counter() - started) * 1000),
        settings.qdrant_sag_collection,
        len(raw_hits),
        len(deduped),
    )
    return deduped


def _debe_refinar_consulta(question: str, settings: Settings) -> bool:
    if not bool(settings.rag_use_query_refiner):
        return False
    text = (question or "").strip()
    if not text:
        return False
    # Consultas muy cortas suelen funcionar bien sin refiner.
    if len(text) <= 45 and len(text.split()) <= 7:
        return False
    return True
