from __future__ import annotations

import argparse
from typing import Any, Dict, List

from ..config import get_settings
from ..providers.llm import generate_answer
from ..sources.resolver import format_sources_from_hits
from .doc_context import build_doc_contexts_from_hits
from .prompting import build_answer_prompt_from_doc_contexts
from .retriever import retrieve, retrieve_sag


def _print_hits(title: str, hits: List[Dict[str, Any]], max_items: int = 10) -> None:
    print("=" * 90)
    print(title)
    print("=" * 90)
    print(f"Total: {len(hits)}")
    for i, hit in enumerate(hits[:max_items], start=1):
        payload = hit.get("payload") or {}
        score = float(hit.get("score") or 0.0)
        print(f"\n[{i}] score={score:.4f}")
        print(f"  doc_id: {payload.get('doc_id', '')}")
        print(f"  producto: {payload.get('producto', '')}")
        print(f"  especie: {payload.get('especie', '')}")
        print(f"  cultivo: {payload.get('cultivo', '')}")
        print(f"  objetivo: {payload.get('objetivo', '')}")
        print(f"  nombre_comercial: {payload.get('nombre_comercial', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Responder pregunta usando pipeline RAG"
    )
    parser.add_argument("question", type=str, help="Pregunta del usuario")
    parser.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="Cantidad de chunks iniciales para recall",
    )
    parser.add_argument(
        "--show-prompt",
        action="store_true",
        help="Imprime el prompt final enviado al LLM",
    )
    parser.add_argument(
        "--show-retrieval",
        action="store_true",
        help="Imprime hits recuperados de CER y SAG",
    )
    parser.add_argument(
        "--max-hits-print",
        type=int,
        default=10,
        help="Cantidad máxima de hits a imprimir por colección",
    )
    args = parser.parse_args()

    settings = get_settings()
    rewritten_query, hits = retrieve(args.question, settings, top_k=args.top_k)
    doc_contexts = build_doc_contexts_from_hits(hits, settings)
    sag_hits = retrieve_sag(
        rewritten_query,
        settings,
        top_k=max(1, int(settings.rag_sag_top_k)),
    )

    prompt = build_answer_prompt_from_doc_contexts(
        question=args.question,
        refined_question=rewritten_query,
        doc_contexts=doc_contexts,
        sag_hits=sag_hits,
    )
    if args.show_retrieval:
        _print_hits("HITS CER (cer_chunks)", hits, max_items=max(1, args.max_hits_print))
        _print_hits("HITS SAG", sag_hits, max_items=max(1, args.max_hits_print))

    if args.show_prompt:
        print("\n" + "=" * 90)
        print("PROMPT FINAL")
        print("=" * 90)
        print(prompt)

    answer = generate_answer(prompt, settings=settings, system_instruction="")
    sources_block = format_sources_from_hits(hits)
    out = answer.rstrip()
    if sources_block:
        out = out + "\n\n" + sources_block

    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
