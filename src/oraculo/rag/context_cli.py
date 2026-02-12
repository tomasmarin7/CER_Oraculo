from __future__ import annotations

import argparse

from ..config import get_settings
from .doc_context import build_doc_contexts_from_hits
from .prompting import build_answer_prompt_from_doc_contexts
from .retriever import retrieve


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Imprime el prompt completo que se envia al LLM"
    )
    parser.add_argument("question", help="Pregunta del usuario")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    settings = get_settings()
    rewritten_query, hits = retrieve(args.question, settings, top_k=args.top_k)
    doc_contexts = build_doc_contexts_from_hits(hits, settings)

    prompt = build_answer_prompt_from_doc_contexts(
        question=args.question,
        refined_question=rewritten_query,
        doc_contexts=doc_contexts,
    )
    print(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
