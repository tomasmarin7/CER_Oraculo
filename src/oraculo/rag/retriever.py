from __future__ import annotations

import logging
import time
import unicodedata
from typing import Any, Dict, List, Tuple

from qdrant_client import models as qm

from ..config import Settings
from ..providers.embeddings import embed_retrieval_query
from ..query_enhancer import enhance_cer_query, enhance_sag_query
from ..vectorstore.qdrant_client import get_qdrant_client
from ..vectorstore.search import query_top_chunks, scroll_points_by_filter

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
    conversation_context: str = "",
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Recupera documentos relevantes para la pregunta del usuario.

    Flujo:
      1. Query enhancer CER unifica conversación + señales CER.csv.
      2. Se genera embedding de la consulta mejorada.
      3. Búsqueda vectorial en Qdrant, opcionalmente con filtro por metadata CER.
      4. Enriquecimiento con scroll por filtro para mejorar recall.
      5. Deduplicación por `doc_id` para mantener 1 hit por documento.

    Input:
      - question: pregunta original del usuario
      - settings: configuración global
      - top_k: cantidad de documentos únicos a recuperar
    Output:
      - rewritten_query: consulta optimizada (str)
      - hits: lista de hits (1 por documento) ordenados por score
    """
    started = time.perf_counter()

    # 1) Query enhancer unificado CER (con señales desde conversación + CER.csv).
    enhancement = enhance_cer_query(
        user_message=question,
        settings=settings,
        conversation_context=conversation_context,
    )
    rewritten_query = enhancement.enhanced_query or (question or "").strip()

    # 2) Generar embedding de la consulta optimizada.
    query_vector = embed_retrieval_query(rewritten_query, settings)
    query_vector = _adapt_query_vector_dim(
        query_vector,
        int(settings.qdrant_cer_chunks_vector_dim),
    )

    # 3) Búsqueda vectorial en Qdrant (con filtro opcional por metadata CER).
    qdrant = get_qdrant_client(settings)
    query_filter = _build_cer_query_filter((enhancement.csv_signals if enhancement else {}))
    effective_top_k = max(1, int(top_k))
    if enhancement and enhancement.exhaustive_hint and enhancement.matched_records_count > effective_top_k:
        effective_top_k = min(max(effective_top_k * 2, 12), 20)
    candidate_k = max(effective_top_k * (6 if query_filter else 4), effective_top_k)
    logger.info(
        "📚 Qdrant CER | colección=%s | top_k=%s->%s | candidatos=%s | filtro=%s...",
        settings.qdrant_collection,
        top_k,
        effective_top_k,
        candidate_k,
        "si" if query_filter else "no",
    )
    raw_hits = query_top_chunks(
        client=qdrant,
        collection=settings.qdrant_collection,
        query_vector=query_vector,
        top_k=candidate_k,
        query_filter=query_filter,
    )

    # 4) Refuerzo de recall por scroll filtrado para solicitudes amplias.
    if query_filter and len(raw_hits) < candidate_k:
        extra_points = scroll_points_by_filter(
            client=qdrant,
            collection=settings.qdrant_collection,
            query_filter=query_filter,
            limit_per_page=256,
            max_points=max(effective_top_k * 20, 300),
        )
        extra_hits = [{"id": p.get("id"), "score": 0.0, "payload": p.get("payload") or {}} for p in extra_points]
        raw_hits = _merge_hits_by_id(raw_hits, extra_hits)

    hits = _select_top_unique_docs(raw_hits, top_k_docs=effective_top_k)

    logger.info(
        "✅ RAG CER OK | tiempo=%sms | colección=%s | hits=%s | documentos_unicos=%s | csv_matches=%s",
        int((time.perf_counter() - started) * 1000),
        settings.qdrant_collection,
        len(raw_hits),
        len(hits),
        enhancement.matched_records_count if enhancement else 0,
    )

    return rewritten_query, hits


def _build_cer_query_filter(csv_signals: dict[str, set[str]]) -> qm.Filter | None:
    if not csv_signals:
        return None

    should: list[Any] = []

    especies = sorted(str(v).strip() for v in csv_signals.get("especies", set()) if str(v).strip())[:4]
    productos = sorted(str(v).strip() for v in csv_signals.get("productos", set()) if str(v).strip())[:6]
    variedades = sorted(str(v).strip() for v in csv_signals.get("variedades", set()) if str(v).strip())[:6]
    clientes = sorted(str(v).strip() for v in csv_signals.get("clientes", set()) if str(v).strip())[:4]

    if especies:
        should.extend(
            qm.FieldCondition(key="especie", match=qm.MatchValue(value=variant))
            for value in especies
            for variant in _payload_value_variants(value)
        )
    if productos:
        should.extend(
            qm.FieldCondition(key="producto", match=qm.MatchValue(value=variant))
            for value in productos
            for variant in _payload_value_variants(value)
        )
    if variedades:
        should.extend(
            qm.FieldCondition(key="variedad", match=qm.MatchValue(value=variant))
            for value in variedades
            for variant in _payload_value_variants(value)
        )
    if clientes:
        should.extend(
            qm.FieldCondition(key="cliente", match=qm.MatchValue(value=variant))
            for value in clientes
            for variant in _payload_value_variants(value)
        )

    if not should:
        return None

    return qm.Filter(should=should)


def _payload_value_variants(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    compact = " ".join(raw.split())
    ascii_compact = unicodedata.normalize("NFD", compact)
    ascii_compact = "".join(ch for ch in ascii_compact if unicodedata.category(ch) != "Mn")
    upper = compact.upper()
    ascii_upper = ascii_compact.upper()
    underscored = upper.replace(" ", "_").replace("/", "_").replace("-", "_")
    ascii_underscored = ascii_upper.replace(" ", "_").replace("/", "_").replace("-", "_")
    normalized = "_".join(part for part in underscored.split("_") if part)
    ascii_normalized = "_".join(part for part in ascii_underscored.split("_") if part)
    variants = [
        compact,
        compact.title(),
        upper,
        normalized,
        ascii_compact,
        ascii_compact.title(),
        ascii_upper,
        ascii_normalized,
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for item in variants:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _merge_hits_by_id(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(hit: dict[str, Any]) -> None:
        hid = str(hit.get("id") or "").strip()
        if hid and hid in seen_ids:
            return
        if hid:
            seen_ids.add(hid)
        merged.append(hit)

    for hit in left:
        _add(hit)
    for hit in right:
        _add(hit)
    return merged


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
    conversation_context: str = "",
) -> List[Dict[str, Any]]:
    """
    Recupera coincidencias relevantes desde la colección SAG.
    """
    started = time.perf_counter()

    enhancement = enhance_sag_query(
        user_message=refined_query,
        settings=settings,
        conversation_context=conversation_context,
    )
    query_text = enhancement.enhanced_query or (refined_query or "").strip()

    query_vector = embed_retrieval_query(query_text, settings)
    source_dim = len(query_vector)
    query_vector = _adapt_query_vector_dim(
        query_vector,
        int(settings.qdrant_sag_vector_dim),
    )
    target_dim = len(query_vector)
    qdrant = get_qdrant_client(settings)
    query_filter = _build_sag_query_filter(
        product_ids=(enhancement.csv_product_ids if enhancement else set()),
        auth_numbers=(enhancement.csv_auth_numbers if enhancement else set()),
    )
    effective_top_k = max(1, int(top_k))
    if enhancement and enhancement.exhaustive_hint and enhancement.matched_records_count > effective_top_k:
        effective_top_k = min(max(effective_top_k * 2, 20), 40)
    candidate_k = max(effective_top_k * (4 if query_filter else 3), effective_top_k)
    logger.info(
        "📚 Qdrant SAG | colección=%s | top_k=%s->%s | candidatos=%s | filtro=%s...",
        settings.qdrant_sag_collection,
        top_k,
        effective_top_k,
        candidate_k,
        "si" if query_filter else "no",
    )
    raw_hits = query_top_chunks(
        client=qdrant,
        collection=settings.qdrant_sag_collection,
        query_vector=query_vector,
        top_k=candidate_k,
        query_filter=query_filter,
    )

    if query_filter and enhancement and (enhancement.csv_product_ids or enhancement.csv_auth_numbers):
        if len(raw_hits) < candidate_k:
            extra_rows = retrieve_sag_rows_by_ids(
                settings=settings,
                product_ids=enhancement.csv_product_ids,
                auth_numbers=enhancement.csv_auth_numbers,
                max_rows=max(effective_top_k * 45, 1200),
            )
            raw_hits = _merge_hits_by_id(raw_hits, extra_rows)

    deduped = _select_top_unique_sag_rows(raw_hits, top_k_rows=effective_top_k)
    logger.info(
        "✅ RAG SAG OK | tiempo=%sms | colección=%s | hits=%s | filas_unicas=%s | dim=%s->%s | csv_matches=%s",
        int((time.perf_counter() - started) * 1000),
        settings.qdrant_sag_collection,
        len(raw_hits),
        len(deduped),
        source_dim,
        target_dim,
        enhancement.matched_records_count if enhancement else 0,
    )
    return deduped


def _build_sag_query_filter(
    *,
    product_ids: set[str],
    auth_numbers: set[str],
) -> qm.Filter | None:
    pid_filters = [
        qm.FieldCondition(key="producto_id", match=qm.MatchValue(value=pid))
        for pid in sorted(str(x).strip() for x in product_ids if str(x).strip())[:200]
    ]
    auth_filters = [
        qm.FieldCondition(
            key="autorizacion_sag_numero_normalizado",
            match=qm.MatchValue(value=auth),
        )
        for auth in sorted(str(x).strip() for x in auth_numbers if str(x).strip())[:200]
    ]
    should = [*pid_filters, *auth_filters]
    if not should:
        return None
    return qm.Filter(should=should)


def retrieve_sag_rows_for_products(
    seed_hits: List[Dict[str, Any]],
    settings: Settings,
    max_rows_per_filter: int = 120,
) -> List[Dict[str, Any]]:
    """
    Enriquece resultados SAG recuperando todas las filas/chunks asociadas
    a los productos detectados (por producto_id y autorización SAG).
    """
    if not seed_hits:
        return []

    started = time.perf_counter()
    qdrant = get_qdrant_client(settings)
    collection = settings.qdrant_sag_collection

    merged: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(hit: Dict[str, Any]) -> None:
        hid = str(hit.get("id") or "")
        if hid and hid in seen_ids:
            return
        if hid:
            seen_ids.add(hid)
        merged.append(hit)

    product_ids: set[str] = set()
    auth_numbers: set[str] = set()

    for hit in seed_hits:
        _add(hit)
        payload = hit.get("payload") or {}
        product_id = str(payload.get("producto_id") or "").strip()
        auth = str(payload.get("autorizacion_sag_numero_normalizado") or "").strip()
        if product_id:
            product_ids.add(product_id)
        if auth:
            auth_numbers.add(auth)

    max_product_terms = max(1, min(len(product_ids), 500))
    max_auth_terms = max(1, min(len(auth_numbers), 500))

    product_filters = [
        qm.FieldCondition(key="producto_id", match=qm.MatchValue(value=product_id))
        for product_id in sorted(product_ids)[:max_product_terms]
    ]
    if product_filters:
        points = scroll_points_by_filter(
            client=qdrant,
            collection=collection,
            query_filter=qm.Filter(should=product_filters),
            limit_per_page=256,
            max_points=max_rows_per_filter,
        )
        for p in points:
            _add(p)

    auth_filters = [
        qm.FieldCondition(
            key="autorizacion_sag_numero_normalizado",
            match=qm.MatchValue(value=auth),
        )
        for auth in sorted(auth_numbers)[:max_auth_terms]
    ]
    if auth_filters:
        points = scroll_points_by_filter(
            client=qdrant,
            collection=collection,
            query_filter=qm.Filter(should=auth_filters),
            limit_per_page=256,
            max_points=max_rows_per_filter,
        )
        for p in points:
            _add(p)

    logger.info(
        "📦 SAG enrich | seed=%s | producto_ids=%s/%s | autorizaciones=%s/%s | filas_totales=%s | tiempo=%sms",
        len(seed_hits),
        min(len(product_ids), max_product_terms),
        len(product_ids),
        min(len(auth_numbers), max_auth_terms),
        len(auth_numbers),
        len(merged),
        int((time.perf_counter() - started) * 1000),
    )
    return merged


def retrieve_sag_rows_by_ids(
    *,
    settings: Settings,
    product_ids: set[str] | None = None,
    auth_numbers: set[str] | None = None,
    max_rows: int = 2000,
) -> List[Dict[str, Any]]:
    """
    Recupera filas SAG por lista de producto_id y/o autorización SAG.
    """
    pids = {str(x).strip() for x in (product_ids or set()) if str(x).strip()}
    auths = {str(x).strip() for x in (auth_numbers or set()) if str(x).strip()}
    if not pids and not auths:
        return []

    started = time.perf_counter()
    qdrant = get_qdrant_client(settings)
    collection = settings.qdrant_sag_collection

    merged: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(hit: Dict[str, Any]) -> None:
        hid = str(hit.get("id") or "")
        if hid and hid in seen_ids:
            return
        if hid:
            seen_ids.add(hid)
        merged.append(hit)

    if pids:
        pid_filters = [
            qm.FieldCondition(key="producto_id", match=qm.MatchValue(value=pid))
            for pid in sorted(pids)[:500]
        ]
        points = scroll_points_by_filter(
            client=qdrant,
            collection=collection,
            query_filter=qm.Filter(should=pid_filters),
            limit_per_page=256,
            max_points=max_rows,
        )
        for p in points:
            _add(p)

    if auths:
        auth_filters = [
            qm.FieldCondition(
                key="autorizacion_sag_numero_normalizado",
                match=qm.MatchValue(value=auth),
            )
            for auth in sorted(auths)[:500]
        ]
        points = scroll_points_by_filter(
            client=qdrant,
            collection=collection,
            query_filter=qm.Filter(should=auth_filters),
            limit_per_page=256,
            max_points=max_rows,
        )
        for p in points:
            _add(p)

    logger.info(
        "📚 Qdrant SAG (ids) | product_ids=%s | auths=%s | rows=%s | tiempo=%sms",
        len(pids),
        len(auths),
        len(merged),
        int((time.perf_counter() - started) * 1000),
    )
    return merged


def retrieve_sag_all_rows(
    settings: Settings,
    max_rows: int = 25000,
) -> List[Dict[str, Any]]:
    """
    Recupera filas SAG mediante scroll global (sin búsqueda vectorial),
    útil cuando se necesita recall alto por criterio estructurado
    (ingrediente/cultivo/objetivo).
    """
    started = time.perf_counter()
    qdrant = get_qdrant_client(settings)
    rows = scroll_points_by_filter(
        client=qdrant,
        collection=settings.qdrant_sag_collection,
        query_filter=qm.Filter(),
        limit_per_page=256,
        max_points=max_rows,
    )
    logger.info(
        "📚 Qdrant SAG (scroll global) | colección=%s | filas=%s | tiempo=%sms",
        settings.qdrant_sag_collection,
        len(rows),
        int((time.perf_counter() - started) * 1000),
    )
    return rows

