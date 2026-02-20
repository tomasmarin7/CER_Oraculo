"""Lógica de respuestas CER: búsqueda de ensayos, detalle y follow-up."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from ..config import Settings
from ..followup import (
    build_detail_followup_prompt,
    build_followup_chat_prompt,
    render_report_options,
)
from ..providers.llm import generate_answer
from ..rag.doc_context import DocContext, build_doc_contexts_from_hits
from ..rag.retriever import retrieve
from ..sources.cer_csv_lookup import detect_cer_entities, load_cer_index
from ..sources.resolver import format_sources_from_hits
from .flow_helpers import (
    deserialize_doc_contexts,
    deserialize_seed_hits,
    load_prompt_template,
    normalize_text,
    render_recent_history,
    serialize_doc_contexts,
    token_roots,
)
from .modelos import SesionChat

logger = logging.getLogger(__name__)
LISTAR_ENSAYOS_PROMPT_FILE = "listar_ensayos.md"


# ---------------------------------------------------------------------------
# Construcción de primera respuesta CER (listado de ensayos)
# ---------------------------------------------------------------------------

def build_cer_first_response_from_hits(
    *,
    question: str,
    hits: list[dict[str, Any]],
    settings: Settings,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Genera texto de listado, escenario detectado, y opciones de reporte."""
    offered_reports = _build_report_options_from_hits(hits, settings)
    if not offered_reports:
        text = (
            "No se ha ensayado este caso en el CER.\n"
            "Si quieres, puedo buscar en nuestra base de datos de etiquetas productos que indiquen "
            "este problema en su etiqueta. ¿Lo hago?\n"
            "Si prefieres no revisar la base de datos de etiquetas, dime otra consulta y buscamos en ensayos CER."
        )
        return text, "no_cer", []

    species_hints = detect_cer_entities(settings.cer_csv_path, question).get("especies", set())
    species_hints_norm = {normalize_text(s) for s in species_hints if normalize_text(s)}
    filtered = offered_reports
    if species_hints_norm:
        species_matches = [
            r for r in offered_reports
            if normalize_text(str(r.get("especie") or "")) in species_hints_norm
        ]
        if species_matches:
            filtered = species_matches
        else:
            csv_matches = _build_report_options_from_csv_species(settings, species_hints_norm)
            if csv_matches:
                filtered = csv_matches
                offered_reports = csv_matches

    requested_species = next((str(s).strip() for s in species_hints if str(s).strip()), "")
    first_species = next(
        (str(r.get("especie") or "").strip() for r in filtered if str(r.get("especie") or "").strip()), "",
    )
    problem_text = first_species or (question or "tu consulta").strip()

    lines = [
        (
            f"• {r.get('producto') or 'N/D'} | {r.get('cliente') or 'N/D'} | "
            f"{r.get('temporada') or 'N/D'} | {r.get('especie') or 'N/D'}"
            + (
                f" ({r.get('variedad')})"
                if str(r.get("variedad") or "").strip() and str(r.get("variedad")).strip() != "N/D"
                else ""
            )
        )
        for r in filtered
    ]
    lines_block = "\n\n".join(lines)

    if species_hints_norm and not any(
        normalize_text(str(r.get("especie") or "")) in species_hints_norm for r in offered_reports
    ):
        cross_problem = requested_species or (question or "tu consulta").strip()
        text = (
            f"Para {cross_problem} no tenemos ensayos CER directos, pero sí en otros cultivos:\n"
            + lines_block
            + "\n\n¿Sobre cuáles ensayos quieres que te detalle más?\n"
            "Si ninguno te sirve, puedo buscar en nuestra base de datos de etiquetas productos "
            "que indiquen ese problema en su etiqueta.\n"
            "Si tampoco quieres revisar la base de datos de etiquetas, dime otro problema o cultivo "
            "y hacemos una nueva búsqueda en ensayos CER."
        )
        return text, "cross_crop", filtered

    text = (
        f"Encontré estos ensayos del CER para {problem_text}:\n"
        + lines_block
        + "\n\n¿Sobre cuáles ensayos quieres que te detalle más?\n"
        "Si ninguno te sirve, puedo buscar productos en nuestra base de datos de etiquetas "
        "que indiquen ese problema en su etiqueta.\n"
        "Si tampoco quieres revisar la base de datos de etiquetas, dime otro problema o cultivo "
        "y hacemos una nueva búsqueda en ensayos CER."
    )
    return text, "direct", filtered


# ---------------------------------------------------------------------------
# Detalle de ensayo CER (follow-up)
# ---------------------------------------------------------------------------

def generate_cer_detail_followup_response(
    user_message: str,
    last_question: str,
    last_assistant_message: str,
    offered_reports: list[dict[str, Any]],
    seed_doc_contexts: list[DocContext],
    settings: Settings,
    top_k: int,
    sesion: SesionChat,
    selected_report_hints: list[str],
    selected_report_indexes: list[int],
    progress_callback: Callable[[str], None] | None = None,
) -> str:
    if progress_callback:
        progress_callback("Estoy preparando el detalle del ensayo que elegiste...")

    doc_contexts = list(seed_doc_contexts)
    logger.info("📂 Detalle | contextos iniciales: %s", len(doc_contexts))

    question = f"{last_question}\nSeguimiento usuario: {user_message}".strip()

    if not doc_contexts:
        doc_contexts = _retrieve_doc_contexts_for_detail(
            user_message, offered_reports, selected_report_hints,
            selected_report_indexes, sesion, question, settings, top_k,
        )

    selected = _select_doc_contexts_for_followup(
        user_message, offered_reports, doc_contexts,
        selected_report_hints, selected_report_indexes,
    )
    if selected:
        doc_contexts = selected
    elif selected_report_indexes:
        indexes_txt = ", ".join(str(i) for i in selected_report_indexes)
        return (
            f"Identifiqué que te interesa el/los ensayo(s) {indexes_txt}, "
            "pero no pude mapearlos con precisión al contexto técnico.\n"
            "¿Puedes indicarme el *número exacto* y/o el *producto* del ensayo para citar solo esa fuente?"
        )
    elif _parece_pedir_ensayo_especifico(user_message):
        return (
            "No pude identificar con precisión cuál ensayo quieres detallar.\n"
            "Indícame el número exacto (por ejemplo: *ensayo 1*) o el producto del ensayo."
        )

    logger.info("🎯 Detalle | contextos seleccionados: %s", len(doc_contexts))
    sesion.flow_data["last_detail_doc_contexts"] = serialize_doc_contexts(doc_contexts)

    prompt = build_detail_followup_prompt(
        last_question=last_question,
        last_assistant_message=last_assistant_message,
        user_message=user_message,
        offered_reports=offered_reports,
        context_block=build_context_block(doc_contexts),
    )
    text = generate_answer(
        prompt, settings, system_instruction="", profile="complex", require_complete=True,
    ).rstrip()
    logger.info("📝 Detalle | respuesta técnica redactada.")

    if doc_contexts:
        sources_block = _format_sources_from_doc_contexts(doc_contexts)
        if sources_block:
            text = text + "\n\n" + sources_block
    return text


def _retrieve_doc_contexts_for_detail(
    user_message: str,
    offered_reports: list[dict[str, Any]],
    selected_report_hints: list[str],
    selected_report_indexes: list[int],
    sesion: SesionChat,
    question: str,
    settings: Settings,
    top_k: int,
) -> list[DocContext]:
    """Obtiene DocContexts cuando no hay contextos previos almacenados."""
    selected_doc_ids = _collect_selected_doc_ids(
        user_message, offered_reports, selected_report_hints, selected_report_indexes,
    )
    seed_hits = deserialize_seed_hits(sesion.flow_data.get("last_cer_seed_hits") or [])
    if seed_hits:
        candidate_hits = seed_hits
        if selected_doc_ids:
            candidate_hits = [
                h for h in seed_hits
                if str((h.get("payload") or {}).get("doc_id") or "").strip() in selected_doc_ids
            ]
        if candidate_hits:
            return build_doc_contexts_from_hits(
                candidate_hits, settings,
                top_docs=max(1, min(len(selected_doc_ids) or top_k, int(settings.rag_top_docs))),
            )

    _refined, hits = retrieve(
        question, settings, top_k=top_k,
        conversation_context=render_recent_history(sesion, max_items=10),
    )
    return build_doc_contexts_from_hits(
        hits, settings, top_docs=max(1, min(top_k, int(settings.rag_top_docs))),
    )


def _format_sources_from_doc_contexts(doc_contexts: list[DocContext]) -> str:
    sources_seed = []
    for dc in doc_contexts:
        payload = {
            "pdf_filename": dc.pdf_filename, "temporada": dc.temporada,
            "cliente": dc.cliente, "producto": dc.producto,
            "especie": dc.especie, "variedad": dc.variedad,
        }
        if any(str(v).strip() for v in payload.values()):
            sources_seed.append({"payload": payload})
    return format_sources_from_hits(sources_seed)


# ---------------------------------------------------------------------------
# Follow-up conversacional (sobre contexto ya detallado)
# ---------------------------------------------------------------------------

def generate_conversational_followup_response(
    last_question: str,
    last_assistant_message: str,
    user_message: str,
    offered_reports: list[dict[str, Any]],
    doc_contexts: list[DocContext],
    settings: Settings,
    progress_callback: Callable[[str], None] | None = None,
) -> str:
    logger.info("💬 Follow-up | respondiendo con contexto conversacional.")
    if progress_callback:
        progress_callback("Estoy preparando una respuesta con lo que ya revisamos...")
    prompt = build_followup_chat_prompt(
        last_question=last_question,
        last_assistant_message=last_assistant_message,
        user_message=user_message,
        offered_reports=offered_reports,
        context_block=build_context_block(doc_contexts),
    )
    try:
        text = (
            generate_answer(prompt, settings, system_instruction="", profile="complex", require_complete=True) or ""
        ).strip()
    except Exception:
        text = ""
    if text:
        return text
    return f"Si quieres, te puedo detallar cualquiera de estos informes:\n{render_report_options(offered_reports)}"


# ---------------------------------------------------------------------------
# Selección de DocContexts para follow-up
# ---------------------------------------------------------------------------

def _select_doc_contexts_for_followup(
    user_message: str,
    offered_reports: list[dict[str, Any]],
    doc_contexts: list[DocContext],
    selected_report_hints: list[str] | None = None,
    selected_report_indexes: list[int] | None = None,
) -> list[DocContext]:
    normalized_message = normalize_text(user_message)
    hints = [normalize_text(h) for h in (selected_report_hints or []) if normalize_text(h)]
    combined = " ".join([normalized_message, *hints]).strip()
    if not combined:
        return []

    selected_doc_ids: set[str] = set()

    # Selección por índices del router
    for idx in (selected_report_indexes or []):
        if 1 <= idx <= len(offered_reports):
            selected_doc_ids.update(_doc_ids_from_report(offered_reports[idx - 1]))

    # "ensayo N" explícito
    for match in re.findall(r"\bensayo\s+(\d+)\b", combined):
        idx = int(match)
        if 1 <= idx <= len(offered_reports):
            selected_doc_ids.update(_doc_ids_from_report(offered_reports[idx - 1]))

    # "todos"
    if any(tok in combined for tok in ("todos", "todas", "ambos", "ambas")):
        return _prioritize_for_product_objective(list(doc_contexts))

    # Ordinales
    ordinal_map = {
        1: ("primero", "primera", "1", "uno"),
        2: ("segundo", "segunda", "2", "dos"),
        3: ("tercero", "tercera", "3", "tres"),
        4: ("cuarto", "cuarta", "4", "cuatro"),
        5: ("quinto", "quinta", "5", "cinco"),
    }
    for idx, report in enumerate(offered_reports, start=1):
        terms = ordinal_map.get(idx, ())
        if any(re.search(rf"\b{re.escape(t)}\b", combined) for t in terms):
            selected_doc_ids.update(_doc_ids_from_report(report))

    # Mención de etiqueta o producto
    for report in offered_reports:
        label_norm = normalize_text(str(report.get("label") or ""))
        products_norm = [normalize_text(str(p)) for p in (report.get("products") or []) if str(p).strip()]
        terms = [t for t in [label_norm, *products_norm] if t]
        if terms and any(t in combined for t in terms):
            selected_doc_ids.update(_doc_ids_from_report(report))

    # Match por producto en contextos
    msg_tokens = [t for t in re.findall(r"[a-z0-9áéíóúñ]+", combined) if len(t) >= 4]
    for dc in doc_contexts:
        prod = normalize_text(dc.producto)
        if prod and (prod in combined or any(tok in prod for tok in msg_tokens)):
            if dc.doc_id:
                selected_doc_ids.add(dc.doc_id)

    # Fallback por metadata
    if not selected_doc_ids:
        matched: list[DocContext] = []
        for dc in doc_contexts:
            terms = [normalize_text(dc.especie), normalize_text(dc.producto), normalize_text(dc.variedad)]
            terms = [t for t in terms if t]
            if terms and any(t in combined for t in terms):
                matched.append(dc)
        return _prioritize_for_product_objective(matched)

    filtered = [dc for dc in doc_contexts if dc.doc_id in selected_doc_ids]
    return _prioritize_for_product_objective(filtered) if filtered else []


def _doc_ids_from_report(report: dict[str, Any]) -> list[str]:
    return [str(d).strip() for d in (report.get("doc_ids") or []) if str(d).strip()]


def _collect_selected_doc_ids(
    user_message: str,
    offered_reports: list[dict[str, Any]],
    selected_report_hints: list[str] | None,
    selected_report_indexes: list[int] | None,
) -> set[str]:
    selected: set[str] = set()
    for idx in (selected_report_indexes or []):
        if 1 <= idx <= len(offered_reports):
            selected.update(_doc_ids_from_report(offered_reports[idx - 1]))

    hints_norm = [normalize_text(h) for h in (selected_report_hints or []) if normalize_text(h)]
    message_norm = normalize_text(user_message)
    combined = " ".join([message_norm, *hints_norm]).strip()

    for match in re.findall(r"\bensayo\s+(\d+)\b", combined):
        idx = int(match)
        if 1 <= idx <= len(offered_reports):
            selected.update(_doc_ids_from_report(offered_reports[idx - 1]))

    for report in offered_reports:
        label = normalize_text(str(report.get("label") or ""))
        products = [normalize_text(str(p)) for p in (report.get("products") or []) if normalize_text(str(p))]
        terms = [t for t in [label, *products] if t]
        if terms and any(t in combined for t in terms):
            selected.update(_doc_ids_from_report(report))
    return selected


# ---------------------------------------------------------------------------
# Priorización de DocContexts (producto × objetivo × temporada)
# ---------------------------------------------------------------------------

def _prioritize_for_product_objective(doc_contexts: list[DocContext]) -> list[DocContext]:
    if not doc_contexts:
        return []
    by_product: dict[str, list[DocContext]] = {}
    for dc in doc_contexts:
        key = normalize_text(dc.producto) or "__unknown__"
        by_product.setdefault(key, []).append(dc)

    selected_ids: set[str] = set()
    result: list[DocContext] = []
    for group in by_product.values():
        by_objective: dict[str, list[DocContext]] = {}
        for i, dc in enumerate(group):
            obj_key = _extract_objective_signature(dc) or f"__unknown__:{dc.doc_id or i}"
            by_objective.setdefault(obj_key, []).append(dc)
        for obj_group in by_objective.values():
            best = max(obj_group, key=lambda d: _season_sort_key(d.temporada))
            if best.doc_id and best.doc_id in selected_ids:
                continue
            if best.doc_id:
                selected_ids.add(best.doc_id)
            result.append(best)
    return result or doc_contexts


def _extract_objective_signature(doc: DocContext) -> str:
    snippets: list[str] = []
    for chunk in doc.chunks:
        section = str(chunk.get("section_norm") or "").upper()
        if "OBJETIVO" not in section and "OBJECTIVE" not in section:
            continue
        text = str(chunk.get("text") or "").strip()
        if text:
            snippets.append(normalize_text(text))
    if not snippets:
        return ""
    tokens = [t for t in re.findall(r"[a-z0-9]+", " ".join(snippets)) if len(t) >= 4]
    return " ".join(tokens[:16]) if tokens else ""


def _season_sort_key(temporada: str) -> tuple[int, int, str]:
    text = str(temporada or "")
    years = [int(y) for y in re.findall(r"(19\d{2}|20\d{2})", text)]
    if not years:
        return (0, 0, "")
    return (max(years), min(years), text)


def _parece_pedir_ensayo_especifico(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if re.search(r"\bensayo\s+\d+\b", normalized):
        return True
    return any(tok in normalized for tok in ("detalle", "mas informacion", "más información", "ampliar"))


# ---------------------------------------------------------------------------
# Construcción de opciones de reporte desde hits
# ---------------------------------------------------------------------------

def _build_report_options_from_hits(
    hits: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    index = load_cer_index(settings.cer_csv_path)
    by_pdf = {normalize_text(rec.pdf): rec for rec in index.records if normalize_text(rec.pdf)}

    options: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    for hit in hits:
        payload = hit.get("payload") or {}
        doc_id = str(payload.get("doc_id") or "").strip()
        if doc_id and doc_id in seen_doc_ids:
            continue
        if doc_id:
            seen_doc_ids.add(doc_id)

        pdf = str(payload.get("pdf_filename") or "").strip()
        rec = by_pdf.get(normalize_text(pdf))

        producto = str((rec.producto if rec else payload.get("producto")) or "").strip() or "N/D"
        cliente = str((rec.cliente if rec else payload.get("cliente")) or "").strip() or "N/D"
        temporada = str((rec.temporada if rec else payload.get("temporada")) or "").strip() or "N/D"
        especie = str((rec.especie if rec else payload.get("especie")) or "").strip() or "N/D"
        variedad = str((rec.variedad if rec else payload.get("variedad")) or "").strip() or "N/D"

        label = f"{producto} ({especie}, {variedad}, {temporada})"
        options.append({
            "label": label, "products": [producto], "doc_ids": [doc_id] if doc_id else [],
            "producto": producto, "cliente": cliente, "temporada": temporada,
            "especie": especie, "variedad": variedad,
        })

    # Deduplicar
    unique: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, ...]] = set()
    for opt in options:
        key = (
            normalize_text(str(opt.get("producto") or "")),
            normalize_text(str(opt.get("cliente") or "")),
            normalize_text(str(opt.get("temporada") or "")),
            normalize_text(str(opt.get("especie") or "")),
            normalize_text(str(opt.get("variedad") or "")),
        )
        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(opt)
    return unique


def _build_report_options_from_csv_species(
    settings: Settings,
    species_hints_norm: set[str],
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not species_hints_norm:
        return []

    index = load_cer_index(settings.cer_csv_path)
    options: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str, str]] = set()

    for rec in index.records:
        especie_norm = normalize_text(rec.especie)
        if not especie_norm or especie_norm not in species_hints_norm:
            continue

        key = (
            normalize_text(rec.producto),
            normalize_text(rec.cliente),
            normalize_text(rec.temporada),
            especie_norm,
            normalize_text(rec.variedad),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)

        producto = rec.producto or "N/D"
        cliente = rec.cliente or "N/D"
        temporada = rec.temporada or "N/D"
        especie = rec.especie or "N/D"
        variedad = rec.variedad or "N/D"
        label = f"{producto} ({especie}, {variedad}, {temporada})"

        options.append({
            "label": label,
            "products": [producto],
            "doc_ids": [rec.pdf] if rec.pdf else [],
            "producto": producto,
            "cliente": cliente,
            "temporada": temporada,
            "especie": especie,
            "variedad": variedad,
        })
        if len(options) >= max(1, int(limit)):
            break

    return options


def _extract_species_and_season_from_label(label: str) -> tuple[str, str]:
    text = str(label or "").strip()
    if not text:
        return "", ""
    match = re.search(r"\(([^)]+)\)", text)
    if not match:
        return "", ""
    parts = [p.strip() for p in match.group(1).split(",") if p.strip()]
    if len(parts) < 2:
        return "", ""
    return normalize_text(parts[0]), normalize_text(parts[-1])


def _limpiar_producto_en_item(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"\(en\s+[^)]+\)", "", value, flags=re.IGNORECASE).strip()
    return re.sub(r"\s+", " ", value)


# ---------------------------------------------------------------------------
# Construcción de bloque de contexto CER
# ---------------------------------------------------------------------------

def build_context_block(doc_contexts: list[DocContext]) -> str:
    parts: list[str] = []
    for i, dc in enumerate(doc_contexts, start=1):
        parts.append(f"=== INFORME {i} ===")
        parts.append(f"doc_id: {dc.doc_id}")
        parts.append(f"temporada: {dc.temporada}")
        parts.append(f"cliente: {dc.cliente}")
        parts.append(f"producto: {dc.producto}")
        parts.append(f"especie: {dc.especie}")
        parts.append(f"variedad: {dc.variedad}")
        parts.append(f"comuna: {dc.comuna}")
        parts.append(f"localidad: {dc.localidad}")
        parts.append(f"region: {dc.region}")
        parts.append(f"ubicacion: {dc.ubicacion}")
        for ch in dc.chunks:
            text = str(ch.get("text") or "").strip()
            if not text:
                continue
            parts.append(f"[chunk {ch.get('chunk_index')} | section {ch.get('section_norm') or ''}]")
            parts.append(text)
        parts.append("")
    return "\n".join(parts).strip() or "SIN_CONTEXTO_CER"
