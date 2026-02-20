"""Lógica de respuestas SAG: búsqueda, filtrado, contexto y generación."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Settings
from ..providers.llm import generate_answer
from ..rag.retriever import (
    retrieve_sag,
    retrieve_sag_rows_by_ids,
    retrieve_sag_rows_for_products,
)
from ..sources.sag_csv_lookup import (
    build_csv_query_hints_block,
    find_products_by_query,
    find_products_by_ingredient,
    find_products_by_objective,
    get_product_composition,
)
from .flow_helpers import (
    load_prompt_template,
    merge_hits_by_id,
    meaningful_tokens,
    normalize_text,
)

logger = logging.getLogger(__name__)
RESPUESTA_SAG_PROMPT_FILE = "respuesta_sag.md"


@dataclass(slots=True)
class SagFlowResult:
    handled: bool
    response: str = ""
    rag_tag: str = "sag"
    sources: list[str] = field(default_factory=list)
    router_context: str = ""


# ---------------------------------------------------------------------------
# Punto de entrada principal SAG
# ---------------------------------------------------------------------------

def generate_sag_response(
    query: str,
    settings: Settings,
    *,
    user_message: str = "",
    product_hint: str = "",
    progress_callback: Callable[[str], None] | None = None,
) -> SagFlowResult:
    normalized_query = (query or "").strip() or "consulta de productos en base de datos de etiquetas"
    effective_user_message = user_message or normalized_query

    if progress_callback:
        progress_callback("Estoy revisando la base de datos de etiquetas...")

    combined_query_norm = normalize_text(f"{normalized_query} {effective_user_message}")
    ingredient_hint = _extract_ingredient_hint(combined_query_norm)
    objective_hint = _extract_objective_hint(combined_query_norm)

    base_top_k = max(1, int(settings.rag_sag_top_k))
    retrieval_top_k = max(base_top_k * 2, 20)

    if progress_callback:
        progress_callback("Estoy extrayendo los productos de etiquetas que coinciden con tu consulta...")

    # 1) CSV inicial con pregunta del usuario (señales para refined query).
    csv_pre_product_ids, csv_pre_auths, _csv_pre_records = find_products_by_query(
        settings.sag_csv_path, normalized_query, limit=max(base_top_k * 20, 120),
    )
    csv_pre_hints = build_csv_query_hints_block(
        settings.sag_csv_path, normalized_query, limit=12,
    )
    csv_pre_ids_text = ", ".join(sorted(csv_pre_product_ids)[:80]) if csv_pre_product_ids else "sin_ids"
    csv_pre_auths_text = ", ".join(sorted(csv_pre_auths)[:80]) if csv_pre_auths else "sin_auths"
    retrieval_context = (
        f"{normalized_query}\n{effective_user_message}\n"
        "SEÑALES_CSV_INICIALES:\n"
        f"{csv_pre_hints}\n"
        f"CSV_PRODUCT_IDS: {csv_pre_ids_text}\n"
        f"CSV_AUTHS: {csv_pre_auths_text}"
    )

    # Búsqueda semántica inicial
    seed_hits = retrieve_sag(
        normalized_query,
        settings=settings,
        top_k=retrieval_top_k,
        conversation_context=retrieval_context,
    )

    # Filtrado progresivo
    filtered_seed_hits = _filtrar_hits_por_consulta(
        seed_hits,
        query_text=normalized_query,
        user_message=effective_user_message,
        product_hint=product_hint,
    )
    if ingredient_hint:
        ingredient_seed = _filter_hits_by_field(seed_hits, ingredient_hint, field="ingredient")
        if ingredient_seed:
            filtered_seed_hits = ingredient_seed
    filtered_seed_hits = _filtrar_hits_por_producto(filtered_seed_hits, product_hint)

    # Enriquecimiento con filas completas
    seed_for_enrich = filtered_seed_hits or seed_hits
    sag_hits = retrieve_sag_rows_for_products(
        seed_for_enrich,
        settings,
        max_rows_per_filter=max(base_top_k * 16, 160),
    )
    # Conserva también los hallazgos CSV iniciales en la etapa de consolidación.
    if csv_pre_product_ids or csv_pre_auths:
        pre_csv_rows = retrieve_sag_rows_by_ids(
            settings=settings,
            product_ids=csv_pre_product_ids,
            auth_numbers=csv_pre_auths,
            max_rows=max(base_top_k * 220, 4500),
        )
        if pre_csv_rows:
            sag_hits = merge_hits_by_id(sag_hits, pre_csv_rows)

    # Boost de recall con CSV por objetivo
    sag_hits = _boost_with_csv_objective(sag_hits, objective_hint, settings, base_top_k)
    # Boost de recall con CSV por ingrediente
    sag_hits = _boost_with_csv_ingredient(sag_hits, ingredient_hint, settings, base_top_k)

    # Filtrado post-enriquecimiento
    sag_hits = _post_enrich_filter(sag_hits, ingredient_hint, objective_hint)
    # 4) Confirmación final CSV + RAG para formar la lista.
    sag_hits = _confirm_hits_with_csv(
        sag_hits=sag_hits,
        normalized_query=normalized_query,
        effective_user_message=effective_user_message,
        settings=settings,
        base_top_k=base_top_k,
    )

    if not sag_hits:
        return SagFlowResult(
            handled=True,
            response="No encontré productos en la base de datos de etiquetas con coincidencia directa para tu consulta.",
            router_context="sin resultados SAG para la consulta",
        )

    # Construcción de contexto y generación de respuesta
    response = _generate_response_text(
        sag_hits, normalized_query, effective_user_message, product_hint, settings, progress_callback,
    )
    response = _prepend_standard_notice(response)
    return SagFlowResult(
        handled=True,
        response=response,
        router_context=_build_router_context_snapshot(sag_hits),
    )


# ---------------------------------------------------------------------------
# Boost de recall con CSV
# ---------------------------------------------------------------------------

def _boost_with_csv_objective(
    sag_hits: list[dict[str, Any]],
    objective_hint: str,
    settings: Settings,
    base_top_k: int,
) -> list[dict[str, Any]]:
    if not objective_hint:
        return sag_hits
    csv_product_ids, csv_auths = find_products_by_objective(settings.sag_csv_path, objective_hint)
    if not csv_product_ids and not csv_auths:
        return sag_hits
    csv_rows = retrieve_sag_rows_by_ids(
        settings=settings,
        product_ids=csv_product_ids,
        auth_numbers=csv_auths,
        max_rows=max(base_top_k * 220, 4500),
    )
    if csv_rows:
        sag_hits = merge_hits_by_id(sag_hits, csv_rows)
        logger.info(
            "📎 SAG+CSV | objetivo=%s | product_ids=%s | auths=%s | rows=%s | total=%s",
            objective_hint, len(csv_product_ids), len(csv_auths), len(csv_rows), len(sag_hits),
        )
    return sag_hits


def _boost_with_csv_ingredient(
    sag_hits: list[dict[str, Any]],
    ingredient_hint: str,
    settings: Settings,
    base_top_k: int,
) -> list[dict[str, Any]]:
    if not ingredient_hint:
        return sag_hits
    csv_product_ids, csv_auths = find_products_by_ingredient(settings.sag_csv_path, ingredient_hint)
    if not csv_product_ids and not csv_auths:
        return sag_hits
    csv_rows = retrieve_sag_rows_by_ids(
        settings=settings,
        product_ids=csv_product_ids,
        auth_numbers=csv_auths,
        max_rows=max(base_top_k * 24, 240),
    )
    if csv_rows:
        sag_hits = merge_hits_by_id(sag_hits, csv_rows)
        logger.info(
            "📎 SAG+CSV | ingrediente=%s | product_ids=%s | auths=%s | rows=%s | total=%s",
            ingredient_hint, len(csv_product_ids), len(csv_auths), len(csv_rows), len(sag_hits),
        )
    return sag_hits


def _post_enrich_filter(
    sag_hits: list[dict[str, Any]],
    ingredient_hint: str,
    objective_hint: str,
) -> list[dict[str, Any]]:
    if ingredient_hint:
        filtered = _filter_hits_by_field(sag_hits, ingredient_hint, field="ingredient")
        logger.info(
            "🎯 SAG | post-enrich ingrediente=%s | antes=%s | despues=%s",
            ingredient_hint, len(sag_hits), len(filtered),
        )
        if filtered:
            return filtered
    if objective_hint and not ingredient_hint:
        filtered = _filter_hits_by_field(sag_hits, objective_hint, field="objective")
        logger.info(
            "🎯 SAG | post-enrich objetivo=%s | antes=%s | despues=%s",
            objective_hint, len(sag_hits), len(filtered),
        )
        if filtered:
            return filtered
    return sag_hits


def _confirm_hits_with_csv(
    *,
    sag_hits: list[dict[str, Any]],
    normalized_query: str,
    effective_user_message: str,
    settings: Settings,
    base_top_k: int,
) -> list[dict[str, Any]]:
    if not sag_hits:
        return sag_hits

    query_parts: list[str] = [normalized_query, effective_user_message]
    for hit in sag_hits[: max(8, base_top_k * 2)]:
        payload = hit.get("payload") or {}
        query_parts.extend(
            [
                str(payload.get("producto_id") or "").strip(),
                str(payload.get("nombre_comercial") or payload.get("producto_nombre_comercial") or "").strip(),
                str(payload.get("autorizacion_sag_numero_normalizado") or "").strip(),
                str(payload.get("objetivo") or "").strip(),
                str(payload.get("objetivo_normalizado") or "").strip(),
                str(payload.get("ingredientes") or "").strip(),
            ]
        )
    csv_query = " ".join(part for part in query_parts if part)
    csv_product_ids, csv_auths, _records = find_products_by_query(
        settings.sag_csv_path, csv_query, limit=max(base_top_k * 30, 200),
    )
    if not csv_product_ids and not csv_auths:
        return sag_hits

    confirmed_rows = retrieve_sag_rows_by_ids(
        settings=settings,
        product_ids=csv_product_ids,
        auth_numbers=csv_auths,
        max_rows=max(base_top_k * 220, 4500),
    )
    if not confirmed_rows:
        return sag_hits

    merged = merge_hits_by_id(sag_hits, confirmed_rows)
    confirmed = [
        hit for hit in merged
        if _hit_matches_csv_confirmation(
            hit,
            csv_product_ids=csv_product_ids,
            csv_auths=csv_auths,
        )
    ]
    if confirmed:
        logger.info(
            "✅ SAG confirmación CSV | ids=%s | auths=%s | antes=%s | despues=%s",
            len(csv_product_ids), len(csv_auths), len(sag_hits), len(confirmed),
        )
        return confirmed
    return merged


def _hit_matches_csv_confirmation(
    hit: dict[str, Any],
    *,
    csv_product_ids: set[str],
    csv_auths: set[str],
) -> bool:
    payload = hit.get("payload") or {}
    pid = normalize_text(str(payload.get("producto_id") or "").strip())
    auth = normalize_text(str(payload.get("autorizacion_sag_numero_normalizado") or "").strip())
    return (pid and pid in csv_product_ids) or (auth and auth in csv_auths)


# ---------------------------------------------------------------------------
# Generación de texto de respuesta
# ---------------------------------------------------------------------------

def _generate_response_text(
    sag_hits: list[dict[str, Any]],
    normalized_query: str,
    effective_user_message: str,
    product_hint: str,
    settings: Settings,
    progress_callback: Callable[[str], None] | None,
) -> str:
    consolidated_count = _count_consolidated_products(sag_hits)
    compact = consolidated_count > 25
    context_block = (
        _build_context_block_compact(sag_hits) if compact else _build_context_block(sag_hits)
    )
    logger.info(
        "🧱 SAG contexto | productos=%s | modo=%s | chars=%s",
        consolidated_count, "compacto" if compact else "detallado", len(context_block),
    )
    prompt = _build_response_prompt(
        user_message=effective_user_message,
        query=normalized_query,
        product_hint=product_hint,
        context_block=context_block,
        csv_hints_block=build_csv_query_hints_block(
            settings.sag_csv_path, f"{normalized_query} {effective_user_message}",
        ),
    )
    if progress_callback:
        progress_callback("Estoy redactando la respuesta con los datos extraídos...")
    response = (
        generate_answer(prompt, settings, system_instruction="", profile="complex", require_complete=True) or ""
    ).strip()
    if not response:
        response = _build_fallback_response(sag_hits, normalized_query, effective_user_message)
    return response


def _prepend_standard_notice(response: str) -> str:
    text = (response or "").strip()
    if not text:
        return text
    if "la siguiente informacion es la que estos productos presentan en sus etiquetas" in normalize_text(text):
        return text
    notice = (
        "La siguiente información es la que estos productos presentan en sus etiquetas.\n"
        "No puedo confirmarte la eficacia de estos productos para lo que dicen hacer.\n"
        "Solo si el producto ha sido ensayado en CER puedo darte información de cómo se desempeñó;\n"
        "por eso mismo tampoco puedo decirte cuál de estos productos es mejor que otro."
    )
    return f"{notice}\n\n{text}"


# ---------------------------------------------------------------------------
# Filtrado de hits SAG
# ---------------------------------------------------------------------------

def _filtrar_hits_por_producto(
    hits: list[dict[str, Any]],
    product_hint: str,
) -> list[dict[str, Any]]:
    hint = normalize_text(product_hint)
    if not hint:
        return hits
    filtered = [
        hit for hit in hits
        if _match_product_name(hit, hint)
    ]
    return filtered or hits


def _match_product_name(hit: dict[str, Any], hint_norm: str) -> bool:
    payload = hit.get("payload") or {}
    nombre = normalize_text(
        str(payload.get("nombre_comercial") or payload.get("producto_nombre_comercial") or "")
    )
    return bool(nombre) and (hint_norm in nombre or nombre in hint_norm)


def _filtrar_hits_por_consulta(
    hits: list[dict[str, Any]],
    *,
    query_text: str,
    user_message: str,
    product_hint: str,
) -> list[dict[str, Any]]:
    if not hits:
        return hits
    query_norm = normalize_text(f"{query_text} {user_message}")
    product_norm = normalize_text(product_hint)

    ingredient = _extract_ingredient_hint(query_norm)
    objective = _extract_objective_hint(query_norm)
    cultivo = _extract_crop_hint(query_norm)
    generic_token = ""
    if not ingredient and not objective and not cultivo and not product_norm:
        tokens = meaningful_tokens(query_norm)
        if tokens:
            generic_token = max(tokens, key=len)

    filtered = hits
    if ingredient:
        by_ingredient = _filter_hits_by_field(filtered, ingredient, field="ingredient")
        logger.info(
            "🎯 SAG | filtro ingrediente=%s | antes=%s | despues=%s",
            ingredient, len(filtered), len(by_ingredient),
        )
        if by_ingredient:
            filtered = merge_hits_by_id(by_ingredient, filtered)
    if objective:
        by_objective = _filter_hits_by_field(filtered, objective, field="objective")
        if by_objective:
            logger.info(
                "🎯 SAG | filtro objetivo=%s | antes=%s | despues=%s",
                objective, len(filtered), len(by_objective),
            )
            filtered = by_objective
    if cultivo:
        by_crop = _filter_hits_by_field(filtered, cultivo, field="crop")
        if by_crop:
            logger.info(
                "🎯 SAG | filtro cultivo=%s | antes=%s | despues=%s",
                cultivo, len(filtered), len(by_crop),
            )
            filtered = by_crop
    if generic_token:
        by_obj = _filter_hits_by_field(filtered, generic_token, field="objective")
        by_ing = _filter_hits_by_field(filtered, generic_token, field="ingredient")
        merged = by_obj + [h for h in by_ing if h not in by_obj]
        if merged:
            logger.info(
                "🎯 SAG | filtro genérico=%s | antes=%s | despues=%s",
                generic_token, len(filtered), len(merged),
            )
            filtered = merged
    if product_norm:
        product_filtered = _filtrar_hits_por_producto(filtered, product_hint)
        if product_filtered:
            filtered = product_filtered
    return filtered


def _filter_hits_by_field(
    hits: list[dict[str, Any]],
    needle: str,
    *,
    field: str,
) -> list[dict[str, Any]]:
    target = normalize_text(needle)
    if not target:
        return hits
    tokens = meaningful_tokens(target)

    out: list[dict[str, Any]] = []
    for hit in hits:
        payload = hit.get("payload") or {}
        haystack = _field_text(payload, field)
        if not haystack:
            continue
        if target in haystack or haystack in target:
            out.append(hit)
        elif tokens and any(tok in haystack for tok in tokens):
            out.append(hit)
    return out


def _field_text(payload: dict[str, Any], field: str) -> str:
    if field == "ingredient":
        return normalize_text(
            " ".join([
                _extract_composition(payload),
                str(payload.get("grupo_quimico") or ""),
                str(payload.get("nombre_comercial") or ""),
                str(payload.get("producto_nombre_comercial") or ""),
                str(payload.get("producto_id") or ""),
            ])
        )
    if field == "objective":
        return normalize_text(
            " ".join([
                str(payload.get("objetivo") or ""),
                str(payload.get("objetivo_normalizado") or ""),
                str(payload.get("categoria_objetivo") or ""),
            ])
        )
    if field == "crop":
        return normalize_text(str(payload.get("cultivo") or ""))
    return ""


# ---------------------------------------------------------------------------
# Extracción de hints desde texto de consulta
# ---------------------------------------------------------------------------

def _extract_ingredient_hint(text: str) -> str:
    patterns = (
        r"\b(?:contiene|contienen|contengan|tiene|tengan|tenga|con|a base de)\s+([a-z0-9][a-z0-9\s\-]{2,80})",
        r"\b(?:ingrediente activo|ingredientes activos|composicion|sustancia activa)\s*(?:de)?\s*([a-z0-9][a-z0-9\s\-]{2,80})",
    )
    generic_noise = {
        "dosis", "dosificacion", "dosificación", "cultivo", "cultivos",
        "objetivo", "objetivos", "plaga", "plagas", "producto", "productos",
        "registro", "registros", "sag",
    }
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = _sanitize_hint_phrase(match.group(1))
            if candidate and candidate not in generic_noise:
                return candidate
    return ""


def _extract_objective_hint(text: str) -> str:
    patterns = (
        r"\b(?:para|contra|tratar|tratan|trata|traten|control(?:ar|an|en)?|combate(?:n|r)?)\s+([a-z0-9][a-z0-9\s\-]{2,80})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = _sanitize_hint_phrase(match.group(1))
            if not any(tok in candidate for tok in ("contiene", "ingrediente", "composicion")):
                return candidate
    return ""


def _extract_crop_hint(text: str) -> str:
    patterns = (
        r"\b(?:en|para)\s+(?:el|la|los|las)?\s*([a-z0-9][a-z0-9\s\-]{2,40})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = _sanitize_hint_phrase(match.group(1))
            if not any(tok in candidate for tok in ("sag", "registro", "producto", "productos")):
                return candidate
    return ""


def _sanitize_hint_phrase(text: str) -> str:
    candidate = re.sub(r"\s+", " ", str(text or "")).strip(" .,:;")
    stop_chunks = (" con ", " en ", " y ", " que ", " del ", " de ")
    lowered = f" {candidate.lower()} "
    cut_idx = len(lowered)
    for chunk in stop_chunks:
        idx = lowered.find(chunk)
        if idx != -1:
            cut_idx = min(cut_idx, idx)
    if cut_idx < len(lowered):
        candidate = lowered[:cut_idx].strip()
    return re.sub(r"\s+", " ", candidate).strip(" .,:;")


# ---------------------------------------------------------------------------
# Construcción de contexto SAG para prompts
# ---------------------------------------------------------------------------

def _build_context_block(hits: list[dict[str, Any]]) -> str:
    grouped = _group_hits_by_product(hits, detailed=True)
    lines: list[str] = []
    for row in grouped.values():
        lines.append(
            f"- producto: {row['producto']} | composicion: {_render_values(row['composiciones'], max_items=4, max_len=120, sep=' | ')} "
            f"| tipo: {_render_values(row['tipos'], max_items=5, max_len=60)} "
            f"| autorizacion: {row['autorizacion']} "
            f"| cultivo: {_render_values(row['cultivos'], max_items=10, max_len=60)} "
            f"| objetivo: {_render_values(row['objetivos'], max_items=10, max_len=110)} "
            f"| dosis: {_render_values(row['dosis'], max_items=10, max_len=80, sep='; ')}"
        )
    return "\n".join(lines) if lines else "- sin datos de etiquetas"


def _build_context_block_compact(hits: list[dict[str, Any]]) -> str:
    grouped = _group_hits_by_product(hits, detailed=False)
    ordered = sorted(grouped.values(), key=lambda r: normalize_text(r["producto"]))
    lines: list[str] = []
    for row in ordered:
        lines.append(
            f"- producto: {row['producto']} | autorizacion: {row['autorizacion']} "
            f"| composicion: {_render_values(row['composiciones'], max_items=2, max_len=80, sep=' | ')} "
            f"| tipo: {_render_values(row['tipos'], max_items=1, max_len=45)}"
        )
    return "\n".join(lines) if lines else "- sin datos de etiquetas"


def _group_hits_by_product(
    hits: list[dict[str, Any]],
    *,
    detailed: bool,
) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in hits:
        payload = hit.get("payload") or {}
        producto = str(
            payload.get("nombre_comercial") or payload.get("producto_nombre_comercial") or "Producto sin nombre"
        ).strip()
        autorizacion = str(payload.get("autorizacion_sag_numero_normalizado") or "N/D").strip()
        key = (normalize_text(autorizacion), normalize_text(producto))
        if key not in grouped:
            grouped[key] = {
                "producto": producto,
                "autorizacion": autorizacion,
                "tipos": set(),
                "composiciones": set(),
            }
            if detailed:
                grouped[key].update({"cultivos": set(), "objetivos": set(), "dosis": set()})

        row = grouped[key]
        tipo = _extract_tipo(payload)
        comp = _extract_composition(payload)
        if tipo and tipo != "N/D":
            row["tipos"].add(tipo)
        if comp and comp != "N/D":
            row["composiciones"].add(comp)
        if detailed:
            cultivo = str(payload.get("cultivo") or "").strip()
            objetivo = str(payload.get("objetivo") or "").strip()
            dosis = _normalize_dose_text(str(payload.get("dosis_texto") or ""))
            if cultivo and cultivo != "N/D":
                row["cultivos"].add(cultivo)
            if objetivo and objetivo != "N/D":
                row["objetivos"].add(objetivo)
            if dosis and dosis != "N/D":
                row["dosis"].add(dosis)
    return grouped


def _count_consolidated_products(hits: list[dict[str, Any]]) -> int:
    keys: set[tuple[str, str]] = set()
    for hit in hits:
        payload = hit.get("payload") or {}
        producto = str(
            payload.get("nombre_comercial") or payload.get("producto_nombre_comercial") or "producto sin nombre"
        ).strip()
        auth = str(payload.get("autorizacion_sag_numero_normalizado") or "N/D").strip()
        keys.add((normalize_text(auth), normalize_text(producto)))
    return len(keys)


def _build_router_context_snapshot(hits: list[dict[str, Any]], limit: int = 20) -> str:
    grouped = _group_hits_by_product(hits, detailed=True)
    if not grouped:
        return "sin contexto SAG consolidado"
    ordered = sorted(grouped.values(), key=lambda r: normalize_text(r["producto"]))
    lines: list[str] = []
    for row in ordered[: max(1, int(limit))]:
        lines.append(
            f"- producto: {row['producto']} | autorizacion: {row['autorizacion']} "
            f"| cultivo: {_render_values(row['cultivos'], max_items=5, max_len=60)} "
            f"| objetivo: {_render_values(row['objetivos'], max_items=5, max_len=90)}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Respuesta fallback sin LLM
# ---------------------------------------------------------------------------

def _build_fallback_response(
    hits: list[dict[str, Any]],
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
        key = (normalize_text(nombre), auth)
        if key not in grouped:
            grouped[key] = {
                "nombre": nombre, "auth": auth,
                "tipo": _extract_tipo(payload),
                "composicion": _extract_composition(payload),
                "cultivos": set(), "objetivos": set(), "dosis": set(),
            }
        g = grouped[key]
        if g["composicion"] == "N/D":
            comp = _extract_composition(payload)
            if comp and comp != "N/D":
                g["composicion"] = comp
        if g["tipo"] == "N/D":
            tipo = _extract_tipo(payload)
            if tipo:
                g["tipo"] = tipo
        cultivo = str(payload.get("cultivo") or "").strip()
        objetivo = str(payload.get("objetivo") or "").strip()
        dosis = _normalize_dose_text(str(payload.get("dosis_texto") or ""))
        if cultivo:
            g["cultivos"].add(cultivo)
        if objetivo:
            g["objetivos"].add(objetivo)
        if dosis:
            g["dosis"].add(dosis)

    rows = sorted(grouped.values(), key=lambda r: normalize_text(str(r.get("nombre") or "")))
    if not rows:
        return "No encontré productos en la base de datos de etiquetas con coincidencia directa para tu consulta."

    lines: list[str] = [
        f"Aquí tienes los productos de la base de datos de etiquetas que coinciden con tu consulta ({len(rows)} encontrados):"
    ]
    for idx, row in enumerate(rows, start=1):
        lines.append(f"{idx}. {row['nombre']}")
        lines.append(f"• Composición / I.A.: {row['composicion']}")
        lines.append(f"• Tipo: {row['tipo']}")
        lines.append(f"• Cultivo: {_render_values(row['cultivos'], max_items=8, max_len=70)}")
        lines.append(f"• Objetivo: {_render_values(row['objetivos'], max_items=6, max_len=120)}")
        lines.append(f"• Dosis reportada: {_render_values(row['dosis'], max_items=8, max_len=80, sep='; ')}")
        lines.append(f"• N° Autorización: {row['auth']}")
        lines.append("")

    response = "\n".join(lines).strip()
    if len(response) > 30000:
        response = response[:30000].rstrip() + "\n\n[Resultado truncado por longitud]"
    return response


# ---------------------------------------------------------------------------
# Prompt SAG
# ---------------------------------------------------------------------------

def _build_response_prompt(
    *,
    user_message: str,
    query: str,
    product_hint: str,
    context_block: str,
    csv_hints_block: str,
) -> str:
    template = load_prompt_template(RESPUESTA_SAG_PROMPT_FILE)
    return (
        template
        .replace("{{user_message}}", user_message.strip())
        .replace("{{query}}", query.strip())
        .replace("{{product_hint}}", (product_hint or "no especificado").strip())
        .replace("{{context_block}}", context_block)
        .replace("{{csv_hints_block}}", csv_hints_block.strip() or "- sin señales adicionales desde CSV")
    ).strip()


# ---------------------------------------------------------------------------
# Utilidades de extracción de campos SAG
# ---------------------------------------------------------------------------

def _extract_tipo(payload: dict[str, Any]) -> str:
    return str(
        payload.get("tipo")
        or payload.get("tipo_producto")
        or payload.get("tipo_formulacion")
        or payload.get("formulacion")
        or payload.get("formulación")
        or "N/D"
    ).strip()


def _extract_composition(payload: dict[str, Any]) -> str:
    keys = (
        "composicion", "composicion_texto", "composición", "composicion_quimica",
        "composición_química", "ingrediente_activo", "ingredientes_activos",
        "ingrediente", "ingredientes", "sustancia_activa", "sustancias_activas",
        "componente_activo", "componentes_activos", "ia", "i_a",
        "active_ingredient", "active_ingredients",
    )
    parts: list[str] = []
    for key in keys:
        text = _payload_value_to_text(payload.get(key))
        if text and _looks_like_valid_composition(text):
            parts.append(text)
    if not parts:
        group = _payload_value_to_text(payload.get("grupo_quimico"))
        if group and _looks_like_valid_composition(group):
            parts.append(group)
    if not parts:
        excel_pid = str(payload.get("producto_id") or "").strip()
        excel_comp = get_product_composition("", excel_pid)
        if excel_comp and _looks_like_valid_composition(excel_comp):
            parts.append(excel_comp)
    if not parts:
        for key, value in payload.items():
            key_norm = normalize_text(str(key))
            if not any(tok in key_norm for tok in ("ingred", "compos", "sustancia", "active")):
                continue
            if any(tok in key_norm for tok in ("telefono", "correo", "email", "emergencia", "seguridad", "contacto")):
                continue
            text = _payload_value_to_text(value)
            if text and _looks_like_valid_composition(text):
                parts.append(text)
    if not parts:
        return "N/D"
    seen: set[str] = set()
    cleaned: list[str] = []
    for p in parts:
        p_clean = re.sub(r"\s+", " ", p).strip()
        key = normalize_text(p_clean)
        if key and key not in seen:
            seen.add(key)
            cleaned.append(p_clean)
    combined = " | ".join(cleaned)
    combined = re.sub(r"\s+", " ", combined).strip()
    return combined[:250].rstrip() + "..." if len(combined) > 250 else combined


def _looks_like_valid_composition(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if any(tok in normalized for tok in ("telefono", "emergencia", "seguridad", "advertencia", "contacto")):
        return False
    return not bool(re.search(r"\+?\d[\d\-\s]{7,}", normalized))


def _payload_value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    if isinstance(value, list):
        parts = [_payload_value_to_text(item) for item in value if item is not None]
        return ", ".join(p for p in parts if p)
    if isinstance(value, dict):
        parts = [_payload_value_to_text(v) for v in value.values()]
        return ", ".join(p for p in parts if p)
    return str(value).strip()


def _normalize_dose_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;")
    if not text:
        return ""
    text = re.sub(r"(\d)\s*a\s*(\d)", r"\1 a \2", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d)([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"\s*;\s*", "; ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def _render_values(
    values: set[str],
    *,
    max_items: int,
    max_len: int,
    default: str = "N/D",
    sep: str = ", ",
) -> str:
    if not values:
        return default
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in sorted(values):
        text = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;")
        if not text:
            continue
        text_norm = normalize_text(text)
        if any(tok in text_norm for tok in ("telefono", "emergencia", "seguridad sin codigos")):
            continue
        if len(text) > max_len:
            text = text[:max_len].rstrip() + "..."
        key = normalize_text(text)
        if key and key not in seen:
            seen.add(key)
            cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return sep.join(cleaned) if cleaned else default
