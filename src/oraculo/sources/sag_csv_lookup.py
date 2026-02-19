from __future__ import annotations

import csv
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MATCH_STOPWORDS = {
    "para",
    "con",
    "del",
    "los",
    "las",
    "una",
    "uno",
    "unos",
    "unas",
    "producto",
    "productos",
    "registro",
    "registros",
    "autorizacion",
    "autorizaciones",
    "cultivo",
    "cultivos",
    "control",
    "tratar",
    "sirve",
    "sag",
}


def _normalize_text(text: str) -> str:
    text = str(text or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _normalize_text(text)) if len(t) >= 4]


def _split_pipe_values(text: str) -> list[str]:
    return [part.strip() for part in str(text or "").split("|") if part.strip()]


def _split_multi_values(text: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"[|\n;]+", str(text or ""))
        if part and part.strip()
    ]


def _first_nonempty(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _truncate(text: str, max_len: int = 180) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= max_len:
        return value
    return value[: max(0, max_len - 3)].rstrip() + "..."


@dataclass(slots=True)
class SagCsvRecord:
    product_id: str
    product_name: str
    auths: set[str]
    objectives: set[str]
    ingredients: set[str]
    composition: str
    searchable_text: str


@dataclass(slots=True)
class SagCsvIndex:
    product_text: dict[str, str]
    product_objective_text: dict[str, str]
    product_auths: dict[str, set[str]]
    product_composition: dict[str, str]
    product_name: dict[str, str]
    records: list[SagCsvRecord]


@lru_cache(maxsize=2)
def _load_index(csv_path: str) -> SagCsvIndex:
    effective_path = (
        (csv_path or "").strip()
        or os.getenv("SAG_CSV_PATH")
        or os.getenv("SAG_EXCEL_PATH")
        or "SAG.csv"
    )
    path = Path(effective_path)
    if not path.exists():
        return SagCsvIndex({}, {}, {}, {}, {}, [])

    product_text: dict[str, str] = {}
    product_objective_text: dict[str, str] = {}
    product_auths: dict[str, set[str]] = {}
    product_composition: dict[str, str] = {}
    product_name: dict[str, str] = {}
    records: list[SagCsvRecord] = []

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = _normalize_text(row.get("producto_id", ""))
            if not pid:
                continue
            name = _first_nonempty(
                row,
                "nombre_comercial",
                "producto_nombre_comercial",
            )
            auths_raw = _first_nonempty(
                row,
                "autorizaciones",
                "autorizacion",
                "autorizacion_sag_numero_normalizado",
            )
            auths = {_normalize_text(v) for v in _split_multi_values(auths_raw)}
            auths = {a for a in auths if a}
            ingredients = set(_split_multi_values(row.get("ingredientes", "")))
            composition = _first_nonempty(
                row,
                "composicion_texto",
                "composicion",
                "composición",
            )

            objective_parts: list[str] = []
            objective_parts.extend(_split_multi_values(_first_nonempty(row, "objetivos", "objetivo")))
            objective_parts.extend(
                _split_multi_values(
                    _first_nonempty(row, "objetivos_normalizados", "objetivo_normalizado")
                )
            )
            objective_parts.extend(
                _split_multi_values(
                    _first_nonempty(row, "categorias_objetivo", "categoria_objetivo")
                )
            )
            objectives = {part for part in objective_parts if part}

            text_parts: list[str] = []
            text_parts.append(str(row.get("grupo_quimico", "")).strip())
            text_parts.extend(sorted(objectives))
            text_parts.extend(sorted(ingredients))
            if composition:
                text_parts.append(composition)
            if name:
                text_parts.append(name)
            search_text = _normalize_text(" | ".join(part for part in text_parts if part))
            obj_text = _normalize_text(" | ".join(sorted(objectives)))

            product_text[pid] = search_text
            product_objective_text[pid] = obj_text
            product_auths[pid] = auths
            if composition:
                product_composition[pid] = composition
            if name:
                product_name[pid] = name
            records.append(
                SagCsvRecord(
                    product_id=pid,
                    product_name=name,
                    auths=auths,
                    objectives=objectives,
                    ingredients=ingredients,
                    composition=composition,
                    searchable_text=search_text,
                )
            )

    return SagCsvIndex(
        product_text=product_text,
        product_objective_text=product_objective_text,
        product_auths=product_auths,
        product_composition=product_composition,
        product_name=product_name,
        records=records,
    )


def find_products_by_ingredient(csv_path: str, ingredient_hint: str) -> tuple[set[str], set[str]]:
    needle = _normalize_text(ingredient_hint)
    if not needle:
        return set(), set()
    needle_tokens = _tokens(needle)
    idx = _load_index(csv_path)
    product_ids: set[str] = set()
    auths: set[str] = set()
    for pid, text in idx.product_text.items():
        if not text:
            continue
        if needle in text or text in needle:
            product_ids.add(pid)
            auths.update(idx.product_auths.get(pid, set()))
            continue
        if needle_tokens and all(tok in text for tok in needle_tokens):
            product_ids.add(pid)
            auths.update(idx.product_auths.get(pid, set()))
    return product_ids, auths


def find_products_by_objective(csv_path: str, objective_hint: str) -> tuple[set[str], set[str]]:
    needle = _normalize_text(objective_hint)
    if not needle:
        return set(), set()
    needle_tokens = _tokens(needle)
    idx = _load_index(csv_path)
    product_ids: set[str] = set()
    auths: set[str] = set()
    for pid, text in idx.product_objective_text.items():
        if not text:
            continue
        if needle in text or text in needle:
            product_ids.add(pid)
            auths.update(idx.product_auths.get(pid, set()))
            continue
        if needle_tokens and all(tok in text for tok in needle_tokens):
            product_ids.add(pid)
            auths.update(idx.product_auths.get(pid, set()))
    return product_ids, auths


def get_product_composition(csv_path: str, product_id: str) -> str:
    pid = _normalize_text(product_id)
    if not pid:
        return ""
    idx = _load_index(csv_path)
    return str(idx.product_composition.get(pid, "")).strip()


def build_csv_query_hints_block(csv_path: str, query_text: str, limit: int = 8) -> str:
    query = _normalize_text(query_text)
    if not query:
        return "- sin señales adicionales desde CSV"
    query_tokens = [tok for tok in _tokens(query) if tok not in MATCH_STOPWORDS]
    if not query_tokens:
        return "- sin señales adicionales desde CSV"

    idx = _load_index(csv_path)
    ranked: list[tuple[int, SagCsvRecord]] = []
    for rec in idx.records:
        if not rec.searchable_text:
            continue
        overlap = sum(1 for tok in query_tokens if tok in rec.searchable_text)
        if overlap <= 0:
            continue
        score = overlap * 10
        if rec.product_name and _normalize_text(rec.product_name) in query:
            score += 8
        if rec.composition and any(tok in _normalize_text(rec.composition) for tok in query_tokens):
            score += 3
        ranked.append((score, rec))

    if not ranked:
        return "- sin señales adicionales desde CSV"

    ranked.sort(key=lambda pair: (pair[0], pair[1].product_name), reverse=True)
    lines: list[str] = []
    for i, (_, rec) in enumerate(ranked[: max(1, int(limit))], start=1):
        auth = _truncate(", ".join(sorted(a for a in rec.auths if a)) or "N/D", max_len=80)
        objectives = _truncate(", ".join(sorted(rec.objectives)) or "N/D")
        ingredients = _truncate(", ".join(sorted(rec.ingredients)) or "N/D")
        product_name = rec.product_name or rec.product_id
        lines.append(
            f"{i}. {product_name} | auth: {auth} | objetivos: {objectives} | ingredientes: {ingredients}"
        )
    return "\n".join(lines)


def find_products_by_query(
    csv_path: str,
    query_text: str,
    limit: int = 80,
) -> tuple[set[str], set[str], list[SagCsvRecord]]:
    query = _normalize_text(query_text)
    if not query:
        return set(), set(), []

    query_tokens = [tok for tok in _tokens(query) if tok not in MATCH_STOPWORDS]
    if not query_tokens:
        return set(), set(), []

    idx = _load_index(csv_path)
    ranked: list[tuple[int, SagCsvRecord]] = []
    for rec in idx.records:
        if not rec.searchable_text:
            continue
        overlap = sum(1 for tok in query_tokens if tok in rec.searchable_text)
        if overlap <= 0:
            continue
        score = overlap * 10
        if rec.product_name and _normalize_text(rec.product_name) in query:
            score += 8
        if rec.composition and any(tok in _normalize_text(rec.composition) for tok in query_tokens):
            score += 4
        ranked.append((score, rec))

    if not ranked:
        return set(), set(), []

    ranked.sort(key=lambda pair: (pair[0], pair[1].product_name), reverse=True)
    top_records = [rec for _, rec in ranked[: max(1, int(limit))]]

    product_ids: set[str] = {rec.product_id for rec in top_records if rec.product_id}
    auths: set[str] = set()
    for rec in top_records:
        auths.update(a for a in rec.auths if a)

    return product_ids, auths, top_records
