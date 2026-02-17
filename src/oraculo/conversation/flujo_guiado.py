from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..aplicacion.texto_oraculo import INTRO_ORACULO_CER
from ..config import Settings
from ..followup import (
    build_detail_followup_prompt,
    build_followup_chat_prompt,
    render_report_options,
    route_guided_followup,
)
from ..providers.llm import generate_answer
from ..rag.doc_context import DocContext, build_doc_contexts_from_hits
from ..rag.retriever import (
    retrieve,
    retrieve_sag,
    retrieve_sag_rows_by_ids,
    retrieve_sag_rows_for_products,
)
from ..sources.sag_excel_lookup import (
    find_products_by_ingredient,
    find_products_by_objective,
    get_product_composition,
)
from ..sources.resolver import format_sources_from_hits
from .modelos import EstadoSesion, SesionChat

LISTAR_ENSAYOS_PROMPT_FILE = "listar_ensayos.md"
RESPUESTA_SAG_PROMPT_FILE = "respuesta_sag.md"
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GuidedFlowResult:
    handled: bool
    response: str = ""
    rag_tag: str = "none"
    sources: list[str] = field(default_factory=list)


def is_greeting_message(text: str) -> bool:
    normalized = _normalize_text(text)
    greetings = {
        "hola",
        "hola!",
        "buenas",
        "buen dia",
        "buenos dias",
        "buenas tardes",
        "buenas noches",
        "holi",
        "inicio",
    }
    return normalized in greetings


def get_guided_intro_text() -> str:
    return INTRO_ORACULO_CER


def try_handle_guided_flow(
    sesion: SesionChat,
    user_message: str,
    settings: Settings,
    top_k: int = 8,
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    text = (user_message or "").strip()
    if not text:
        return GuidedFlowResult(handled=False)

    if sesion.estado == EstadoSesion.ESPERANDO_DETALLE_PRODUCTO:
        return _handle_product_detail_followup(
            sesion,
            text,
            settings,
            top_k,
            progress_callback=progress_callback,
        )

    if sesion.estado == EstadoSesion.ESPERANDO_CONFIRMACION_SAG:
        return _handle_sag_followup(
            sesion,
            text,
            settings,
            progress_callback=progress_callback,
        )

    if sesion.estado in {EstadoSesion.ESPERANDO_PROBLEMA, EstadoSesion.MENU}:
        return _handle_problem_query(
            sesion,
            text,
            settings,
            top_k,
            progress_callback=progress_callback,
        )

    if _looks_like_problem_query(text):
        return _handle_problem_query(
            sesion,
            text,
            settings,
            top_k,
            progress_callback=progress_callback,
        )

    return GuidedFlowResult(handled=False)


def execute_guided_action_from_router(
    sesion: SesionChat,
    user_message: str,
    settings: Settings,
    *,
    action: str,
    query: str = "",
    top_k: int = 8,
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    action_norm = (action or "").strip().upper()
    text = (user_message or "").strip()
    effective_query = (query or text).strip()

    if action_norm == "NEW_CER_QUERY":
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        return _handle_problem_query(
            sesion,
            effective_query,
            settings,
            top_k,
            progress_callback=progress_callback,
        )

    if action_norm == "DETAIL_FROM_LIST":
        sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
        return _handle_product_detail_followup(
            sesion,
            text,
            settings,
            top_k,
            progress_callback=progress_callback,
        )

    if action_norm == "ASK_SAG":
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        return _generate_sag_response(
            effective_query,
            settings,
            user_message=text,
            product_hint="",
            progress_callback=progress_callback,
        )

    if action_norm in {"CHAT_REPLY", "CLARIFY"}:
        return try_handle_guided_flow(
            sesion,
            text,
            settings,
            top_k=top_k,
            progress_callback=progress_callback,
        )

    return GuidedFlowResult(handled=False)


def _handle_problem_query(
    sesion: SesionChat,
    question: str,
    settings: Settings,
    top_k: int,
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    if progress_callback:
        progress_callback("Buscando ensayos CER relevantes para tu consulta...")
    logger.info("🔍 Flujo CER | iniciando búsqueda de ensayos para la consulta del usuario...")
    refined_query, hits = retrieve(question, settings, top_k=top_k)
    if progress_callback:
        progress_callback("Armando contexto tecnico de ensayos CER...")
    logger.info("🧱 Flujo CER | construyendo contexto expandido desde %s documentos...", len(hits))
    doc_contexts = build_doc_contexts_from_hits(
        hits,
        settings,
        top_docs=max(1, min(top_k, int(settings.rag_top_docs))),
    )

    sesion.flow_data["last_question"] = question
    sesion.flow_data["last_doc_contexts"] = _serialize_doc_contexts(doc_contexts)
    sesion.flow_data["last_detail_doc_contexts"] = []

    response_text = _generate_first_response_with_context(
        question=question,
        refined_query=refined_query,
        doc_contexts=doc_contexts,
        settings=settings,
        progress_callback=progress_callback,
    )
    logger.info("📝 Flujo CER | listado de ensayos redactado.")
    scenario, report_options = _infer_scenario_and_report_options_from_text(
        response_text,
        doc_contexts,
    )

    sesion.flow_data["offered_reports"] = report_options

    # Si ya se listaron opciones de ensayos, el siguiente turno debe ser de detalle.
    # No dependemos de frases exactas del LLM para evitar perder contexto conversacional.
    if report_options:
        sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
    elif scenario == "no_cer":
        sesion.estado = EstadoSesion.ESPERANDO_CONFIRMACION_SAG
    else:
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA

    if not response_text:
        response_text = (
            "No encontré una coincidencia clara en los ensayos CER para tu consulta. "
            "¿Te gustaría que revise productos del SAG relacionados con tu problema?"
        )
        sesion.estado = EstadoSesion.ESPERANDO_CONFIRMACION_SAG

    return GuidedFlowResult(handled=True, response=response_text, rag_tag="cer")


def _handle_product_detail_followup(
    sesion: SesionChat,
    user_message: str,
    settings: Settings,
    top_k: int,
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    offered_reports = [
        report
        for report in sesion.flow_data.get("offered_reports", [])
        if isinstance(report, dict)
    ]
    if not offered_reports:
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        return GuidedFlowResult(
            handled=True,
            response="Cuéntame nuevamente el problema de tu cultivo y lo revisamos en los ensayos CER.",
        )

    # Si el usuario hace una pregunta puntual sobre el contexto ya detallado,
    # respondemos directo sin volver a enrutar como nueva consulta.
    last_detail_items = sesion.flow_data.get("last_detail_doc_contexts") or []
    last_detail_contexts = _deserialize_doc_contexts(last_detail_items)
    if _es_pregunta_sobre_contexto_actual(user_message):
        base_contexts = last_detail_contexts or _deserialize_doc_contexts(
            sesion.flow_data.get("last_doc_contexts") or []
        )
        contextual_response = _generate_conversational_followup_response(
            last_question=str(sesion.flow_data.get("last_question") or "").strip(),
            last_assistant_message=_last_assistant_message(sesion),
            user_message=user_message,
            offered_reports=offered_reports,
            doc_contexts=base_contexts,
            settings=settings,
            progress_callback=progress_callback,
        )
        sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
        return GuidedFlowResult(
            handled=True,
            response=contextual_response,
            rag_tag="none",
        )

    decision = route_guided_followup(
        last_question=str(sesion.flow_data.get("last_question") or "").strip(),
        last_assistant_message=_last_assistant_message(sesion),
        user_message=user_message,
        offered_reports=offered_reports,
        settings=settings,
    )
    if decision.selected_reports:
        logger.info(
            "🧩 Follow-up | señales de selección detectadas por router: %s",
            ", ".join(decision.selected_reports),
        )
    if decision.selected_report_indexes:
        logger.info(
            "🔢 Follow-up | índices exactos detectados por router: %s",
            ", ".join(str(i) for i in decision.selected_report_indexes),
        )

    if decision.action == "NEW_RAG_QUERY":
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        next_query = decision.query.strip() or user_message
        return _handle_problem_query(
            sesion,
            next_query,
            settings,
            top_k,
            progress_callback=progress_callback,
        )

    if decision.action == "ASK_SAG":
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        sag_product = (decision.sag_product or "").strip()
        sag_query = (decision.query or "").strip()
        if not sag_query and sag_product:
            sag_query = f"registro SAG y cultivos autorizados para {sag_product}"
        if not sag_query:
            sag_query = user_message.strip()
        return _generate_sag_response(
            sag_query,
            settings,
            user_message=user_message,
            product_hint=sag_product,
            progress_callback=progress_callback,
        )

    if decision.action == "CHAT_REPLY":
        sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
        stored_contexts = (
            last_detail_contexts
            or _deserialize_doc_contexts(sesion.flow_data.get("last_doc_contexts") or [])
        )
        chat_response = _generate_conversational_followup_response(
            last_question=str(sesion.flow_data.get("last_question") or "").strip(),
            last_assistant_message=_last_assistant_message(sesion),
            user_message=user_message,
            offered_reports=offered_reports,
            doc_contexts=stored_contexts,
            settings=settings,
            progress_callback=progress_callback,
        )
        return GuidedFlowResult(
            handled=True,
            response=chat_response,
            rag_tag="none",
        )

    if decision.action == "CLARIFY":
        sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
        clarify_text = (
            "Para ayudarte mejor, ¿prefieres que:\n"
            "• investigue en la base de ensayos del CER, o\n"
            "• busque información en productos registrados en el SAG?\n\n"
            "Si quieres seguir con los informes ya listados, indícame cuál:\n"
            f"{render_report_options(offered_reports)}"
        )
        if _normalize_text(_last_assistant_message(sesion)) == _normalize_text(clarify_text):
            clarify_text = (
                "Quiero asegurarme de entenderte bien antes de continuar.\n"
                f"Sobre \"{user_message.strip()}\": ¿quieres evidencia de ensayos CER "
                "o prefieres que revise productos registrados en SAG?"
            )
        return GuidedFlowResult(
            handled=True,
            response=clarify_text,
            rag_tag="none",
        )

    stored = sesion.flow_data.get("last_doc_contexts") or []
    stored_contexts = _deserialize_doc_contexts(stored)
    detail_response = _generate_cer_detail_followup_response(
        user_message=user_message,
        last_question=str(sesion.flow_data.get("last_question") or "").strip(),
        last_assistant_message=_last_assistant_message(sesion),
        offered_reports=offered_reports,
        seed_doc_contexts=stored_contexts,
        settings=settings,
        top_k=top_k,
        sesion=sesion,
        selected_report_hints=decision.selected_reports or [],
        selected_report_indexes=decision.selected_report_indexes or [],
        progress_callback=progress_callback,
    )

    sesion.estado = EstadoSesion.ESPERANDO_DETALLE_PRODUCTO
    return GuidedFlowResult(handled=True, response=detail_response, rag_tag="cer")


def _handle_sag_followup(
    sesion: SesionChat,
    user_message: str,
    settings: Settings,
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    if _is_negative(user_message):
        sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
        return GuidedFlowResult(
            handled=True,
            response="Entendido. Si quieres, cuéntame otro problema del cultivo para revisarlo.",
        )

    if not _is_affirmative(user_message):
        return GuidedFlowResult(handled=False)

    last_question = str(sesion.flow_data.get("last_question") or "").strip()
    query = f"registro SAG y cultivos autorizados para: {last_question}".strip()

    sesion.estado = EstadoSesion.ESPERANDO_PROBLEMA
    return _generate_sag_response(
        query,
        settings,
        user_message=user_message,
        product_hint="",
        progress_callback=progress_callback,
    )


def _generate_sag_response(
    query: str,
    settings: Settings,
    *,
    user_message: str = "",
    product_hint: str = "",
    progress_callback: Callable[[str], None] | None = None,
) -> GuidedFlowResult:
    normalized_query = (query or "").strip()
    if not normalized_query:
        normalized_query = "consulta de productos SAG"
    effective_user_message = user_message or normalized_query

    if progress_callback:
        progress_callback("Consultando registros SAG relacionados...")
    combined_query_norm = _normalize_text(f"{normalized_query} {effective_user_message}")
    ingredient_hint = _extract_ingredient_hint_from_text(combined_query_norm)
    objective_hint = _extract_objective_hint_from_text(combined_query_norm)
    base_top_k = max(1, int(settings.rag_sag_top_k))
    retrieval_top_k = max(base_top_k * 2, 20)
    if progress_callback:
        progress_callback("Buscando coincidencias SAG en Qdrant...")
    seed_hits = retrieve_sag(
        normalized_query,
        settings=settings,
        top_k=retrieval_top_k,
    )
    filtered_seed_hits = _filtrar_hits_sag_por_consulta(
        seed_hits,
        query_text=normalized_query,
        user_message=effective_user_message,
        product_hint=product_hint,
    )
    if ingredient_hint:
        ingredient_seed_hits = _filter_sag_hits_by_field(
            seed_hits,
            ingredient_hint,
            field="ingredient",
        )
        if ingredient_seed_hits:
            # Si detectamos ingrediente en la consulta, priorizamos ese subconjunto
            # para enriquecer y reducir ruido antes de Gemini.
            filtered_seed_hits = ingredient_seed_hits
    filtered_seed_hits = _filtrar_hits_sag_por_producto(filtered_seed_hits, product_hint)
    seed_for_enrich = filtered_seed_hits or seed_hits
    sag_hits = retrieve_sag_rows_for_products(
        seed_for_enrich,
        settings,
        max_rows_per_filter=max(base_top_k * 16, 160),
    )
    # Boost de recall con Excel para consultas por objetivo/plaga (ej: "pulgon").
    # Esto evita perder productos por depender solo del top-k semántico inicial.
    if objective_hint:
        excel_obj_product_ids, excel_obj_auths = find_products_by_objective(
            settings.sag_excel_path,
            objective_hint,
        )
        if excel_obj_product_ids or excel_obj_auths:
            excel_obj_rows = retrieve_sag_rows_by_ids(
                settings=settings,
                product_ids=excel_obj_product_ids,
                auth_numbers=excel_obj_auths,
                max_rows=max(base_top_k * 220, 4500),
            )
            if excel_obj_rows:
                sag_hits = _merge_hits_by_id(sag_hits, excel_obj_rows)
                logger.info(
                    "📎 SAG+Excel | objetivo=%s | product_ids=%s | auths=%s | rows_agregadas=%s | total=%s",
                    objective_hint,
                    len(excel_obj_product_ids),
                    len(excel_obj_auths),
                    len(excel_obj_rows),
                    len(sag_hits),
                )
    if ingredient_hint:
        excel_product_ids, excel_auths = find_products_by_ingredient(
            settings.sag_excel_path,
            ingredient_hint,
        )
        if excel_product_ids or excel_auths:
            excel_rows = retrieve_sag_rows_by_ids(
                settings=settings,
                product_ids=excel_product_ids,
                auth_numbers=excel_auths,
                max_rows=max(base_top_k * 24, 240),
            )
            if excel_rows:
                sag_hits = _merge_hits_by_id(sag_hits, excel_rows)
                logger.info(
                    "📎 SAG+Excel | ingrediente=%s | product_ids=%s | auths=%s | rows_agregadas=%s | total=%s",
                    ingredient_hint,
                    len(excel_product_ids),
                    len(excel_auths),
                    len(excel_rows),
                    len(sag_hits),
                )
        ingredient_hits_final = _filter_sag_hits_by_field(
            sag_hits,
            ingredient_hint,
            field="ingredient",
        )
        logger.info(
            "🎯 SAG | post-enrich ingrediente=%s | antes=%s | despues=%s",
            ingredient_hint,
            len(sag_hits),
            len(ingredient_hits_final),
        )
        if ingredient_hits_final:
            sag_hits = ingredient_hits_final
    if objective_hint and not ingredient_hint:
        objective_hits_final = _filter_sag_hits_by_field(
            sag_hits,
            objective_hint,
            field="objective",
        )
        logger.info(
            "🎯 SAG | post-enrich objetivo=%s | antes=%s | despues=%s",
            objective_hint,
            len(sag_hits),
            len(objective_hits_final),
        )
        if objective_hits_final:
            sag_hits = objective_hits_final
    if not sag_hits:
        return GuidedFlowResult(
            handled=True,
            response="No encontré productos del SAG con coincidencia directa para tu consulta.",
            rag_tag="sag",
        )

    consolidated_count = _count_sag_consolidated_products(sag_hits)
    compact_context = consolidated_count > 25
    context_block = (
        _build_sag_context_block_compact(sag_hits)
        if compact_context
        else _build_sag_context_block(sag_hits)
    )
    logger.info(
        "🧱 SAG contexto | productos_consolidados=%s | modo=%s | chars=%s",
        consolidated_count,
        "compacto" if compact_context else "detallado",
        len(context_block),
    )
    prompt = _build_sag_response_prompt(
        user_message=effective_user_message,
        query=normalized_query,
        product_hint=product_hint,
        context_block=context_block,
    )
    if progress_callback:
        progress_callback("Redactando respuesta con datos SAG...")
    response = (
        generate_answer(
            prompt,
            settings,
            system_instruction="",
            profile="complex",
            require_complete=True,
        )
        or ""
    ).strip()
    if not response:
        # Fallback robusto si el LLM no responde.
        response = _build_sag_response_from_hits(
            sag_hits,
            query_text=normalized_query,
            user_message=effective_user_message,
        )
    return GuidedFlowResult(handled=True, response=response, rag_tag="sag")


def _merge_hits_by_id(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(hit: dict[str, Any]) -> None:
        hid = str(hit.get("id") or "").strip()
        if hid and hid in seen_ids:
            return
        if hid:
            seen_ids.add(hid)
        out.append(hit)

    for hit in left:
        _add(hit)
    for hit in right:
        _add(hit)
    return out


def _generate_cer_detail_followup_response(
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
        progress_callback("Preparando detalle tecnico de los ensayos CER...")
    doc_contexts = list(seed_doc_contexts)
    logger.info("📂 Detalle | contextos iniciales disponibles: %s", len(doc_contexts))
    question = f"{last_question}\nSeguimiento usuario: {user_message}".strip()
    refined = question

    if not doc_contexts:
        refined, hits = retrieve(question, settings, top_k=top_k)
        doc_contexts = build_doc_contexts_from_hits(
            hits,
            settings,
            top_docs=max(1, min(top_k, int(settings.rag_top_docs))),
        )

    selected_doc_contexts = _select_doc_contexts_for_followup(
        user_message=user_message,
        offered_reports=offered_reports,
        doc_contexts=doc_contexts,
        selected_report_hints=selected_report_hints,
        selected_report_indexes=selected_report_indexes,
    )
    if selected_doc_contexts:
        doc_contexts = selected_doc_contexts
    elif selected_report_indexes:
        selected_txt = ", ".join(str(i) for i in selected_report_indexes)
        return (
            "Identifiqué que te interesa el/los ensayo(s) "
            f"{selected_txt}, pero no pude mapearlos con precisión al contexto técnico.\n"
            "¿Puedes indicarme el *número exacto* y/o el *producto* del ensayo para citar solo esa fuente?"
        )
    elif _parece_pedir_ensayo_especifico(user_message):
        return (
            "No pude identificar con precisión cuál ensayo quieres detallar.\n"
            "Indícame el número exacto (por ejemplo: *ensayo 1*) o el producto del ensayo."
        )
    logger.info("🎯 Detalle | contextos seleccionados para responder: %s", len(doc_contexts))
    logger.info(
        "🧾 Detalle | doc_ids seleccionados: %s",
        ", ".join([dc.doc_id for dc in doc_contexts if dc.doc_id]) or "sin_doc_id",
    )
    sesion.flow_data["last_detail_doc_contexts"] = _serialize_doc_contexts(doc_contexts)

    prompt = _build_detail_followup_prompt(
        last_question=last_question,
        last_assistant_message=last_assistant_message,
        user_message=user_message,
        offered_reports=offered_reports,
        context_block=_build_context_block(doc_contexts),
    )
    text = generate_answer(
        prompt,
        settings,
        system_instruction="",
        profile="complex",
        require_complete=True,
    ).rstrip()
    logger.info("📝 Detalle | respuesta técnica redactada.")

    if not doc_contexts:
        return text

    sources_seed = []
    for dc in doc_contexts:
        payload = {
            "pdf_filename": dc.pdf_filename,
            "temporada": dc.temporada,
            "cliente": dc.cliente,
            "producto": dc.producto,
            "especie": dc.especie,
            "variedad": dc.variedad,
        }
        if any(str(v).strip() for v in payload.values()):
            sources_seed.append({"payload": payload})

    sources_block = format_sources_from_hits(sources_seed)
    if sources_block:
        text = text + "\n\n" + sources_block

    return text


def _select_doc_contexts_for_followup(
    user_message: str,
    offered_reports: list[dict[str, Any]],
    doc_contexts: list[DocContext],
    selected_report_hints: list[str] | None = None,
    selected_report_indexes: list[int] | None = None,
) -> list[DocContext]:
    normalized_message = _normalize_text(user_message)
    hints = [_normalize_text(h) for h in (selected_report_hints or []) if _normalize_text(h)]
    combined_message = " ".join([normalized_message, *hints]).strip()
    if not combined_message:
        return []

    # Prioridad 1: selección exacta que devuelve el router (Gemini).
    for idx in (selected_report_indexes or []):
        if 1 <= idx <= len(offered_reports):
            selected = _doc_contexts_for_report(offered_reports[idx - 1], doc_contexts)
            if selected:
                return selected

    # Selección explícita por "ensayo N".
    ensayo_match = re.search(r"\bensayo\s+(\d+)\b", combined_message)
    if ensayo_match:
        idx = int(ensayo_match.group(1))
        if 1 <= idx <= len(offered_reports):
            selected = _doc_contexts_for_report(offered_reports[idx - 1], doc_contexts)
            if selected:
                return selected

    # Match directo por nombre de producto mencionado por el usuario.
    direct_product_matches = []
    tokens_msg = [t for t in re.findall(r"[a-z0-9áéíóúñ]+", combined_message) if len(t) >= 4]
    for dc in doc_contexts:
        prod = _normalize_text(dc.producto)
        if prod and (
            prod in combined_message
            or any(tok in prod for tok in tokens_msg)
        ):
            direct_product_matches.append(dc)
    if direct_product_matches:
        return direct_product_matches

    # Si el usuario pide todos, no filtramos.
    if any(token in combined_message for token in ("todos", "todas", "ambos", "ambas")):
        return list(doc_contexts)

    selected_doc_ids: set[str] = set()

    # Seleccion por ordinal ("el primero", "la segunda", etc.)
    ordinal_map = {
        1: ("primero", "primera", "1", "uno"),
        2: ("segundo", "segunda", "2", "dos"),
        3: ("tercero", "tercera", "3", "tres"),
        4: ("cuarto", "cuarta", "4", "cuatro"),
        5: ("quinto", "quinta", "5", "cinco"),
    }
    for idx, report in enumerate(offered_reports, start=1):
        terms = ordinal_map.get(idx, ())
        if any(re.search(rf"\b{re.escape(term)}\b", combined_message) for term in terms):
            selected = _doc_contexts_for_report(report, doc_contexts)
            if selected:
                return selected

    # Seleccion por menciones de etiqueta o productos del reporte.
    for report in offered_reports:
        label_norm = _normalize_text(str(report.get("label") or ""))
        products_norm = [
            _normalize_text(str(p))
            for p in (report.get("products") or [])
            if str(p).strip()
        ]
        report_terms = [t for t in [label_norm, *products_norm] if t]
        if report_terms and any(term in combined_message for term in report_terms):
            selected = _doc_contexts_for_report(report, doc_contexts)
            if selected:
                return selected

    # Fallback por metadata del doc cuando no hay doc_ids en offered_reports.
    if not selected_doc_ids:
        matched_contexts: list[DocContext] = []
        for dc in doc_contexts:
            terms = [
                _normalize_text(dc.especie),
                _normalize_text(dc.producto),
                _normalize_text(dc.variedad),
            ]
            terms = [t for t in terms if t]
            if terms and any(term in combined_message for term in terms):
                matched_contexts.append(dc)
        return matched_contexts

    filtered = [dc for dc in doc_contexts if dc.doc_id in selected_doc_ids]
    return filtered


def _doc_contexts_for_report(
    report: dict[str, Any],
    doc_contexts: list[DocContext],
) -> list[DocContext]:
    selected_doc_ids = {
        str(doc_id).strip()
        for doc_id in (report.get("doc_ids") or [])
        if str(doc_id).strip()
    }
    if selected_doc_ids:
        filtered = [dc for dc in doc_contexts if dc.doc_id in selected_doc_ids]
        if filtered and len(filtered) < len(doc_contexts):
            return filtered

    products_norm = [
        _normalize_text(_limpiar_producto_en_item(str(p)))
        for p in (report.get("products") or [])
        if str(p).strip()
    ]
    products_norm = [p for p in products_norm if p]
    if products_norm:
        by_product = []
        for dc in doc_contexts:
            prod = _normalize_text(dc.producto)
            if prod and any(prod == p or prod in p or p in prod for p in products_norm):
                by_product.append(dc)
        if by_product:
            return by_product

    label_raw = str(report.get("label") or "")
    label_norm = _normalize_text(label_raw)
    label_species_norm, label_temporada_norm = _extract_species_and_season_from_label(label_raw)

    if label_species_norm:
        by_label_meta = []
        for dc in doc_contexts:
            especie = _normalize_text(dc.especie)
            temporada = _normalize_text(dc.temporada)
            species_ok = especie and (
                especie == label_species_norm
                or especie in label_species_norm
                or label_species_norm in especie
                or bool(_token_roots(especie) & _token_roots(label_species_norm))
            )
            season_ok = True
            if label_temporada_norm:
                season_ok = temporada and (
                    temporada == label_temporada_norm
                    or temporada in label_temporada_norm
                    or label_temporada_norm in temporada
                )
            if species_ok and season_ok:
                by_label_meta.append(dc)
        if by_label_meta:
            return by_label_meta

    if label_norm:
        by_label = []
        for dc in doc_contexts:
            especie = _normalize_text(dc.especie)
            if especie and (especie in label_norm or label_norm in especie):
                by_label.append(dc)
        if by_label:
            return by_label

    if selected_doc_ids:
        filtered = [dc for dc in doc_contexts if dc.doc_id in selected_doc_ids]
        if filtered:
            return filtered

    return []


def _generate_first_response_with_context(
    question: str,
    refined_query: str,
    doc_contexts: list[DocContext],
    settings: Settings,
    progress_callback: Callable[[str], None] | None = None,
) -> str:
    logger.info("📝 Redacción | preparando lista de ensayos para el usuario...")
    if progress_callback:
        progress_callback("Redactando respuesta con ensayos CER...")
    prompt = _build_first_response_prompt(
        question=question,
        refined_query=refined_query,
        context_block=_build_context_block(doc_contexts),
    )
    max_attempts = 3
    last_text = ""
    for attempt in range(1, max_attempts + 1):
        raw = generate_answer(
            prompt,
            settings,
            system_instruction="",
            profile="complex",
        )
        text = (raw or "").strip()
        if text and _is_complete_first_response(text):
            if attempt > 1:
                logger.info(
                    "✅ Redacción CER completada tras reintento | intento=%s | salida=%s chars",
                    attempt,
                    len(text),
                )
            return text
        last_text = text
        logger.warning(
            "⚠️ Redacción CER incompleta | intento=%s/%s | salida=%s chars | regenerando...",
            attempt,
            max_attempts,
            len(text),
        )
        if attempt < max_attempts and progress_callback:
            progress_callback("Respuesta parcial detectada; regenerando para enviarte la versión completa...")

    if last_text:
        logger.warning(
            "⚠️ Redacción CER sin cierre tras %s intentos | se enviará mensaje de contingencia",
            max_attempts,
        )
    return (
        "No pude completar la redacción de la respuesta en este intento. "
        "¿Quieres que lo intente nuevamente?"
    )


def _load_prompt_template(filename: str) -> str:
    prompt_path = Path(__file__).resolve().parent / "prompts" / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _build_first_response_prompt(
    question: str,
    refined_query: str,
    context_block: str,
) -> str:
    template = _load_prompt_template(LISTAR_ENSAYOS_PROMPT_FILE)
    return (
        template.replace("{{question}}", question.strip())
        .replace("{{refined_query}}", (refined_query or question).strip())
        .replace("{{context_block}}", context_block)
    ).strip()


def _is_complete_first_response(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if (
        "te interesaria mas informacion de alguno de estos ensayos" in normalized
        or "te gustaria que te dijera cuales productos del sag combaten este problema" in normalized
    ):
        return True
    # Si no incluye cierre esperado y termina en token corto, suele venir truncada.
    tail = (text or "").strip().split(" ")[-1].strip(".,:;!?()[]{}")
    if tail.isdigit() and len(tail) <= 4:
        return False
    return False


def _build_sag_response_prompt(
    *,
    user_message: str,
    query: str,
    product_hint: str,
    context_block: str,
) -> str:
    template = _load_prompt_template(RESPUESTA_SAG_PROMPT_FILE)
    return (
        template.replace("{{user_message}}", user_message.strip())
        .replace("{{query}}", query.strip())
        .replace("{{product_hint}}", (product_hint or "no especificado").strip())
        .replace("{{context_block}}", context_block)
    ).strip()


def _build_detail_followup_prompt(
    last_question: str,
    last_assistant_message: str,
    user_message: str,
    offered_reports: list[dict[str, Any]],
    context_block: str,
) -> str:
    return build_detail_followup_prompt(
        last_question=last_question,
        last_assistant_message=last_assistant_message,
        user_message=user_message,
        offered_reports=offered_reports,
        context_block=context_block,
    )


def _build_followup_chat_prompt(
    last_question: str,
    last_assistant_message: str,
    user_message: str,
    offered_reports: list[dict[str, Any]],
    context_block: str,
) -> str:
    return build_followup_chat_prompt(
        last_question=last_question,
        last_assistant_message=last_assistant_message,
        user_message=user_message,
        offered_reports=offered_reports,
        context_block=context_block,
    )


def _build_context_block(doc_contexts: list[DocContext]) -> str:
    parts: list[str] = []
    for i, dc in enumerate(doc_contexts, start=1):
        parts.append(f"=== INFORME {i} ===")
        parts.append(f"doc_id: {dc.doc_id}")
        parts.append(f"temporada: {dc.temporada}")
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
            parts.append(
                f"[chunk {ch.get('chunk_index')} | section {ch.get('section_norm') or ''}]"
            )
            parts.append(text)
        parts.append("")
    return "\n".join(parts).strip() or "SIN_CONTEXTO_CER"


def _infer_scenario_and_report_options_from_text(
    text: str,
    doc_contexts: list[DocContext],
) -> tuple[str, list[dict[str, Any]]]:
    normalized = _normalize_text(text)
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    options: list[dict[str, Any]] = []

    if (
        "te gustaria" in normalized
        and "producto" in normalized
        and "sag" in normalized
    ):
        scenario = "no_cer"
    elif "no hemos testeado" in normalized and "pero" in normalized:
        scenario = "cross_crop"
    elif "en el cer hemos testeado" in normalized or "hemos testeado productos" in normalized:
        scenario = "direct"
    else:
        scenario = "none"

    for line in lines:
        if not line.startswith("•"):
            continue
        content = line.lstrip("•").strip()
        if not content:
            continue

        # Siempre que haya "label: productos", usamos ambos lados.
        if ":" in content:
            label = content.split(":", 1)[0].strip(" .")
            after_colon = content.split(":", 1)[1].strip()
            products = [item.strip(" .") for item in after_colon.split(",") if item.strip(" .")]
            options.append(_build_report_option(label, products, doc_contexts))
            continue

        label = content.split(":", 1)[0].strip(" .")
        options.append(_build_report_option(label, [label], doc_contexts))

    unique_options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in options:
        key = _normalize_text(str(option.get("label") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        unique_options.append(option)

    return scenario, unique_options


def _build_report_option(
    label: str,
    products: list[str],
    doc_contexts: list[DocContext],
) -> dict[str, Any]:
    label_norm = _normalize_text(label)
    label_species_norm, label_temporada_norm = _extract_species_and_season_from_label(label)
    product_norms = {
        _normalize_text(_limpiar_producto_en_item(p))
        for p in products
        if _normalize_text(_limpiar_producto_en_item(p))
    }
    doc_ids: list[str] = []

    if label_species_norm and label_temporada_norm:
        strong_matches: list[str] = []
        for dc in doc_contexts:
            species_norm = _normalize_text(dc.especie)
            season_norm = _normalize_text(dc.temporada)
            product_norm = _normalize_text(dc.producto)
            species_ok = species_norm and (
                species_norm == label_species_norm
                or species_norm in label_species_norm
                or label_species_norm in species_norm
                or bool(_token_roots(species_norm) & _token_roots(label_species_norm))
            )
            season_ok = season_norm and (
                season_norm == label_temporada_norm
                or season_norm in label_temporada_norm
                or label_temporada_norm in season_norm
            )
            product_ok = (not product_norms) or (
                product_norm
                and any(
                    product_norm == p or product_norm in p or p in product_norm
                    for p in product_norms
                )
            )
            if species_ok and season_ok and product_ok and dc.doc_id:
                strong_matches.append(dc.doc_id)
        if strong_matches:
            return {
                "label": label,
                "products": products,
                "doc_ids": sorted({doc_id for doc_id in strong_matches if doc_id}),
            }

    for dc in doc_contexts:
        species_norm = _normalize_text(dc.especie)
        product_norm = _normalize_text(dc.producto)
        if product_norm and any(
            product_norm == p or product_norm in p or p in product_norm
            for p in product_norms
        ):
            doc_ids.append(dc.doc_id)
            continue
        if label_norm and species_norm and label_norm == species_norm:
            doc_ids.append(dc.doc_id)
            continue

    return {
        "label": label,
        "products": products,
        "doc_ids": sorted({doc_id for doc_id in doc_ids if doc_id}),
    }


def _limpiar_producto_en_item(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"\(en\s+[^)]+\)", "", value, flags=re.IGNORECASE).strip()
    return re.sub(r"\s+", " ", value)


def _extract_species_and_season_from_label(label: str) -> tuple[str, str]:
    text = str(label or "").strip()
    if not text:
        return "", ""
    match = re.search(r"\(([^,]+),\s*([^)]+)\)", text)
    if not match:
        return "", ""
    species = _normalize_text(match.group(1))
    season = _normalize_text(match.group(2))
    return species, season


def _token_roots(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", _normalize_text(text))
    roots: set[str] = set()
    for tok in tokens:
        if len(tok) <= 3:
            continue
        root = tok
        if root.endswith("es") and len(root) > 4:
            root = root[:-2]
        elif root.endswith("s") and len(root) > 4:
            root = root[:-1]
        roots.add(root)
    return roots


def _serialize_doc_contexts(doc_contexts: list[DocContext]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dc in doc_contexts:
        out.append(
            {
                "doc_id": dc.doc_id,
                "pdf_filename": dc.pdf_filename,
                "temporada": dc.temporada,
                "cliente": dc.cliente,
                "producto": dc.producto,
                "especie": dc.especie,
                "variedad": dc.variedad,
                "comuna": dc.comuna,
                "localidad": dc.localidad,
                "region": dc.region,
                "ubicacion": dc.ubicacion,
                "chunks": [dict(ch) for ch in dc.chunks],
            }
        )
    return out


def _deserialize_doc_contexts(items: list[dict[str, Any]]) -> list[DocContext]:
    out: list[DocContext] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            DocContext(
                doc_id=str(item.get("doc_id") or ""),
                pdf_filename=str(item.get("pdf_filename") or ""),
                temporada=str(item.get("temporada") or ""),
                cliente=str(item.get("cliente") or ""),
                producto=str(item.get("producto") or ""),
                especie=str(item.get("especie") or ""),
                variedad=str(item.get("variedad") or ""),
                comuna=str(item.get("comuna") or ""),
                localidad=str(item.get("localidad") or ""),
                region=str(item.get("region") or ""),
                ubicacion=str(item.get("ubicacion") or ""),
                chunks=[dict(ch) for ch in (item.get("chunks") or []) if isinstance(ch, dict)],
            )
        )
    return out


def _looks_like_problem_query(text: str) -> bool:
    normalized = _normalize_text(text)
    keywords = [
        "plaga",
        "pulgon",
        "enfermedad",
        "problema",
        "que puedo hacer",
        "control",
        "tratamiento",
        "como combatir",
    ]
    return any(k in normalized for k in keywords)


def _last_assistant_message(sesion: SesionChat) -> str:
    for msg in reversed(sesion.mensajes):
        if msg.rol == "assistant":
            return msg.texto
    return ""


def _is_affirmative(text: str) -> bool:
    normalized = _normalize_text(text)
    yes_words = {
        "si",
        "claro",
        "dale",
        "ok",
        "bueno",
        "perfecto",
        "me interesa",
        "quiero",
    }
    return normalized in yes_words or any(
        word in normalized for word in ["si ", "me interesa", "quiero"]
    )


def _is_negative(text: str) -> bool:
    normalized = _normalize_text(text)
    no_words = {
        "no",
        "nop",
        "no gracias",
        "paso",
    }
    return normalized in no_words


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _generate_conversational_followup_response(
    last_question: str,
    last_assistant_message: str,
    user_message: str,
    offered_reports: list[dict[str, Any]],
    doc_contexts: list[DocContext],
    settings: Settings,
    progress_callback: Callable[[str], None] | None = None,
) -> str:
    logger.info("💬 Follow-up | respondiendo con contexto conversacional ya disponible...")
    if progress_callback:
        progress_callback("Preparando respuesta de seguimiento...")
    prompt = _build_followup_chat_prompt(
        last_question=last_question,
        last_assistant_message=last_assistant_message,
        user_message=user_message,
        offered_reports=offered_reports,
        context_block=_build_context_block(doc_contexts),
    )
    try:
        text = (
            generate_answer(
                prompt,
                settings,
                system_instruction="",
                profile="complex",
                require_complete=True,
            )
            or ""
        ).strip()
    except Exception:
        text = ""
    if text:
        return text
    return (
        "Si quieres, te puedo detallar cualquiera de estos informes:\n"
        f"{render_report_options(offered_reports)}"
    )


def _es_pregunta_sobre_contexto_actual(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    signals = (
        "dosis",
        "cuanto",
        "correcta",
        "aplicar",
        "aplicacion",
        "funciona",
        "resultado",
        "fitotoxic",
        "frecuencia",
        "cuantas veces",
        "y eso",
        "sirve",
        "como fue",
    )
    return any(token in normalized for token in signals)


def _filtrar_hits_sag_por_producto(
    hits: list[dict[str, Any]],
    product_hint: str,
) -> list[dict[str, Any]]:
    hint = _normalize_text(product_hint)
    if not hint:
        return hits

    filtered: list[dict[str, Any]] = []
    for hit in hits:
        payload = hit.get("payload") or {}
        nombre = _normalize_text(
            str(
                payload.get("nombre_comercial")
                or payload.get("producto_nombre_comercial")
                or ""
            )
        )
        if not nombre:
            continue
        if hint in nombre or nombre in hint:
            filtered.append(hit)

    if filtered:
        return filtered
    return hits


def _filtrar_hits_sag_por_consulta(
    hits: list[dict[str, Any]],
    *,
    query_text: str,
    user_message: str,
    product_hint: str,
) -> list[dict[str, Any]]:
    if not hits:
        return hits
    query_norm = _normalize_text(f"{query_text} {user_message}")
    product_norm = _normalize_text(product_hint)

    ingredient = _extract_ingredient_hint_from_text(query_norm)
    objective = _extract_objective_hint_from_text(query_norm)
    cultivo = _extract_crop_hint_from_text(query_norm)
    generic_token = ""
    if not ingredient and not objective and not cultivo and not product_norm:
        tokens = _meaningful_tokens(query_norm)
        if tokens:
            generic_token = max(tokens, key=len)

    filtered = hits
    if ingredient:
        by_ingredient = _filter_sag_hits_by_field(filtered, ingredient, field="ingredient")
        logger.info(
            "🎯 SAG | filtro ingrediente=%s | antes=%s | despues=%s",
            ingredient,
            len(filtered),
            len(by_ingredient),
        )
        # No estricto: prioriza hits que sí matchean ingrediente,
        # pero conserva el resto para que Gemini decida con contexto.
        if by_ingredient:
            filtered = _merge_hits_by_id(by_ingredient, filtered)
    if objective:
        by_objective = _filter_sag_hits_by_field(filtered, objective, field="objective")
        if by_objective:
            logger.info(
                "🎯 SAG | filtro objetivo=%s | antes=%s | despues=%s",
                objective,
                len(filtered),
                len(by_objective),
            )
            filtered = by_objective
    if cultivo:
        by_crop = _filter_sag_hits_by_field(filtered, cultivo, field="crop")
        if by_crop:
            logger.info(
                "🎯 SAG | filtro cultivo=%s | antes=%s | despues=%s",
                cultivo,
                len(filtered),
                len(by_crop),
            )
            filtered = by_crop
    if generic_token:
        by_generic_obj = _filter_sag_hits_by_field(filtered, generic_token, field="objective")
        by_generic_ing = _filter_sag_hits_by_field(filtered, generic_token, field="ingredient")
        merged = by_generic_obj + [hit for hit in by_generic_ing if hit not in by_generic_obj]
        if merged:
            logger.info(
                "🎯 SAG | filtro genérico=%s | antes=%s | despues=%s",
                generic_token,
                len(filtered),
                len(merged),
            )
            filtered = merged

    # Si la consulta viene por producto, priorizamos coincidencia exacta de nombre comercial.
    if product_norm:
        product_filtered = _filtrar_hits_sag_por_producto(filtered, product_hint)
        if product_filtered:
            filtered = product_filtered

    return filtered


def _filter_sag_hits_by_field(
    hits: list[dict[str, Any]],
    needle: str,
    *,
    field: str,
) -> list[dict[str, Any]]:
    target = _normalize_text(needle)
    if not target:
        return hits
    tokens = _meaningful_tokens(target)

    def _fields(payload: dict[str, Any]) -> str:
        if field == "ingredient":
            composition = _extract_sag_composition(payload)
            group = str(payload.get("grupo_quimico") or "")
            producto = str(payload.get("nombre_comercial") or "")
            producto_alt = str(payload.get("producto_nombre_comercial") or "")
            producto_id = str(payload.get("producto_id") or "")
            return _normalize_text(
                " ".join(
                    [
                        composition,
                        group,
                        producto,
                        producto_alt,
                        producto_id,
                    ]
                )
            )
        if field == "objective":
            return _normalize_text(
                " ".join(
                    [
                        str(payload.get("objetivo") or ""),
                        str(payload.get("objetivo_normalizado") or ""),
                        str(payload.get("categoria_objetivo") or ""),
                    ]
                )
            )
        if field == "crop":
            return _normalize_text(str(payload.get("cultivo") or ""))
        return ""

    out: list[dict[str, Any]] = []
    for hit in hits:
        payload = hit.get("payload") or {}
        haystack = _fields(payload)
        if not haystack:
            continue
        if target in haystack or haystack in target:
            out.append(hit)
            continue
        if tokens and any(token in haystack for token in tokens):
            out.append(hit)
    return out


def _extract_ingredient_hint_from_text(text: str) -> str:
    patterns = (
        r"\b(?:contiene|contienen|contengan|tiene|tengan|tenga|con|a base de)\s+([a-z0-9][a-z0-9\s\-]{2,80})",
        r"\b(?:ingrediente activo|ingredientes activos|composicion|sustancia activa)\s*(?:de)?\s*([a-z0-9][a-z0-9\s\-]{2,80})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = _sanitize_hint_phrase(match.group(1))
            if not candidate:
                continue
            generic_noise = {
                "dosis",
                "dosificacion",
                "dosificación",
                "cultivo",
                "cultivos",
                "objetivo",
                "objetivos",
                "plaga",
                "plagas",
                "producto",
                "productos",
                "registro",
                "registros",
                "sag",
            }
            if candidate in generic_noise:
                continue
            return candidate
    return ""


def _extract_objective_hint_from_text(text: str) -> str:
    patterns = (
        r"\b(?:para|contra|tratar|tratan|trata|traten|control(?:ar|an|en)?|combate(?:n|r)?)\s+([a-z0-9][a-z0-9\s\-]{2,80})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = _sanitize_hint_phrase(match.group(1))
            # Evitar capturar frases de ingrediente.
            if any(token in candidate for token in ("contiene", "ingrediente", "composicion")):
                continue
            return candidate
    return ""


def _extract_crop_hint_from_text(text: str) -> str:
    patterns = (
        r"\b(?:en|para)\s+(?:el|la|los|las)?\s*([a-z0-9][a-z0-9\s\-]{2,40})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        candidate = _sanitize_hint_phrase(match.group(1))
        if any(token in candidate for token in ("sag", "registro", "producto", "productos")):
            continue
        return candidate
    return ""


def _sanitize_hint_phrase(text: str) -> str:
    candidate = re.sub(r"\s+", " ", str(text or "")).strip(" .,:;")
    stop_chunks = (
        " con ",
        " en ",
        " y ",
        " que ",
        " del ",
        " de ",
    )
    lowered = f" {candidate.lower()} "
    cut_idx = len(lowered)
    for chunk in stop_chunks:
        idx = lowered.find(chunk)
        if idx != -1:
            cut_idx = min(cut_idx, idx)
    if cut_idx < len(lowered):
        candidate = lowered[:cut_idx].strip()
    return re.sub(r"\s+", " ", candidate).strip(" .,:;")


def _meaningful_tokens(text: str) -> list[str]:
    stopwords = {
        "producto",
        "productos",
        "registrado",
        "registrados",
        "registro",
        "sag",
        "para",
        "contra",
        "con",
        "sin",
        "del",
        "de",
        "la",
        "el",
        "los",
        "las",
        "que",
        "cual",
        "cuales",
        "tiene",
        "tienen",
    }
    tokens = [t for t in re.findall(r"[a-z0-9]+", _normalize_text(text)) if len(t) >= 4]
    return [t for t in tokens if t not in stopwords]


def _build_sag_response_from_hits(
    hits: list[dict[str, Any]],
    *,
    query_text: str,
    user_message: str,
) -> str:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in hits:
        payload = hit.get("payload") or {}
        nombre = str(
            payload.get("nombre_comercial") or payload.get("producto_nombre_comercial") or "Producto sin nombre"
        ).strip()
        auth = str(payload.get("autorizacion_sag_numero_normalizado") or "N/D").strip()
        key = (_normalize_text(nombre), auth)
        if key not in grouped:
            grouped[key] = {
                "nombre": nombre,
                "auth": auth,
                "tipo": str(
                    payload.get("tipo")
                    or payload.get("tipo_formulacion")
                    or payload.get("formulacion")
                    or payload.get("formulación")
                    or "N/D"
                ).strip(),
                "composicion": _extract_sag_composition(payload),
                "cultivos": set(),
                "objetivos": set(),
                "dosis": set(),
            }
        g = grouped[key]
        composicion_hit = _extract_sag_composition(payload)
        if composicion_hit and composicion_hit != "N/D" and g["composicion"] == "N/D":
            g["composicion"] = composicion_hit
        tipo_hit = str(
            payload.get("tipo")
            or payload.get("tipo_formulacion")
            or payload.get("formulacion")
            or payload.get("formulación")
            or ""
        ).strip()
        if tipo_hit and g["tipo"] == "N/D":
            g["tipo"] = tipo_hit
        cultivo = str(payload.get("cultivo") or "").strip()
        objetivo = str(payload.get("objetivo") or "").strip()
        dosis = re.sub(r"\s+", " ", str(payload.get("dosis_texto") or "")).strip()
        if cultivo:
            g["cultivos"].add(cultivo)
        if objetivo:
            g["objetivos"].add(objetivo)
        if dosis:
            g["dosis"].add(dosis)

    rows = list(grouped.values())
    if not rows:
        return "No encontré productos del SAG con coincidencia directa para tu consulta."
    rows.sort(key=lambda item: _normalize_text(str(item.get("nombre") or "")))

    intro = (
        f"Aquí tienes los productos del SAG que coinciden con tu consulta ({len(rows)} encontrados):"
    )
    lines: list[str] = [intro]
    for idx, row in enumerate(rows, start=1):
        cultivos = _render_clean_values(
            row["cultivos"],
            default="N/D",
            max_items=8,
            max_value_len=70,
        )
        objetivos = _render_clean_values(
            row["objetivos"],
            default="N/D",
            max_items=6,
            max_value_len=120,
        )
        dosis = _render_clean_values(
            row["dosis"],
            default="N/D",
            max_items=8,
            max_value_len=80,
            sep="; ",
        )
        lines.append(f"{idx}. {row['nombre']}")
        lines.append(f"• Composición / I.A.: {row['composicion']}")
        lines.append(f"• Tipo: {row['tipo']}")
        lines.append(f"• Cultivo: {cultivos}")
        lines.append(f"• Objetivo: {objetivos}")
        lines.append(f"• Dosis reportada: {dosis}")
        lines.append(f"• N° Autorización: {row['auth']}")

    response = "\n".join(lines).strip()
    # Salvaguarda para evitar respuestas excesivas; Telegram permite chunking.
    if len(response) > 30000:
        response = response[:30000].rstrip() + "\n\n[Resultado truncado por longitud]"
    return response


def _render_clean_values(
    values: set[str],
    *,
    default: str,
    max_items: int,
    max_value_len: int,
    sep: str = ", ",
) -> str:
    if not values:
        return default
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in sorted(values):
        text = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;")
        if not text:
            continue
        text_norm = _normalize_text(text)
        if any(token in text_norm for token in ("telefono", "emergencia", "seguridad sin codigos")):
            continue
        if len(text) > max_value_len:
            text = text[:max_value_len].rstrip() + "..."
        key = _normalize_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return sep.join(cleaned) if cleaned else default


def _build_sag_context_block(hits: list[dict[str, Any]]) -> str:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in hits:
        payload = hit.get("payload") or {}
        producto = str(
            payload.get("nombre_comercial")
            or payload.get("producto_nombre_comercial")
            or "Producto sin nombre"
        ).strip()
        tipo = str(
            payload.get("tipo")
            or payload.get("tipo_formulacion")
            or payload.get("formulacion")
            or payload.get("formulación")
            or "N/D"
        ).strip()
        autorizacion = str(payload.get("autorizacion_sag_numero_normalizado") or "N/D").strip()
        cultivo = str(payload.get("cultivo") or "N/D").strip()
        objetivo = str(payload.get("objetivo") or "N/D").strip()
        dosis = re.sub(r"\s+", " ", str(payload.get("dosis_texto") or "N/D")).strip()
        composicion = _extract_sag_composition(payload)

        # Clave principal por autorización SAG; producto como respaldo para N/D.
        key = (_normalize_text(autorizacion), _normalize_text(producto))
        if key not in grouped:
            grouped[key] = {
                "producto": producto,
                "autorizacion": autorizacion,
                "tipos": set(),
                "composiciones": set(),
                "cultivos": set(),
                "objetivos": set(),
                "dosis": set(),
            }
        row = grouped[key]
        if tipo and tipo != "N/D":
            row["tipos"].add(tipo)
        if composicion and composicion != "N/D":
            row["composiciones"].add(composicion)
        if cultivo and cultivo != "N/D":
            row["cultivos"].add(cultivo)
        if objetivo and objetivo != "N/D":
            row["objetivos"].add(objetivo)
        if dosis and dosis != "N/D":
            row["dosis"].add(dosis)

    lines: list[str] = []
    for row in grouped.values():
        tipo_txt = _render_clean_values(
            row["tipos"],
            default="N/D",
            max_items=5,
            max_value_len=60,
        )
        composicion_txt = _render_clean_values(
            row["composiciones"],
            default="N/D",
            max_items=4,
            max_value_len=120,
            sep=" | ",
        )
        cultivo_txt = _render_clean_values(
            row["cultivos"],
            default="N/D",
            max_items=10,
            max_value_len=60,
        )
        objetivo_txt = _render_clean_values(
            row["objetivos"],
            default="N/D",
            max_items=10,
            max_value_len=110,
        )
        dosis_txt = _render_clean_values(
            row["dosis"],
            default="N/D",
            max_items=10,
            max_value_len=80,
            sep="; ",
        )
        lines.append(
            f"- producto: {row['producto']} | composicion: {composicion_txt} | tipo: {tipo_txt} | autorizacion: {row['autorizacion']} | cultivo: {cultivo_txt} | objetivo: {objetivo_txt} | dosis: {dosis_txt}"
        )
    return "\n".join(lines) if lines else "- sin datos sag"


def _count_sag_consolidated_products(hits: list[dict[str, Any]]) -> int:
    keys: set[tuple[str, str]] = set()
    for hit in hits:
        payload = hit.get("payload") or {}
        producto = str(
            payload.get("nombre_comercial")
            or payload.get("producto_nombre_comercial")
            or "producto sin nombre"
        ).strip()
        auth = str(payload.get("autorizacion_sag_numero_normalizado") or "N/D").strip()
        keys.add((_normalize_text(auth), _normalize_text(producto)))
    return len(keys)


def _build_sag_context_block_compact(hits: list[dict[str, Any]]) -> str:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in hits:
        payload = hit.get("payload") or {}
        producto = str(
            payload.get("nombre_comercial")
            or payload.get("producto_nombre_comercial")
            or "Producto sin nombre"
        ).strip()
        auth = str(payload.get("autorizacion_sag_numero_normalizado") or "N/D").strip()
        key = (_normalize_text(auth), _normalize_text(producto))
        if key not in grouped:
            grouped[key] = {
                "producto": producto,
                "auth": auth,
                "composiciones": set(),
                "tipos": set(),
            }
        row = grouped[key]
        comp = _extract_sag_composition(payload)
        if comp and comp != "N/D":
            row["composiciones"].add(comp)
        tipo = str(
            payload.get("tipo")
            or payload.get("tipo_formulacion")
            or payload.get("formulacion")
            or payload.get("formulación")
            or ""
        ).strip()
        if tipo:
            row["tipos"].add(tipo)

    ordered = sorted(grouped.values(), key=lambda item: _normalize_text(item["producto"]))
    lines: list[str] = []
    for row in ordered:
        comp_txt = _render_clean_values(
            row["composiciones"],
            default="N/D",
            max_items=2,
            max_value_len=80,
            sep=" | ",
        )
        tipo_txt = _render_clean_values(
            row["tipos"],
            default="N/D",
            max_items=1,
            max_value_len=45,
        )
        lines.append(
            f"- producto: {row['producto']} | autorizacion: {row['auth']} | composicion: {comp_txt} | tipo: {tipo_txt}"
        )
    return "\n".join(lines) if lines else "- sin datos sag"


def _extract_sag_composition(payload: dict[str, Any]) -> str:
    keys = (
        "composicion",
        "composición",
        "composicion_quimica",
        "composición_química",
        "ingrediente_activo",
        "ingredientes_activos",
        "ingrediente",
        "ingredientes",
        "sustancia_activa",
        "sustancias_activas",
        "componente_activo",
        "componentes_activos",
        "ia",
        "i_a",
        "active_ingredient",
        "active_ingredients",
    )
    parts: list[str] = []
    for key in keys:
        value = payload.get(key)
        text = _payload_value_to_text(value)
        if text and _looks_like_valid_composition_text(text):
            parts.append(text)
    if not parts:
        group = _payload_value_to_text(payload.get("grupo_quimico"))
        if group and _looks_like_valid_composition_text(group):
            parts.append(group)
    if not parts:
        excel_pid = str(payload.get("producto_id") or "").strip()
        excel_comp = get_product_composition("", excel_pid)
        if excel_comp and _looks_like_valid_composition_text(excel_comp):
            parts.append(excel_comp)
    if not parts:
        for key, value in payload.items():
            key_norm = _normalize_text(str(key))
            if not key_norm:
                continue
            if not any(token in key_norm for token in ("ingred", "compos", "sustancia", "active")):
                continue
            if any(
                noise in key_norm
                for noise in (
                    "telefono",
                    "telefon",
                    "correo",
                    "email",
                    "emergencia",
                    "seguridad",
                    "advertencia",
                    "contacto",
                )
            ):
                continue
            text = _payload_value_to_text(value)
            if text and _looks_like_valid_composition_text(text):
                parts.append(text)
    if not parts:
        return "N/D"
    cleaned_parts: list[str] = []
    seen_parts: set[str] = set()
    for p in parts:
        p_clean = re.sub(r"\s+", " ", p).strip()
        key = _normalize_text(p_clean)
        if not key or key in seen_parts:
            continue
        seen_parts.add(key)
        cleaned_parts.append(p_clean)
    combined = " | ".join(cleaned_parts)
    combined = re.sub(r"\s+", " ", combined).strip()
    if len(combined) > 250:
        return combined[:250].rstrip() + "..."
    return combined


def _looks_like_valid_composition_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if any(
        token in normalized
        for token in ("telefono", "emergencia", "seguridad", "advertencia", "contacto")
    ):
        return False
    if re.search(r"\+?\d[\d\-\s]{7,}", normalized):
        return False
    return True


def _payload_value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    if isinstance(value, list):
        parts = [_payload_value_to_text(item) for item in value]
        parts = [p for p in parts if p]
        return ", ".join(parts)
    if isinstance(value, dict):
        parts = [_payload_value_to_text(v) for v in value.values()]
        parts = [p for p in parts if p]
        return ", ".join(parts)
    return str(value).strip()


def _parece_pedir_ensayo_especifico(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if re.search(r"\bensayo\s+\d+\b", normalized):
        return True
    return any(
        token in normalized
        for token in ("detalle", "mas informacion", "más información", "ampliar")
    )
