from __future__ import annotations

import argparse

from oraculo.config import get_settings
from oraculo.providers.llm import generate_answer
from oraculo.rag.doc_context import build_doc_contexts_from_hits
from oraculo.rag.prompting import build_answer_prompt_from_doc_contexts
from oraculo.rag.retriever import retrieve
from oraculo.sources.resolver import format_sources_from_hits


SEP = "=" * 100
SUB = "-" * 100


def _print_hits(hits: list[dict], max_text: int = 180) -> None:
    print(f"\n{SUB}")
    print("HITS RECUPERADOS")
    print(SUB)
    print(f"Total hits: {len(hits)}")
    for i, hit in enumerate(hits, start=1):
        payload = hit.get("payload") or {}
        text = str(payload.get("text") or "").replace("\n", " ")
        text = text[:max_text] + ("..." if len(text) > max_text else "")

        print(f"\n[{i}] score={float(hit.get('score', 0.0)):.4f}")
        print(f"doc_id: {payload.get('doc_id', '')}")
        print(f"producto: {payload.get('producto', '')}")
        print(f"especie: {payload.get('especie', '')}")
        print(f"variedad: {payload.get('variedad', '')}")
        print(f"temporada: {payload.get('temporada', '')}")
        if text:
            print(f"snippet: {text}")


def _print_doc_contexts(doc_contexts: list) -> None:
    print(f"\n{SUB}")
    print("DOC CONTEXTS (DESPUES DE EXPANSION POR DOC_ID)")
    print(SUB)
    print(f"Total doc_contexts: {len(doc_contexts)}")
    for i, doc in enumerate(doc_contexts, start=1):
        print(f"\n[{i}] doc_id={doc.doc_id}")
        print(f"temporada: {doc.temporada}")
        print(f"producto/especie/variedad: {doc.producto} | {doc.especie} | {doc.variedad}")
        print("ubicacion:")
        print(f"  comuna: {doc.comuna or 'no especificado'}")
        print(f"  localidad: {doc.localidad or 'no especificado'}")
        print(f"  region: {doc.region or 'no especificado'}")
        print(f"  ubicacion: {doc.ubicacion or 'no especificado'}")
        print(f"chunks seleccionados: {len(doc.chunks)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Debug completo del pipeline RAG: refine, hits, contextos, prompt y respuesta"
    )
    parser.add_argument("question", type=str, help="Pregunta del usuario")
    parser.add_argument("--top-k", type=int, default=8, help="Cantidad de documentos")
    parser.add_argument(
        "--show-prompt",
        action="store_true",
        help="Imprime el prompt completo antes de la respuesta",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="Override temporal de GEMINI_MAX_OUTPUT_TOKENS para este debug",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=None,
        help="Override temporal de GEMINI_TIMEOUT_MS para este debug",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.max_output_tokens is not None:
        settings = settings.model_copy(
            update={"gemini_max_output_tokens": int(args.max_output_tokens)}
        )
    if args.timeout_ms is not None:
        settings = settings.model_copy(update={"gemini_timeout_ms": int(args.timeout_ms)})

    print(SEP)
    print("DEBUG FULL PIPELINE")
    print(SEP)
    print(f"Pregunta original: {args.question}")
    print(f"top_k: {args.top_k}")
    print(f"Modelo refine: {settings.gemini_refine_model}")
    print(f"Modelo respuesta: {settings.gemini_model}")
    print(f"Modelo fallback respuesta: {settings.gemini_fallback_model}")
    print(f"Timeout respuesta (ms): {settings.gemini_timeout_ms}")
    print(f"Max output tokens: {settings.gemini_max_output_tokens}")

    rewritten_query, hits = retrieve(args.question, settings, top_k=args.top_k)
    print(f"\nPregunta refinada:\n{rewritten_query}")

    _print_hits(hits)

    doc_contexts = build_doc_contexts_from_hits(hits, settings, top_docs=args.top_k)
    _print_doc_contexts(doc_contexts)

    prompt = build_answer_prompt_from_doc_contexts(
        question=args.question,
        refined_question=rewritten_query,
        doc_contexts=doc_contexts,
    )

    if args.show_prompt:
        print(f"\n{SUB}")
        print("PROMPT COMPLETO")
        print(SUB)
        print(prompt)

    print(f"\n{SUB}")
    print("RESPUESTA FINAL LLM")
    print(SUB)
    try:
        answer = generate_answer(prompt, settings, system_instruction="")
        print(answer)
    except Exception as exc:
        print(f"[ERROR] No se pudo generar respuesta: {type(exc).__name__}: {exc}")
        return 1

    sources_block = format_sources_from_hits(hits)
    if sources_block:
        print(f"\n{SUB}")
        print("FUENTES (AUTO)")
        print(SUB)
        print(sources_block)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
