from __future__ import annotations

import sys
from typing import Optional, Tuple

from ..config import get_settings
from .qdrant_client import get_qdrant_client


EXPECTED_VECTOR_SIZE = 768  # tu base está en 768


def _extract_vector_size(collection_info) -> Tuple[int, Optional[str]]:
    """
    Devuelve (size, vector_name).
    - size: tamaño del vector
    - vector_name: nombre si la colección usa vectores nombrados (o None si es single vector)
    """
    vectors = collection_info.config.params.vectors

    # Caso 1: vector único (VectorParams)
    if hasattr(vectors, "size"):
        return int(vectors.size), None

    # Caso 2: vectores nombrados (dict[str, VectorParams])
    if isinstance(vectors, dict):
        if "default" in vectors and hasattr(vectors["default"], "size"):
            return int(vectors["default"].size), "default"

        name, params = next(iter(vectors.items()))
        if hasattr(params, "size"):
            return int(params.size), str(name)

    raise TypeError(f"No pude interpretar la configuración de vectores: {type(vectors)}")


def main() -> int:
    settings = get_settings()
    client = get_qdrant_client(settings)

    try:
        info = client.get_collection(settings.qdrant_collection)
    except Exception as e:
        print("ERROR: no pude acceder a la colección en Qdrant.")
        print(f"  collection: {settings.qdrant_collection}")
        print(f"  detalle: {e}")
        return 1

    try:
        size, vname = _extract_vector_size(info)
    except Exception as e:
        print("ERROR: pude acceder a la colección, pero no pude leer el vector_size.")
        print(f"  detalle: {e}")
        return 1

    ok_dim = (size == EXPECTED_VECTOR_SIZE)

    print("OK: conexión a Qdrant Cloud")
    print(f"  collection: {settings.qdrant_collection}")
    print(f"  vector_size: {size}" + (f" (vector='{vname}')" if vname else ""))
    print(f"  expected: {EXPECTED_VECTOR_SIZE}")
    print(f"  status: {'OK' if ok_dim else 'MISMATCH'}")

    return 0 if ok_dim else 2


if __name__ == "__main__":
    raise SystemExit(main())
