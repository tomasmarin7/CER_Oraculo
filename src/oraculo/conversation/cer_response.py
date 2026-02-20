"""Lógica de respuestas CER: búsqueda de ensayos, detalle y follow-up."""

from __future__ import annotations

import logging
import re
from pathlib import Path
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
from ..sources.cer_csv_lookup import detect_cer_entities, find_cer_records_by_query, load_cer_index
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
    refined_query: str,
    conversation_context: str,
    hits: list[dict[str, Any]],
    settings: Settings,
) -> tuple[str, str, list[dict[str, Any]], list[DocContext]]:
    """Genera texto de listado, escenario detectado, opciones y contexto overview."""
    overview_by_doc_id = _build_overview_doc_context_by_doc_id(hits, settings)
    csv_seed_reports = _build_report_options_from_csv_query(settings, question, limit=12)
    rag_csv_reports = _build_report_options_from_hits(
        hits,
        settings,
        overview_by_doc_id=overview_by_doc_id,
    )
    offered_reports = _merge_report_options(rag_csv_reports, csv_seed_reports)
    if not offered_reports:
        text = (
            "No se ha ensayado este caso en el CER.\n"
            "Si quieres, puedo buscar en nuestra base de datos de etiquetas productos que indiquen "
            "este problema en su etiqueta. ¿Lo hago?\n"
            "Si prefieres no revisar la base de datos de etiquetas, dime otra consulta y buscamos en ensayos CER."
        )
        return text, "no_cer", [], []

    species_hints = detect_cer_entities(settings.cer_csv_path, question).get("especies", set())
    species_hints_norm = {normalize_text(s) for s in species_hints if normalize_text(s)}
    if species_hints_norm and not any(
        normalize_text(str(r.get("especie") or "")) in species_hints_norm for r in offered_reports
    ):
        csv_matches = _build_report_options_from_csv_species(settings, species_hints_norm)
        if csv_matches:
            offered_reports = _merge_report_options(csv_matches, offered_reports)

    _annotate_inclusion_logic(offered_reports, species_hints_norm=species_hints_norm)
    direct_crop_reports = [r for r in offered_reports if str(r.get("match_scope") or "") == "direct_crop"]
    cross_crop_reports = [r for r in offered_reports if str(r.get("match_scope") or "") == "cross_crop"]

    # Si no hay evidencia directa para el cultivo pedido, pero sí evidencia en otros cultivos,
    # guiamos al LLM con solo opciones cross-crop para forzar CASO B sin hardcodear texto.
    prompt_reports = offered_reports
    if species_hints_norm and not direct_crop_reports and cross_crop_reports:
        prompt_reports = cross_crop_reports

    prompt = _build_listar_ensayos_prompt(
        question=question,
        refined_query=refined_query,
        conversation_context=conversation_context,
        report_options=prompt_reports,
    )
    try:
        text = (
            generate_answer(prompt, settings, system_instruction="", profile="complex", require_complete=True) or ""
        ).strip()
    except Exception:
        text = ""
    if not text:
        return _fallback_listing_text(question, prompt_reports, overview_by_doc_id)
    text = _normalize_listing_output_format(text)

    ordered_reports = _reorder_reports_from_listed_text(text, prompt_reports)
    scenario = _detect_listing_scenario(text)
    final_reports = ordered_reports or prompt_reports
    overview_contexts = _collect_overview_contexts_from_reports(final_reports, overview_by_doc_id)
    return text, scenario, final_reports, overview_contexts


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
        if selected_doc_ids:
            # Si ya hay selección explícita, no hacer retrieve abierto para evitar mezcla.
            return []

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
    explicit_selection = bool(selected_report_indexes)

    # Selección por índices del router
    for idx in (selected_report_indexes or []):
        if 1 <= idx <= len(offered_reports):
            selected_doc_ids.update(_doc_ids_from_report(offered_reports[idx - 1]))

    # "ensayo N" explícito
    for match in re.findall(r"\bensayo\s+(\d+)\b", combined):
        idx = int(match)
        if 1 <= idx <= len(offered_reports):
            selected_doc_ids.update(_doc_ids_from_report(offered_reports[idx - 1]))
            explicit_selection = True

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
            explicit_selection = True

    # Mención de etiqueta o producto
    for report in offered_reports:
        label_norm = normalize_text(str(report.get("label") or ""))
        products_norm = [normalize_text(str(p)) for p in (report.get("products") or []) if str(p).strip()]
        terms = [t for t in [label_norm, *products_norm] if t]
        if terms and any(t in combined for t in terms):
            selected_doc_ids.update(_doc_ids_from_report(report))
            explicit_selection = True

    # Si solo se ofreció un informe CER, anclar el detalle a ese informe
    # para evitar mezclar contextos/fuentes de otras coincidencias semánticas.
    if not explicit_selection and not selected_doc_ids and len(offered_reports) == 1:
        selected_doc_ids.update(_doc_ids_from_report(offered_reports[0]))
        explicit_selection = True

    if explicit_selection and selected_doc_ids:
        filtered = [dc for dc in doc_contexts if dc.doc_id in selected_doc_ids]
        return _prioritize_for_product_objective(filtered) if filtered else []

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

    if not selected and len(offered_reports) == 1:
        selected.update(_doc_ids_from_report(offered_reports[0]))
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
    *,
    overview_by_doc_id: dict[str, DocContext] | None = None,
) -> list[dict[str, Any]]:
    index = load_cer_index(settings.cer_csv_path)
    by_pdf = {normalize_text(rec.pdf): rec for rec in index.records if normalize_text(rec.pdf)}
    by_doc_key: dict[str, Any] = {}
    for rec in index.records:
        for key in _doc_lookup_keys_from_value(rec.pdf):
            by_doc_key[key] = rec

    options: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    for hit in hits:
        payload = hit.get("payload") or {}
        doc_id = str(payload.get("doc_id") or "").strip()
        if doc_id and doc_id in seen_doc_ids:
            continue
        if doc_id:
            seen_doc_ids.add(doc_id)

        pdf = str(payload.get("pdf_filename") or payload.get("pdf") or "").strip()
        rec = by_pdf.get(normalize_text(pdf)) if pdf else None
        if rec is None:
            for key in _doc_lookup_keys_from_value(doc_id):
                rec = by_doc_key.get(key)
                if rec is not None:
                    break
        if rec is None and pdf:
            for key in _doc_lookup_keys_from_value(pdf):
                rec = by_doc_key.get(key)
                if rec is not None:
                    break
        if rec is None:
            # Fallback robusto: si no hay match CSV exacto para doc/pdf,
            # construir opción desde payload de Qdrant para no perder evidencia CER real.
            producto = _display_value(payload.get("producto"))
            cliente = _display_value(payload.get("cliente"))
            temporada = _display_value(payload.get("temporada"))
            especie = _display_value(payload.get("especie"))
            variedad = _display_value(payload.get("variedad"))
            source = "rag_payload"
        else:
            producto = _display_value(rec.producto)
            cliente = _display_value(rec.cliente)
            temporada = _display_value(rec.temporada)
            especie = _display_value(rec.especie)
            variedad = _display_value(rec.variedad)
            source = "rag"

        label = f"{producto} ({especie}, {variedad}, {temporada})"
        doc_ids = []
        if doc_id:
            doc_ids.append(doc_id)
        if pdf:
            doc_ids.extend(_doc_id_candidates_from_pdf(pdf))
        if rec is not None:
            doc_ids.extend(_doc_id_candidates_from_pdf(rec.pdf))
        unique_doc_ids: list[str] = []
        seen_doc_id_keys: set[str] = set()
        for item in doc_ids:
            clean = str(item or "").strip()
            key = normalize_text(clean)
            if not clean or key in seen_doc_id_keys:
                continue
            seen_doc_id_keys.add(key)
            unique_doc_ids.append(clean)
        options.append({
            "label": label, "products": [producto], "doc_ids": unique_doc_ids,
            "producto": producto, "cliente": cliente, "temporada": temporada,
            "especie": especie, "variedad": variedad,
            "overview": _extract_overview_text(overview_by_doc_id.get(doc_id)) if overview_by_doc_id and doc_id else "",
            "source": source,
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


def _build_overview_doc_context_by_doc_id(
    hits: list[dict[str, Any]],
    settings: Settings,
) -> dict[str, DocContext]:
    if not hits:
        return {}
    top_docs = max(1, len({str((h.get("payload") or {}).get("doc_id") or "").strip() for h in hits if str((h.get("payload") or {}).get("doc_id") or "").strip()}))
    doc_contexts = build_doc_contexts_from_hits(hits, settings, top_docs=top_docs)
    out: dict[str, DocContext] = {}
    for dc in doc_contexts:
        doc_id = str(dc.doc_id or "").strip()
        if doc_id:
            out[doc_id] = dc
    return out


def _collect_overview_contexts_from_reports(
    report_options: list[dict[str, Any]],
    overview_by_doc_id: dict[str, DocContext],
) -> list[DocContext]:
    out: list[DocContext] = []
    seen: set[str] = set()
    for report in report_options:
        for doc_id in _doc_ids_from_report(report):
            if doc_id in seen:
                continue
            ctx = overview_by_doc_id.get(doc_id)
            if ctx is None:
                continue
            seen.add(doc_id)
            out.append(ctx)
            break
    return out


def _extract_overview_text(doc_context: DocContext | None) -> str:
    if doc_context is None:
        return ""

    candidates: list[str] = []
    for chunk in doc_context.chunks:
        chunk_type = normalize_text(str(chunk.get("chunk_type") or ""))
        section = normalize_text(str(chunk.get("section_norm") or ""))
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        if chunk_type in {"doc_overview", "conclusion_overview"}:
            candidates.append(text)
            continue
        if any(token in section for token in ("objetivo", "resumen", "conclusion", "conclusiones", "objective", "abstract")):
            candidates.append(text)

    if not candidates:
        for chunk in doc_context.chunks[:2]:
            text = str(chunk.get("text") or "").strip()
            if text:
                candidates.append(text)
                break

    if not candidates:
        return ""

    merged = " ".join(candidates)
    merged = re.sub(r"\s+", " ", merged).strip()
    return merged[:420]


def _merge_report_options(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
    limit: int = 12,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for item in [*primary, *secondary]:
        key = (
            normalize_text(str(item.get("producto") or "")),
            normalize_text(str(item.get("cliente") or "")),
            normalize_text(str(item.get("temporada") or "")),
            normalize_text(str(item.get("especie") or "")),
            normalize_text(str(item.get("variedad") or "")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _build_report_options_from_csv_query(
    settings: Settings,
    question: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    records = find_cer_records_by_query(settings.cer_csv_path, question, limit=max(1, int(limit)))
    if not records:
        return []

    options: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str, str]] = set()
    for rec in records:
        key = (
            normalize_text(rec.producto),
            normalize_text(rec.cliente),
            normalize_text(rec.temporada),
            normalize_text(rec.especie),
            normalize_text(rec.variedad),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)

        producto = _display_value(rec.producto)
        cliente = _display_value(rec.cliente)
        temporada = _display_value(rec.temporada)
        especie = _display_value(rec.especie)
        variedad = _display_value(rec.variedad)
        label = f"{producto} ({especie}, {variedad}, {temporada})"
        options.append({
            "label": label,
            "products": [producto],
            "doc_ids": _doc_id_candidates_from_pdf(rec.pdf),
            "producto": producto,
            "cliente": cliente,
            "temporada": temporada,
            "especie": especie,
            "variedad": variedad,
            "source": "csv_query",
        })
    return options


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

        producto = _display_value(rec.producto)
        cliente = _display_value(rec.cliente)
        temporada = _display_value(rec.temporada)
        especie = _display_value(rec.especie)
        variedad = _display_value(rec.variedad)
        label = f"{producto} ({especie}, {variedad}, {temporada})"

        options.append({
            "label": label,
            "products": [producto],
            "doc_ids": _doc_id_candidates_from_pdf(rec.pdf),
            "producto": producto,
            "cliente": cliente,
            "temporada": temporada,
            "especie": especie,
            "variedad": variedad,
            "source": "csv_species",
        })
        if len(options) >= max(1, int(limit)):
            break

    return options


def _display_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "N/D"
    if normalize_text(text) in {"na", "n/a", "nd", "n/d", "s/i", "sin info", "sin informacion"}:
        return "N/D"
    return text


def _doc_lookup_keys_from_value(value: Any) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    name = Path(raw).name
    stem = Path(name).stem
    keys = {normalize_text(raw), normalize_text(name), normalize_text(stem)}
    return {k for k in keys if k}


def _doc_id_candidates_from_pdf(pdf_value: Any) -> list[str]:
    raw = str(pdf_value or "").strip()
    if not raw:
        return []
    name = Path(raw).name
    stem = Path(name).stem
    candidates = [stem, name, raw]
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = str(item or "").strip()
        if not text:
            continue
        key = normalize_text(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _build_listar_ensayos_prompt(
    *,
    question: str,
    refined_query: str,
    conversation_context: str,
    report_options: list[dict[str, Any]],
) -> str:
    template = load_prompt_template(LISTAR_ENSAYOS_PROMPT_FILE)
    context_rows: list[str] = []
    for i, report in enumerate(report_options, start=1):
        context_rows.append(
            (
                f"INFORME {i}\n"
                f"- producto: {report.get('producto') or 'N/D'}\n"
                f"- cliente: {report.get('cliente') or 'N/D'}\n"
                f"- temporada: {report.get('temporada') or 'N/D'}\n"
                f"- cultivo: {report.get('especie') or 'N/D'}\n"
                f"- variedad: {report.get('variedad') or 'N/D'}\n"
                f"- match_scope: {report.get('match_scope') or 'query_match'}\n"
                f"- inclusion_reason: {report.get('inclusion_reason') or 'N/D'}\n"
                f"- overview: {report.get('overview') or 'N/D'}\n"
                f"- doc_ids: {', '.join(_doc_ids_from_report(report)) or 'N/D'}"
            )
        )
    context_block = "\n\n".join(context_rows) if context_rows else "SIN_CONTEXTO_CER"
    question_block = (
        f"{(question or '').strip()}\n\n"
        f"CONTEXTO_CONVERSACION_RECIENTE:\n{(conversation_context or '').strip() or '(sin contexto)'}"
    )
    return (
        template
        .replace("{{question}}", question_block)
        .replace("{{refined_query}}", (refined_query or "").strip())
        .replace("{{context_block}}", context_block)
    )


def _reorder_reports_from_listed_text(
    listed_text: str,
    report_options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not listed_text or not report_options:
        return report_options
    lines = [line.strip() for line in listed_text.splitlines() if line.strip().startswith("• ")]
    if not lines:
        return report_options

    matched: list[dict[str, Any]] = []
    seen: set[int] = set()
    for line in lines:
        body = line.lstrip("•").strip()
        parts = [p.strip() for p in body.split("|")]
        if len(parts) < 4:
            continue
        product = normalize_text(parts[0])
        client = normalize_text(parts[1])
        season = normalize_text(parts[2])
        species_text = parts[3]
        species_part = species_text.split("(")[0].strip()
        variety_part = ""
        variety_match = re.search(r"\(([^)]+)\)", species_text)
        if variety_match:
            variety_part = variety_match.group(1).strip()
        species = normalize_text(species_part)
        variety = normalize_text(variety_part)

        idx = _match_report_index(
            report_options,
            product=product,
            client=client,
            season=season,
            species=species,
            variety=variety,
        )
        if idx is None or idx in seen:
            continue
        seen.add(idx)
        matched.append(report_options[idx])
    if not matched:
        return report_options
    matched.extend(report_options[i] for i in range(len(report_options)) if i not in seen)
    return matched


def _match_report_index(
    report_options: list[dict[str, Any]],
    *,
    product: str,
    client: str,
    season: str,
    species: str,
    variety: str,
) -> int | None:
    for idx, report in enumerate(report_options):
        r_product = normalize_text(str(report.get("producto") or ""))
        r_client = normalize_text(str(report.get("cliente") or ""))
        r_season = normalize_text(str(report.get("temporada") or ""))
        r_species = normalize_text(str(report.get("especie") or ""))
        r_variety = normalize_text(str(report.get("variedad") or ""))
        if product and product != r_product:
            continue
        if client and client != r_client:
            continue
        if season and season != r_season:
            continue
        if species and species != r_species:
            continue
        if variety and variety != r_variety:
            continue
        return idx
    return None


def _detect_listing_scenario(text: str) -> str:
    normalized = normalize_text(text)
    if "no se ha ensayado este caso en el cer" in normalized:
        return "no_cer"
    if "no tenemos ensayos cer directos" in normalized:
        return "cross_crop"
    return "direct"


def _normalize_listing_output_format(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").splitlines()]
    if not lines:
        return text
    first = lines[0].strip()
    is_listing = (
        first.startswith("Encontré estos ensayos del CER para")
        or first.startswith("Para ")
    )
    if not is_listing:
        return text

    idx = 1
    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    bullets: list[str] = []
    while idx < len(lines):
        current = lines[idx].strip()
        if not current:
            idx += 1
            continue
        if current.startswith("• "):
            bullets.append(current)
            idx += 1
            continue
        break

    if not bullets:
        return text

    remainder = [line.strip() for line in lines[idx:] if line.strip()]
    out_text = first + "\n" + "\n\n".join(bullets)
    if remainder:
        out_text += "\n\n" + "\n".join(remainder)
    return out_text.strip()


def _fallback_listing_text(
    question: str,
    report_options: list[dict[str, Any]],
    overview_by_doc_id: dict[str, DocContext],
) -> tuple[str, str, list[dict[str, Any]], list[DocContext]]:
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
        for r in report_options
    ]
    text = (
        f"Encontré estos ensayos del CER para {(question or 'tu consulta').strip()}:\n"
        + "\n\n".join(lines)
        + "\n\n¿Sobre cuáles ensayos quieres que te detalle más?\n"
        "Si ninguno te sirve, puedo buscar productos en nuestra base de datos de etiquetas "
        "que indiquen ese problema en su etiqueta.\n"
        "Si tampoco quieres revisar la base de datos de etiquetas, dime otro problema o cultivo "
        "y hacemos una nueva búsqueda en ensayos CER."
    )
    return text, "direct", report_options, _collect_overview_contexts_from_reports(report_options, overview_by_doc_id)


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


def _annotate_inclusion_logic(
    report_options: list[dict[str, Any]],
    *,
    species_hints_norm: set[str],
) -> None:
    if not report_options:
        return

    for report in report_options:
        source = normalize_text(str(report.get("source") or "")) or "unknown"
        species_norm = normalize_text(str(report.get("especie") or ""))
        has_crop_hint = bool(species_hints_norm)
        species_roots = token_roots(species_norm)
        is_direct_crop = bool(has_crop_hint and species_norm and (
            species_norm in species_hints_norm
            or any(species_roots and (species_roots & token_roots(hint)) for hint in species_hints_norm)
        ))

        if is_direct_crop:
            report["match_scope"] = "direct_crop"
            if source == "csv_species":
                report["inclusion_reason"] = "Coincide con el cultivo consultado (validado contra CER.csv)."
            elif source == "csv_query":
                report["inclusion_reason"] = "Coincide con cultivo y términos de la consulta."
            elif source == "rag":
                report["inclusion_reason"] = "Coincide con el cultivo consultado y fue recuperado por similitud técnica."
            else:
                report["inclusion_reason"] = "Coincide con el cultivo consultado."
            continue

        if has_crop_hint:
            report["match_scope"] = "cross_crop"
            if source == "rag":
                report["inclusion_reason"] = (
                    "Incluido como referencia en otro cultivo por similitud técnica con el problema consultado."
                )
            else:
                report["inclusion_reason"] = (
                    "Incluido como referencia en otro cultivo por coincidencia con los términos de la consulta."
                )
            continue

        report["match_scope"] = "query_match"
        if source == "rag":
            report["inclusion_reason"] = "Coincidencia semántica con la consulta técnica."
        elif source == "csv_query":
            report["inclusion_reason"] = "Coincidencia por términos de consulta en CER.csv."
        elif source == "csv_species":
            report["inclusion_reason"] = "Coincidencia por especie detectada en el contexto."
        else:
            report["inclusion_reason"] = "Coincide con la consulta."



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
