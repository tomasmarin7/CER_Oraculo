from __future__ import annotations

import argparse
import time
import traceback
from typing import Any, Dict, List

from oraculo.config import get_settings
from oraculo.providers.embeddings import embed_retrieval_query
from oraculo.providers.query_refiner import refine_user_question
from oraculo.rag.retriever import retrieve_sag
from oraculo.vectorstore.search import query_top_chunks
from oraculo.vectorstore.qdrant_client import get_qdrant_client


SEP = "=" * 100
SUB = "-" * 100


def _adapt_query_vector_dim(query_vector: List[float], target_dim: int) -> List[float]:
    if len(query_vector) == target_dim:
        return query_vector
    if len(query_vector) < target_dim:
        return list(query_vector) + [0.0] * (target_dim - len(query_vector))
    return list(query_vector[:target_dim])


def _show_hit(hit: Dict[str, Any], idx: int) -> None:
    payload = hit.get("payload") or {}
    score = float(hit.get("score") or 0.0)
    print(f"[{idx}] score={score:.4f}")
    print(f"    nombre_comercial: {payload.get('nombre_comercial', '')}")
    print(f"    tipo_producto: {payload.get('tipo_producto', '')}")
    print(f"    cultivo: {payload.get('cultivo', '')}")
    print(f"    objetivo: {payload.get('objetivo', '')}")
    print(f"    dosis_texto: {payload.get('dosis_texto', '')}")
    print(
        "    autorizacion_sag_numero: "
        f"{payload.get('autorizacion_sag_numero_normalizado', '')}"
    )
    print(f"    source_type: {payload.get('source_type', '')}")


def _show_hits(title: str, hits: List[Dict[str, Any]], max_items: int) -> None:
    print(f"\n{SUB}")
    print(title)
    print(SUB)
    print(f"Total hits: {len(hits)}")
    for i, hit in enumerate(hits[:max_items], start=1):
        _show_hit(hit, i)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnóstico de retrieval exclusivo para colección SAG"
    )
    parser.add_argument("question", type=str, help="Pregunta del usuario")
    parser.add_argument("--top-k", type=int, default=8, help="Top K final deduplicado")
    parser.add_argument(
        "--candidate-k",
        type=int,
        default=24,
        help="Top K crudo para inspección directa en Qdrant",
    )
    parser.add_argument(
        "--max-print",
        type=int,
        default=10,
        help="Máximo de hits a imprimir",
    )
    parser.add_argument(
        "--no-refine",
        action="store_true",
        help="Desactiva reescritura de consulta (usa pregunta original)",
    )
    args = parser.parse_args()

    settings = get_settings()

    print(SEP)
    print("TEST RAG SAG")
    print(SEP)
    print(f"Colección SAG configurada: {settings.qdrant_sag_collection}")
    print(f"Dimensión CER configurada: {settings.qdrant_cer_chunks_vector_dim}")
    print(f"Dimensión SAG configurada: {settings.qdrant_sag_vector_dim}")
    print(f"Pregunta original: {args.question}")

    # 1) Refinar (opcional)
    if args.no_refine:
        query_for_sag = args.question.strip()
        print("Modo refine: DESACTIVADO")
    else:
        print("Modo refine: ACTIVADO")
        t0 = time.time()
        query_for_sag = refine_user_question(args.question, settings)
        print(f"Tiempo refine: {time.time() - t0:.2f}s")

    print(f"Query usada para SAG:\n{query_for_sag}")

    # 2) Inspección cruda en Qdrant (sin deduplicación)
    qdrant = get_qdrant_client(settings)
    t1 = time.time()
    vector = embed_retrieval_query(query_for_sag, settings)
    print(f"Tiempo embedding: {time.time() - t1:.2f}s")
    print(f"Dimensión embedding original: {len(vector)}")

    sag_dim = int(settings.qdrant_sag_vector_dim)
    print(f"Dimensión objetivo SAG: {sag_dim}")
    vector = _adapt_query_vector_dim(vector, sag_dim)
    print(f"Dimensión embedding usada en query: {len(vector)}")

    t2 = time.time()
    raw_hits = query_top_chunks(
        client=qdrant,
        collection=settings.qdrant_sag_collection,
        query_vector=vector,
        top_k=max(1, int(args.candidate_k)),
    )
    print(f"Tiempo query Qdrant (raw): {time.time() - t2:.2f}s")
    _show_hits("HITS CRUDOS SAG (Qdrant)", raw_hits, max_items=max(1, args.max_print))

    # 3) Resultado final del retriever SAG (con deduplicación)
    t3 = time.time()
    sag_hits = retrieve_sag(query_for_sag, settings, top_k=max(1, int(args.top_k)))
    print(f"\nTiempo retrieve_sag (deduplicado): {time.time() - t3:.2f}s")
    _show_hits(
        "HITS DEDUPLICADOS SAG (retrieve_sag)",
        sag_hits,
        max_items=max(1, args.max_print),
    )

    if not raw_hits:
        print(
            "\n[ALERTA] Qdrant devolvió 0 hits crudos en SAG. "
            "Revisa nombre de colección, vectores y carga de datos."
        )
    elif raw_hits and not sag_hits:
        print(
            "\n[ALERTA] Hay hits crudos pero 0 deduplicados. "
            "Revisa lógica de deduplicación SAG."
        )
    else:
        print("\n[OK] Retrieval SAG devolvió resultados.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise
