"""Utilidades compartidas para el flujo guiado de conversación."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from ..followup import render_report_options
from ..rag.doc_context import DocContext
from .modelos import SesionChat


# ---------------------------------------------------------------------------
# Normalización de texto
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Normaliza texto: quita acentos, minúsculas, colapsa espacios."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    return re.sub(r"\s+", " ", text)


def token_roots(text: str) -> set[str]:
    """Extrae raíces simplificadas de tokens (stemming muy básico)."""
    tokens = re.findall(r"[a-z0-9]+", normalize_text(text))
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


def meaningful_tokens(text: str) -> list[str]:
    """Devuelve tokens informativos descartando stopwords."""
    stopwords = {
        "producto", "productos", "registrado", "registrados", "registro",
        "sag", "para", "contra", "con", "sin", "del", "de", "la", "el",
        "los", "las", "que", "cual", "cuales", "tiene", "tienen",
    }
    tokens = [t for t in re.findall(r"[a-z0-9]+", normalize_text(text)) if len(t) >= 4]
    return [t for t in tokens if t not in stopwords]


# ---------------------------------------------------------------------------
# Detección de intención
# ---------------------------------------------------------------------------

def is_affirmative(text: str) -> bool:
    normalized = normalize_text(text)
    yes_words = {"si", "claro", "dale", "ok", "bueno", "perfecto", "me interesa", "quiero"}
    return normalized in yes_words or any(w in normalized for w in ["si ", "me interesa", "quiero"])


def is_negative(text: str) -> bool:
    normalized = normalize_text(text)
    return normalized in {"no", "nop", "no gracias", "paso"}


def looks_like_problem_query(text: str) -> bool:
    normalized = normalize_text(text)
    keywords = [
        "plaga", "pulgon", "enfermedad", "problema",
        "que puedo hacer", "control", "tratamiento", "como combatir",
    ]
    return any(k in normalized for k in keywords)


def es_pregunta_sobre_contexto_actual(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    signals = (
        "dosis", "cuanto", "correcta", "aplicar", "aplicacion",
        "funciona", "resultado", "fitotoxic", "frecuencia",
        "cuantas veces", "y eso", "sirve", "como fue",
    )
    return any(token in normalized for token in signals)


def parece_pedir_ensayo_especifico(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if re.search(r"\bensayo\s+\d+\b", normalized):
        return True
    return any(
        token in normalized
        for token in ("detalle", "mas informacion", "más información", "ampliar")
    )


# ---------------------------------------------------------------------------
# Historial y mensajes de sesión
# ---------------------------------------------------------------------------

def last_assistant_message(sesion: SesionChat) -> str:
    for msg in reversed(sesion.mensajes):
        if msg.rol == "assistant":
            return msg.texto
    return ""


def render_recent_history(sesion: SesionChat, max_items: int = 12) -> str:
    if not sesion.mensajes:
        return "(vacio)"
    lines = [f"{m.rol}: {m.texto}" for m in sesion.mensajes[-max_items:]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Texto de aclaración para follow-up
# ---------------------------------------------------------------------------

def build_followup_clarify_text(
    *,
    user_message: str,
    offered_reports: list[dict[str, Any]],
) -> str:
    normalized = normalize_text(user_message)
    if any(
        token in normalized
        for token in ("no me interesa", "ninguno", "ninguna", "no me sirven", "ningun ensayo")
    ):
        return (
            "Entiendo.\n"
            "Si quieres, hacemos una nueva búsqueda de ensayos del CER para otro cultivo, problema o producto.\n"
            "Y si ninguno de estos ensayos te satisface, también puedo buscar en nuestra base "
            "de datos de etiquetas según lo que declaran en su etiqueta."
        )

    options = render_report_options(offered_reports)
    base = (
        "Para continuar con precisión, dime qué ensayo o ensayos quieres revisar "
        "(por número o por producto).\n"
    )
    if options:
        return f"{base}Opciones disponibles:\n{options}"
    cleaned = (user_message or "").strip()
    if cleaned:
        return (
            "No logré identificar el ensayo exacto que quieres detallar. "
            f'Cuando dices "{cleaned}", indícame el número de ensayo o el producto.'
        )
    return "No logré identificar el ensayo exacto. Indícame el número o producto del ensayo."


# ---------------------------------------------------------------------------
# Serialización de hits y contextos de documentos
# ---------------------------------------------------------------------------

def serialize_seed_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": hit.get("id"),
            "score": float(hit.get("score", 0.0)),
            "payload": dict(hit.get("payload") or {}),
        }
        for hit in hits
    ]


def deserialize_seed_hits(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "score": float(item.get("score", 0.0)),
            "payload": dict(item.get("payload") or {}),
        }
        for item in items
        if isinstance(item, dict)
    ]


def serialize_doc_contexts(doc_contexts: list[DocContext]) -> list[dict[str, Any]]:
    return [
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
        for dc in doc_contexts
    ]


def deserialize_doc_contexts(items: list[dict[str, Any]]) -> list[DocContext]:
    return [
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
        for item in items
        if isinstance(item, dict)
    ]


# ---------------------------------------------------------------------------
# Merge de hits por id
# ---------------------------------------------------------------------------

def merge_hits_by_id(
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


# ---------------------------------------------------------------------------
# Carga de plantillas de prompts
# ---------------------------------------------------------------------------

def load_prompt_template(filename: str) -> str:
    """Carga un template .md desde conversation/prompts/."""
    prompt_path = Path(__file__).resolve().parent / "prompts" / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()
