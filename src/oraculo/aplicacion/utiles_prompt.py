from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def cargar_plantilla_prompt(carpeta_base: Path, nombre_archivo: str) -> str:
    ruta_prompt = carpeta_base / nombre_archivo
    if not ruta_prompt.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {ruta_prompt}")
    return ruta_prompt.read_text(encoding="utf-8").strip()


def parsear_json_modelo(texto_crudo: str) -> dict[str, Any]:
    texto = (texto_crudo or "").strip()
    if not texto:
        return {}
    try:
        data = json.loads(texto)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass

    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio >= 0 and fin > inicio:
        try:
            data = json.loads(texto[inicio : fin + 1])
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
