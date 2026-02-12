from __future__ import annotations

from google import genai
from google.genai import types

from oraculo.config import get_settings


def try_generate(client: genai.Client, model_name: str, prompt: str) -> None:
    print(f"\n=== Probando modelo: {model_name} ===")
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0),
        )
        text = (resp.text or "").strip()
        print("OK")
        print("Respuesta:", text[:300] if text else "(vacia)")
    except Exception as exc:
        print("ERROR")
        print(type(exc).__name__, str(exc))


def main() -> int:
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())

    configured_models = [
        ("GEMINI_MODEL", settings.gemini_model),
        ("GEMINI_REFINE_MODEL", settings.gemini_refine_model),
    ]

    for label, model_name in configured_models:
        print(f"\n{label}={model_name}")
        try_generate(
            client=client,
            model_name=model_name,
            prompt="Responde solo: OK",
        )

    print("\n=== Listando modelos disponibles (si aplica) ===")
    try:
        models = client.models.list()
        count = 0
        for model in models:
            name = getattr(model, "name", None)
            if name:
                print("-", name)
                count += 1
                if count >= 30:
                    print("... (mostrando primeros 30)")
                    break
    except Exception as exc:
        print("No se pudo listar modelos:", type(exc).__name__, str(exc))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
