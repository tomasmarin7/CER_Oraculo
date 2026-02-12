from __future__ import annotations

import argparse

from qdrant_client import models as qm

from ..config import get_settings
from .qdrant_client import get_qdrant_client


DEFAULT_FIELDS = [
    "producto",
    "especie",
    "variedad",
    "cliente",
    "temporada",
    "doc_id",
    "pdf_filename",
]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Crea payload indexes (keyword) en Qdrant para permitir filtros/scroll."
    )
    ap.add_argument(
        "--fields",
        nargs="*",
        default=DEFAULT_FIELDS,
        help="Lista de campos payload a indexar (keyword).",
    )
    args = ap.parse_args()

    settings = get_settings()
    client = get_qdrant_client(settings)

    print(f"collection: {settings.qdrant_collection}")
    ok = True

    for field in args.fields:
        try:
            client.create_payload_index(
                collection_name=settings.qdrant_collection,
                field_name=field,
                field_schema=qm.PayloadSchemaType.KEYWORD,
                wait=True,
            )
            print(f"OK: index creado -> {field} (keyword)")
        except Exception as e:
            # Si ya existe u otro warning, no es crítico.
            print(f"WARN: no se pudo crear index para '{field}': {e}")
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
