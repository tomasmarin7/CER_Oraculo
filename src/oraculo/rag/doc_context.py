from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Set, Tuple

from qdrant_client.http.exceptions import UnexpectedResponse

from ..config import Settings
from ..vectorstore.qdrant_client import get_qdrant_client
from ..vectorstore.search import scroll_doc_points


# Cantidad de informes a expandir
TOP_DOCS = 8

# Ventanas para dar “contexto completo” por informe
HEAD_N = 8           # primeros chunks: portada/resumen/intro
TAIL_N = 10          # últimos chunks: conclusiones/anexos
AROUND_BEFORE = 6    # ventana antes del mejor chunk
AROUND_AFTER = 12    # ventana después del mejor chunk

# Presupuesto total aproximado de contexto (se reparte por documento)
TOTAL_CONTEXT_CHAR_BUDGET = 96000
MIN_DOC_CHAR_BUDGET = 6000
MAX_DOC_CHAR_BUDGET = 20000

# Si quieres SOLO texto original, pon False (recomendado para evitar “resúmenes”)
INCLUDE_OVERVIEW_CHUNKS = True

# Secciones “core” (usar section_norm es mucho más confiable)
CORE_SECTION_NORMS = {
    "RESUMEN",
    "OBJETIVO",
    "MATERIALES Y METODO",
    "MATERIALES Y METODOS",
    "MATERIALES Y MÉTODO",
    "MATERIALES Y MÉTODOS",
    "DISENO EXPERIMENTAL",
    "DISEÑO EXPERIMENTAL",
    "EVALUACIONES",
    "TRATAMIENTO",
    "TRATAMIENTOS",
    "RESULTADOS",
    "CONCLUSIONES",
    "CONCLUSION",
    "CONCLUSIÓN",
}

# A veces el informe viene en inglés
CORE_SECTION_NORMS_EN = {
    "ABSTRACT",
    "OBJECTIVE",
    "MATERIALS AND METHODS",
    "METHODS",
    "RESULTS",
    "CONCLUSION",
}

# Tablas relevantes: tratamientos/dosis/diseño/resultados
TABLE_SECTIONS_HINTS = ("TRAT", "DOSIS", "DISENO", "DISEÑO", "RESULT", "EVAL")
LOCATION_FIELDS = ("comuna", "localidad", "region", "ubicacion")


@dataclass
class DocContext:
    doc_id: str
    pdf_filename: str
    temporada: str
    cliente: str
    producto: str
    especie: str
    variedad: str
    comuna: str
    localidad: str
    region: str
    ubicacion: str
    chunks: List[Dict[str, Any]]


def _payload_get(payload: Dict[str, Any], key: str) -> str:
    v = payload.get(key)
    return str(v).strip() if v is not None else ""


def _payload_get_first(payload: Dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = _payload_get(payload, key)
        if value:
            return value
    return ""


def _best_text(payload: Dict[str, Any]) -> str:
    for k in ("text", "chunk", "content", "page_content"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _extract_location_fields(payload: Dict[str, Any]) -> Dict[str, str]:
    """
    Extrae ubicación territorial usando aliases frecuentes.
    """
    comuna = _payload_get_first(payload, ["comuna", "ubicacion_comuna", "commune"])
    localidad = _payload_get_first(
        payload,
        ["localidad", "ubicacion_localidad", "locality", "ciudad", "city"],
    )
    region = _payload_get_first(payload, ["region", "ubicacion_region", "state"])
    ubicacion = _payload_get_first(
        payload,
        ["ubicacion", "location", "ubicacion_texto", "zona"],
    )

    return _location_dict(comuna, localidad, region, ubicacion)


def _location_dict(comuna: str, localidad: str, region: str, ubicacion: str) -> Dict[str, str]:
    return {
        "comuna": comuna,
        "localidad": localidad,
        "region": region,
        "ubicacion": ubicacion,
    }


def _empty_location() -> Dict[str, str]:
    return _location_dict("", "", "", "")


def _merge_location(base: Dict[str, str], candidate: Dict[str, str]) -> Dict[str, str]:
    merged = dict(base)
    for key in LOCATION_FIELDS:
        if not merged[key] and candidate[key]:
            merged[key] = candidate[key]
    return merged


def _clean_location_value(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip(" .;,\n\t")
    cleaned = cleaned.replace("O’H", "O'H").replace("O´H", "O'H")
    cleaned = cleaned.replace("RegiÃ³n", "Región")
    cleaned = cleaned.replace("Oâ€™H", "O'H")
    return cleaned


def _extract_location_from_text(text: str) -> Dict[str, str]:
    """
    Extrae comuna/localidad/región desde texto libre de chunks.
    """
    source = text or ""
    out = _empty_location()
    if not source:
        return out

    common_chars = r"A-Za-zÁÉÍÓÚÑáéíóúñ'´`’.\-\s"
    patterns = {
        "comuna": [
            rf"comuna\s+de\s+([{common_chars}]{{2,80}})",
            rf"comuna\s+([{common_chars}]{{2,80}})",
        ],
        "localidad": [
            rf"localidad\s+de\s+([{common_chars}]{{2,80}})",
            rf"localidad\s+([{common_chars}]{{2,80}})",
            rf"located\s+in\s+([{common_chars}]{{2,80}})",
        ],
        "region": [
            rf"regi[oó]n\s+del?\s+([{common_chars}]{{2,120}})",
            rf"regi[oó]n\s+de\s+([{common_chars}]{{2,120}})",
            rf"regi[oó]n\s+([{common_chars}]{{2,120}})",
            rf"region\s+([{common_chars}]{{2,120}})",
        ],
    }

    for field, regexes in patterns.items():
        for regex in regexes:
            match = re.search(regex, source, flags=re.IGNORECASE)
            if match:
                value = _clean_location_value(match.group(1))
                value = re.split(
                    r"[\(\),;]| latitud| longitud| latitude| longitude|\bchile\b",
                    value,
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip()

                # Limpieza específica para capturas largas.
                value = re.sub(r"\ben\s+la\s+comuna\s+de\s+.*$", "", value, flags=re.IGNORECASE).strip()
                value = re.sub(r"\bcomuna\s+de\s+.*$", "", value, flags=re.IGNORECASE).strip()
                value = re.sub(r"\s+", " ", value).strip(" -")
                if value:
                    out[field] = value
                    break
    # Ubicación libre: conservar una frase corta cuando exista "ubicado en ..."
    match_ubic = re.search(
        r"(ubicad[oa]\s+en\s+[^.\n]{8,180}|located\s+in\s+[^.\n]{8,180})",
        source,
        flags=re.IGNORECASE,
    )
    if match_ubic:
        value = _clean_location_value(match_ubic.group(1))
        value = re.split(r"[\(\);]| latitude| longitude", value, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        out["ubicacion"] = value

    return out


def _fill_location_from_points(
    location: Dict[str, str],
    points: List[Dict[str, Any]],
) -> Dict[str, str]:
    """
    Si faltan campos territoriales en payload_ref, intenta completarlos
    usando metadata de cualquier chunk del documento.
    """
    completed = dict(location)
    if all(completed.values()):
        return completed

    for point in points:
        payload = point.get("payload") or {}
        candidate = _extract_location_fields(payload)

        # Si la metadata viene vacía, intentar extraer desde texto del chunk.
        if not any(candidate.values()):
            candidate = _extract_location_from_text(_best_text(payload))

        completed = _merge_location(completed, candidate)
        if all(completed.values()):
            break

    return completed


def _chunk_index(payload: Dict[str, Any]) -> int:
    try:
        return int(payload.get("chunk_index"))
    except Exception:
        return -1


def _section_norm(payload: Dict[str, Any]) -> str:
    return _payload_get(payload, "section_norm").strip()


def _chunk_type(payload: Dict[str, Any]) -> str:
    return _payload_get(payload, "chunk_type").strip()


def _is_overview_chunk(payload: Dict[str, Any]) -> bool:
    ct = _chunk_type(payload)
    return ct in {"doc_overview", "conclusion_overview"}


def _is_core_section(payload: Dict[str, Any]) -> bool:
    sn = _section_norm(payload).upper()
    if not sn:
        return False
    if sn in CORE_SECTION_NORMS:
        return True
    if sn in CORE_SECTION_NORMS_EN:
        return True
    # heurística por si viene “RESULTADOS > X”
    for core in CORE_SECTION_NORMS:
        if core in sn:
            return True
    for core in CORE_SECTION_NORMS_EN:
        if core in sn:
            return True
    return False


def _is_relevant_table(payload: Dict[str, Any]) -> bool:
    if _chunk_type(payload) != "table":
        return False
    sn = _section_norm(payload).upper()
    return any(h in sn for h in TABLE_SECTIONS_HINTS)


def _collect_indices(points: List[Dict[str, Any]]) -> Tuple[List[int], int, int]:
    indices = sorted({_chunk_index(p["payload"]) for p in points if _chunk_index(p["payload"]) >= 0})
    if not indices:
        return [], -1, -1
    return indices, indices[0], indices[-1]


def _plan_indices(points: List[Dict[str, Any]], best_idx: int) -> List[int]:
    """
    Plan de selección en orden de prioridad:
    1) Secciones core (por section_norm) + tablas relevantes
    2) Ventana alrededor del best chunk
    3) Head / Tail
    4) (opcional) overview chunks (se incluyen aunque no sean “core”, si está activado)
    """
    indices, min_i, max_i = _collect_indices(points)
    if not indices:
        return []

    wanted: Set[int] = set()

    # 1) Core sections + tablas relevantes
    for p in points:
        pay = p["payload"]
        idx = _chunk_index(pay)
        if idx < 0:
            continue

        if not INCLUDE_OVERVIEW_CHUNKS and _is_overview_chunk(pay):
            continue

        if _is_core_section(pay) or _is_relevant_table(pay):
            wanted.add(idx)

    # 2) Ventana alrededor del best chunk
    if best_idx >= 0:
        for i in range(best_idx - AROUND_BEFORE, best_idx + AROUND_AFTER + 1):
            wanted.add(i)

    # 3) Head / Tail
    for i in range(min_i, min_i + HEAD_N):
        wanted.add(i)
    for i in range(max_i - TAIL_N + 1, max_i + 1):
        wanted.add(i)

    # 4) Overview (si está activado): asegurar que entren, pero sin desplazar core
    if INCLUDE_OVERVIEW_CHUNKS:
        for p in points:
            pay = p["payload"]
            idx = _chunk_index(pay)
            if idx >= 0 and _is_overview_chunk(pay):
                wanted.add(idx)

    # Orden final
    return sorted(wanted)


def _pack_doc(
    points: List[Dict[str, Any]],
    best_idx: int,
    doc_char_budget: int,
) -> List[Dict[str, Any]]:
    # map idx -> payload (primera ocurrencia)
    by_idx: Dict[int, Dict[str, Any]] = {}
    for p in points:
        pay = p["payload"]
        idx = _chunk_index(pay)
        if idx >= 0 and idx not in by_idx:
            by_idx[idx] = pay

    plan = _plan_indices(points, best_idx)

    chunks: List[Dict[str, Any]] = []
    total_chars = 0

    # Primera pasada: asegurar core sections primero (aunque el plan venga mezclado)
    def add_idx(idx: int) -> None:
        nonlocal total_chars
        pay = by_idx.get(idx)
        if not pay:
            return
        if not INCLUDE_OVERVIEW_CHUNKS and _is_overview_chunk(pay):
            return

        text = _best_text(pay)
        if not text:
            return

        # paquete compacto por chunk
        chunk = {
            "chunk_index": idx,
            "chunk_type": _payload_get(pay, "chunk_type"),
            "page_number": pay.get("page_number"),
            "section_norm": _payload_get(pay, "section_norm"),
            "heading_path": _payload_get(pay, "heading_path"),
            "text": text,
        }

        if total_chars + len(text) > doc_char_budget:
            return

        total_chars += len(text)
        chunks.append(chunk)

    core_first = []
    rest = []
    for idx in plan:
        pay = by_idx.get(idx)
        if not pay:
            continue
        if _is_core_section(pay) or _is_relevant_table(pay) or _is_overview_chunk(pay):
            core_first.append(idx)
        else:
            rest.append(idx)

    seen = set()

    for idx in core_first:
        if idx in seen:
            continue
        seen.add(idx)
        add_idx(idx)

    for idx in rest:
        if idx in seen:
            continue
        seen.add(idx)
        add_idx(idx)

    # ordenar chunks por chunk_index (para lectura fluida)
    chunks.sort(key=lambda c: c["chunk_index"])
    return chunks


def _doc_char_budget(settings: Settings, docs_count: int) -> int:
    """
    Calcula presupuesto por documento según el presupuesto total del prompt.
    """
    total_budget = max(int(settings.rag_total_context_char_budget), 1)
    min_budget = max(int(settings.rag_min_doc_char_budget), 1)
    max_budget = max(int(settings.rag_max_doc_char_budget), min_budget)
    safe_docs_count = max(docs_count, 1)

    per_doc = total_budget // safe_docs_count
    if per_doc < min_budget:
        return min_budget
    if per_doc > max_budget:
        return max_budget
    return per_doc


def _fetch_doc_points(
    hits: List[Dict[str, Any]],
    settings: Settings,
    qdrant: Any,
    doc_id: str,
) -> List[Dict[str, Any]]:
    try:
        return scroll_doc_points(
            client=qdrant,
            collection=settings.qdrant_collection,
            doc_id=doc_id,
            payload_fields=None,
        )
    except UnexpectedResponse:
        # Fallback: usa solo los hits de ese doc si scroll falla.
        return [
            {"id": h.get("id"), "payload": (h.get("payload") or {})}
            for h in hits
            if (h.get("payload") or {}).get("doc_id") == doc_id
        ]


def build_doc_contexts_from_hits(
    hits: List[Dict[str, Any]],
    settings: Settings,
    top_docs: int = TOP_DOCS,
) -> List[DocContext]:
    """
    1) Agrupa hits por doc_id y toma los top N docs por score.
    2) Para cada doc_id: scroll de todos sus chunks.
    3) Selecciona chunks priorizados con presupuesto dinámico por documento.
    """
    doc_best: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}

    for h in hits:
        payload = h.get("payload") or {}
        doc_id = _payload_get(payload, "doc_id")
        if not doc_id:
            continue

        score = float(h.get("score", 0.0))
        cidx = _chunk_index(payload)

        if doc_id not in doc_best or score > doc_best[doc_id][0]:
            doc_best[doc_id] = (score, cidx, payload)

    max_docs = max(int(top_docs), 1)
    chosen = sorted(doc_best.items(), key=lambda kv: kv[1][0], reverse=True)[:max_docs]
    per_doc_char_budget = _doc_char_budget(settings, docs_count=len(chosen))

    qdrant = get_qdrant_client(settings)
    out: List[DocContext] = []

    for doc_id, (_score, best_cidx, payload_ref) in chosen:
        location = _extract_location_fields(payload_ref)
        points = _fetch_doc_points(hits, settings, qdrant, doc_id)

        location = _fill_location_from_points(location, points)

        chunks = _pack_doc(
            points,
            best_idx=best_cidx,
            doc_char_budget=per_doc_char_budget,
        )

        out.append(
            DocContext(
                doc_id=doc_id,
                pdf_filename=_payload_get(payload_ref, "pdf_filename"),
                temporada=_payload_get(payload_ref, "temporada"),
                cliente=_payload_get(payload_ref, "cliente"),
                producto=_payload_get(payload_ref, "producto"),
                especie=_payload_get(payload_ref, "especie"),
                variedad=_payload_get(payload_ref, "variedad"),
                comuna=location["comuna"],
                localidad=location["localidad"],
                region=location["region"],
                ubicacion=location["ubicacion"],
                chunks=chunks,
            )
        )

    return out
