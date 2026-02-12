from __future__ import annotations

from qdrant_client import QdrantClient

from ..config import Settings


def get_qdrant_client(settings: Settings) -> QdrantClient:
    return QdrantClient(
        url=str(settings.qdrant_url),
        api_key=settings.qdrant_api_key.get_secret_value(),
        # Preferimos estabilidad (REST). Más adelante se puede habilitar gRPC.
        prefer_grpc=False,
        timeout=30,
    )
