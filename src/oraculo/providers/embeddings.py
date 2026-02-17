from __future__ import annotations

import logging
import math
import time
from typing import List

from google import genai
from google.genai import types

from ..config import Settings


EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768  # tu Qdrant está en 768
logger = logging.getLogger(__name__)


def _l2_normalize(vec: List[float]) -> List[float]:
    # Para 768/1536 Google recomienda normalizar (3072 ya viene normalizado).
    # https://ai.google.dev/gemini-api/docs/embeddings
    norm = math.sqrt(sum((x * x) for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def embed_retrieval_query(text: str, settings: Settings) -> List[float]:
    """
    Embedding para la pregunta del usuario.
    task_type debe ser RETRIEVAL_QUERY para RAG.
    """
    started = time.perf_counter()
    logger.info("🧮 Embedding | generando vector de búsqueda...")
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())

    result = client.models.embed_content(
        model=EMBED_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=EMBED_DIM,
        ),
    )

    [emb] = result.embeddings
    vec = [float(x) for x in emb.values]
    logger.info(
        "✅ Embedding listo | tiempo=%sms | entrada=%s chars | dimensión=%s",
        int((time.perf_counter() - started) * 1000),
        len(text or ""),
        len(vec),
    )
    return _l2_normalize(vec)
