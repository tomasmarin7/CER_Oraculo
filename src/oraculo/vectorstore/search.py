from __future__ import annotations

from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client import models as qm
from qdrant_client.http.exceptions import UnexpectedResponse


def query_top_chunks(
    client: QdrantClient,
    collection: str,
    query_vector: List[float],
    top_k: int = 8,
    score_threshold: Optional[float] = None,
    query_filter: Optional[qm.Filter] = None,
    payload_fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    with_payload: Any = True if payload_fields is None else payload_fields

    resp = client.query_points(
        collection_name=collection,
        query=query_vector,
        limit=top_k,
        query_filter=query_filter,
        score_threshold=score_threshold,
        with_payload=with_payload,
        with_vectors=False,
    )

    results: List[Dict[str, Any]] = []
    for p in resp.points:
        results.append(
            {
                "id": p.id,
                "score": p.score,
                "payload": p.payload or {},
            }
        )
    return results


def scroll_doc_points(
    client: QdrantClient,
    collection: str,
    doc_id: str,
    limit_per_page: int = 128,
    max_points: int = 2000,
    payload_fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Trae puntos de un documento (doc_id) usando scroll.
    Requiere índice keyword para 'doc_id' en Qdrant Cloud.
    """
    with_payload: Any = True if payload_fields is None else payload_fields

    flt = qm.Filter(
        must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]
    )

    all_points: List[Dict[str, Any]] = []
    next_offset = None

    while True:
        try:
            points, next_offset = client.scroll(
                collection_name=collection,
                scroll_filter=flt,
                limit=limit_per_page,
                offset=next_offset,
                with_payload=with_payload,
                with_vectors=False,
            )
        except UnexpectedResponse:
            raise

        for p in points:
            all_points.append({"id": p.id, "payload": p.payload or {}})

        if len(all_points) >= max_points:
            break
        if next_offset is None or len(points) == 0:
            break

    return all_points
