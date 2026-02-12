"""
Pipeline RAG interactivo: escribe tu pregunta y ve el proceso completo.

Ejecutar:
    python tests/test_pipeline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Agregar raíz del proyecto al PYTHONPATH
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from typing import Any, Dict, List

from src.oraculo.config import get_settings
from src.oraculo.providers.query_refiner import refine_user_question
from src.oraculo.rag.doc_context import build_doc_contexts_from_hits
from src.oraculo.rag.prompting import build_answer_prompt_from_doc_contexts
from src.oraculo.rag.retriever import retrieve
from src.oraculo.providers.llm import generate_answer
from src.oraculo.sources.resolver import format_sources_from_hits


# ═══════════════════════════════════════════════════════════════════════════
# Utilidades de impresión
# ═══════════════════════════════════════════════════════════════════════════

SEP = "=" * 80
SUBSEP = "-" * 70


def _header(title: str) -> None:
    print(f"\n{SEP}\n  {title}\n{SEP}")


def _subheader(title: str) -> None:
    print(f"\n{SUBSEP}\n  {title}\n{SUBSEP}")


def _print_hits(hits: List[Dict[str, Any]], max_text: int = 150) -> None:
    print(f"\n  Total: {len(hits)} documentos recuperados\n")
    for i, h in enumerate(hits, 1):
        score = h.get("score", 0.0)
        p = h.get("payload", {})
        doc_id = p.get('doc_id', '?')
        especie = p.get('especie', '')
        producto = p.get('producto', '')
        variedad = p.get('variedad', '')
        text = p.get('text', '')[:max_text].replace('\n', ' ')
        
        print(f"  [{i:>2}] Score: {score:.4f}")
        print(f"       Doc ID: {doc_id}")
        print(f"       Especie: {especie}  |  Producto: {producto}  |  Variedad: {variedad}")
        print(f"       Snippet: {text}...")
        print()


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline interactivo
# ═══════════════════════════════════════════════════════════════════════════

def run_interactive_pipeline():
    """
    Pipeline interactivo que solicita la pregunta al usuario y muestra:
      1. Pregunta original
      2. Consulta optimizada (refinada)
      3. Información del RAG (hits recuperados)
      4. Respuesta final redactada por el LLM
    """
    settings = get_settings()
    
    print("\n" + "█" * 80)
    print("  SISTEMA RAG INTERACTIVO - ORÁCULO AGRÓNOMO")
    print("█" * 80)
    print("\n  Escribe tu pregunta sobre ensayos agronómicos.")
    print("  (Ejemplos: productos para arañita roja en cerezo, para qué sirve Kelpak, etc.)")
    print()
    
    # Solicitar pregunta al usuario
    question = input("  🔍 Tu pregunta: ").strip()
    
    if not question:
        print("\n  ⚠️  No se ingresó ninguna pregunta. Saliendo...")
        return
    
    # ═══════════════════════════════════════════════════════════════════════
    # PASO 1: Mostrar pregunta original
    # ═══════════════════════════════════════════════════════════════════════
    _header("1️⃣  PREGUNTA ORIGINAL")
    print(f"\n  {question}\n")
    
    # ═══════════════════════════════════════════════════════════════════════
    # PASO 2: Optimizar consulta con Gemini
    # ═══════════════════════════════════════════════════════════════════════
    _header("2️⃣  CONSULTA OPTIMIZADA PARA BÚSQUEDA")
    print("\n  ⏳ Optimizando consulta con Gemini...\n")
    
    rewritten_query = refine_user_question(question, settings)
    
    print(f"  {rewritten_query}\n")
    
    # ═══════════════════════════════════════════════════════════════════════
    # PASO 3: Recuperar información del RAG
    # ═══════════════════════════════════════════════════════════════════════
    _header("3️⃣  INFORMACIÓN RECUPERADA DEL RAG")
    print("\n  ⏳ Buscando documentos relevantes en Qdrant...\n")
    
    rewritten2, hits = retrieve(question, settings, top_k=8)
    
    _print_hits(hits)
    
    # ═══════════════════════════════════════════════════════════════════════
    # PASO 4: Construir contexto y generar respuesta con LLM
    # ═══════════════════════════════════════════════════════════════════════
    _header("4️⃣  RESPUESTA REDACTADA CON LA INFORMACIÓN")
    print("\n  ⏳ Generando respuesta con Gemini...\n")
    
    # Construir contexto por documento
    doc_contexts = build_doc_contexts_from_hits(hits, settings)
    
    # Construir prompt
    prompt = build_answer_prompt_from_doc_contexts(
        question=question,
        refined_question=rewritten_query,
        doc_contexts=doc_contexts,
    )
    
    # Generar respuesta con LLM
    llm_output = generate_answer(prompt, settings, system_instruction="")
    
    # Agregar fuentes
    sources_block = format_sources_from_hits(hits)
    
    final_answer = llm_output.rstrip()
    if sources_block:
        final_answer = final_answer + "\n\n" + sources_block
    
    print(f"{final_answer}\n")
    
    # ═══════════════════════════════════════════════════════════════════════
    # Resumen final
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{SUBSEP}")
    print(f"  ✅ Respuesta generada exitosamente")
    print(f"  📊 Documentos consultados: {len(hits)}")
    print(f"  📄 Informes procesados: {len(doc_contexts)}")
    print(f"  📝 Caracteres en respuesta: {len(final_answer)}")
    print(f"{SUBSEP}\n")


# ═══════════════════════════════════════════════════════════════════════════
# Ejecución directa
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        run_interactive_pipeline()
    except KeyboardInterrupt:
        print("\n\n  ⚠️  Interrumpido por el usuario. Saliendo...")
    except Exception as e:
        print(f"\n\n  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
