from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from .doc_context import DocContext


ANSWER_PROMPT_FILE = "answer.md"


def _load_prompt_template(filename: str) -> str:
    base_dir = Path(__file__).resolve().parents[1]  # .../oraculo
    prompt_path = base_dir / "prompts" / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _render_template(template: str, mapping: Dict[str, str]) -> str:
    out = template
    for key, value in mapping.items():
        out = out.replace(f"{{{{{key}}}}}", value)
    return out


def _doc_header(dc: DocContext) -> str:
    return (
        f"pdf_filename: {dc.pdf_filename}\n"
        f"doc_id: {dc.doc_id}\n"
        f"temporada: {dc.temporada} | cliente: {dc.cliente} | producto: {dc.producto}\n"
        f"temporada_max_year: {_season_max_year(dc.temporada)}\n"
        f"especie: {dc.especie} | variedad: {dc.variedad}\n"
        f"comuna: {dc.comuna}\n"
        f"localidad: {dc.localidad}\n"
        f"region: {dc.region}\n"
        f"ubicacion: {dc.ubicacion}\n"
    )


def _build_context_block(doc_contexts: List[DocContext]) -> str:
    """Transforma contextos de documentos a un bloque de texto para el prompt."""
    parts: list[str] = []

    for i, dc in enumerate(doc_contexts, start=1):
        parts.append(f"=== INFORME {i} (doc_id={dc.doc_id}) ===\n")
        parts.append(_doc_header(dc))
        for ch in dc.chunks:
            ci = ch.get("chunk_index")
            ctype = ch.get("chunk_type") or "text"
            sec = ch.get("section_norm") or ""
            page = ch.get("page_number")

            header = f"[chunk {ci} | section {sec} | type {ctype}]"
            if page is not None:
                header = f"[chunk {ci} | page {page} | section {sec} | type {ctype}]"

            parts.append(header + "\n")
            parts.append(ch.get("text", "").strip() + "\n\n")

    return "".join(parts).strip()


def _build_sag_context_block(sag_hits: List[Dict[str, Any]]) -> str:
    """
    Transforma hits SAG en bloque estructurado para redacción por LLM.
    """
    if not sag_hits:
        return "SIN_COINCIDENCIAS_SAG"

    parts: list[str] = []
    for i, hit in enumerate(sag_hits, start=1):
        payload = hit.get("payload") or {}
        score = float(hit.get("score") or 0.0)
        parts.append(f"=== PRODUCTO SAG {i} ===")
        parts.append(f"score: {score:.4f}")
        parts.append(f"nombre_comercial: {payload.get('nombre_comercial', '')}")
        parts.append(
            f"producto_nombre_comercial: {payload.get('producto_nombre_comercial', '')}"
        )
        parts.append(f"producto_id: {payload.get('producto_id', '')}")
        parts.append(f"tipo_producto: {payload.get('tipo_producto', '')}")
        parts.append(f"formulacion: {payload.get('formulacion', '')}")
        parts.append(
            f"autorizacion_sag_numero: {payload.get('autorizacion_sag_numero_normalizado', '')}"
        )
        parts.append(
            f"importador_distribuidor: {payload.get('importador_distribuidor', '')}"
        )
        parts.append(f"cultivo: {payload.get('cultivo', '')}")
        parts.append(f"objetivo: {payload.get('objetivo', '')}")
        parts.append(f"categoria_objetivo: {payload.get('categoria_objetivo', '')}")
        parts.append(f"dosis_texto: {payload.get('dosis_texto', '')}")
        parts.append(f"carencia_dias: {payload.get('carencia_dias', '')}")
        parts.append(
            "max_aplicaciones_por_temporada: "
            f"{payload.get('max_aplicaciones_por_temporada', '')}"
        )
        parts.append("")
    return "\n".join(parts).strip()


def _season_max_year(season_text: str) -> str:
    matches = re.findall(r"\b(?:19|20)\d{2}\b", season_text or "")
    if not matches:
        return ""
    return max(matches)


def build_answer_prompt_from_doc_contexts(
    question: str,
    refined_question: str,
    doc_contexts: List[DocContext],
    sag_hits: List[Dict[str, Any]] | None = None,
) -> str:
    """Arma prompt final reemplazando placeholders del template."""
    template = _load_prompt_template(ANSWER_PROMPT_FILE)

    rq = (refined_question or "").strip() or question.strip()
    context_block = _build_context_block(doc_contexts)
    sag_context_block = _build_sag_context_block(sag_hits or [])

    return _render_template(
        template,
        {
            "question": question.strip(),
            "refined_question": rq,
            "context": context_block,
            "sag_context": sag_context_block,
        },
    ).strip()
