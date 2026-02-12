from __future__ import annotations

import time

from oraculo.config import get_settings
from oraculo.providers.query_refiner import refine_user_question


TEST_QUESTIONS = [
    "Que productos recomiendas para arañita roja en cerezo Regina?",
    "Resultados de Kelpak en uva Red Globe temporada 2023-2024",
    "Dosis y momento de aplicacion de cobre para oidio en vid",
    "Comparar Surround versus caolin en cerezo para golpe de sol",
    "No encuentro informacion de Black Kat en ciruelo, que alternativa hay?",
]


def main() -> int:
    settings = get_settings()

    print("=" * 100)
    print("TEST QUERY ENHANCER")
    print("=" * 100)
    print(f"Modelo refine: {settings.gemini_refine_model}")

    for i, question in enumerate(TEST_QUESTIONS, start=1):
        print("\n" + "-" * 100)
        print(f"Caso {i}")
        print("-" * 100)
        print("Pregunta:")
        print(question)
        print("\n[INFO] Enviando pregunta al modelo refine...")

        started = time.time()
        try:
            rewritten = refine_user_question(question, settings)
        except Exception as exc:
            elapsed = time.time() - started
            print(f"[ERROR] Fallo en caso {i} ({elapsed:.2f}s): {type(exc).__name__}: {exc}")
            continue
        elapsed = time.time() - started

        print("\nQuery refinada:")
        print(rewritten)
        print(f"\nTiempo: {elapsed:.2f}s")
        print(f"\nLargo query refinada: {len(rewritten.split())} palabras")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
