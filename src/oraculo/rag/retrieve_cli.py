from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from ..config import get_settings
from .retriever import retrieve

TEXT_KEYS_PRIORITY = ["text", "chunk", "content", "page_content"]


def _best_text_from_payload(payload: Dict[str, Any]) -> str:
    for key in TEXT_KEYS_PRIORITY:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value

    # Fallback simple: primer string "largo" que parezca contenido.
    for value in payload.values():
        if isinstance(value, str) and len(value) > 40:
            return value

    return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba retrieval (refine -> embedding -> busqueda vectorial)"
    )
    parser.add_argument("question", help="Pregunta del usuario")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    settings = get_settings()
    rewritten_query, hits = retrieve(args.question, settings, top_k=args.top_k)

    print(f"original:  {args.question}")
    print(f"rewritten: {rewritten_query}")
    print(f"hits: {len(hits)}\n")

    for index, hit in enumerate(hits, start=1):
        payload = hit.get("payload") or {}
        snippet = _best_text_from_payload(payload)
        snippet = (snippet[:300] + "...") if len(snippet) > 300 else snippet

        score = float(hit.get("score", 0.0))
        point_id = hit.get("id")

        print(f"{index}. id={point_id} score={score:.4f}")
        if snippet:
            print(f"   snippet: {snippet}")
        else:
            keys = list(payload.keys())
            print(f"   payload_keys: {keys[:20]}{'...' if len(keys) > 20 else ''}")
            preview = json.dumps(payload, ensure_ascii=False) if payload else "{}"
            preview = (preview[:300] + "...") if len(preview) > 300 else preview
            print(f"   payload_preview: {preview}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
