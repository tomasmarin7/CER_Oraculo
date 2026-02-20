from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

STOPWORDS = {
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "para",
    "con",
    "por",
    "sobre",
    "que",
    "ensayo",
    "ensayos",
    "informe",
    "informes",
    "cer",
    "quiero",
    "necesito",
    "dame",
    "mostrar",
    "muestra",
    "todos",
    "todas",
}


@dataclass(slots=True)
class CerCsvRecord:
    temporada: str
    cliente: str
    producto: str
    especie: str
    variedad: str
    pdf: str
    url_estudio: str
    url_pdf: str
    searchable_text: str


@dataclass(slots=True)
class CerCsvIndex:
    records: list[CerCsvRecord]
    especies: set[str]
    productos: set[str]
    variedades: set[str]
    clientes: set[str]
    temporadas: set[str]


def _normalize(text: str) -> str:
    value = str(text or "").strip().lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _tokenize(text: str) -> list[str]:
    return [tok for tok in re.findall(r"[a-z0-9]+", _normalize(text)) if len(tok) >= 3]


def _singular(token: str) -> str:
    tok = token.strip()
    if len(tok) > 4 and tok.endswith("es"):
        return tok[:-2]
    if len(tok) > 3 and tok.endswith("s"):
        return tok[:-1]
    return tok


def _token_root(token: str) -> str:
    tok = _singular(token.strip())
    if len(tok) >= 5 and tok.endswith(("a", "o")):
        tok = tok[:-1]
    return tok


def _token_roots(text: str) -> set[str]:
    roots: set[str] = set()
    for tok in _tokenize(text):
        if tok in STOPWORDS:
            continue
        root = _token_root(tok)
        if len(root) >= 4:
            roots.add(root)
    return roots


def _contains_with_plural_support(haystack: str, needle: str) -> bool:
    h = _normalize(haystack)
    n = _normalize(needle)
    if not h or not n:
        return False
    if len(n) <= 3:
        if re.search(rf"\b{re.escape(n)}\b", h):
            return True
        n_s = _singular(n)
        return bool(n_s and re.search(rf"\b{re.escape(n_s)}\b", h))
    if n in h:
        return True
    n_s = _singular(n)
    return bool(n_s and n_s in h)


@lru_cache(maxsize=2)
def load_cer_index(csv_path: str) -> CerCsvIndex:
    path = Path((csv_path or "").strip() or "CER.csv")
    if not path.exists():
        return CerCsvIndex([], set(), set(), set(), set(), set())

    records: list[CerCsvRecord] = []
    especies: set[str] = set()
    productos: set[str] = set()
    variedades: set[str] = set()
    clientes: set[str] = set()
    temporadas: set[str] = set()

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rec = CerCsvRecord(
                temporada=str(row.get("temporada") or "").strip(),
                cliente=str(row.get("cliente") or "").strip(),
                producto=str(row.get("producto") or "").strip(),
                especie=str(row.get("especie") or "").strip(),
                variedad=str(row.get("variedad") or "").strip(),
                pdf=str(row.get("pdf") or "").strip(),
                url_estudio=str(row.get("url_estudio") or "").strip(),
                url_pdf=str(row.get("url_pdf") or "").strip(),
                searchable_text="",
            )
            parts = [
                rec.temporada,
                rec.cliente,
                rec.producto,
                rec.especie,
                rec.variedad,
                rec.pdf,
                rec.url_estudio,
                rec.url_pdf,
            ]
            rec.searchable_text = _normalize(" | ".join(part for part in parts if part))
            records.append(rec)

            if rec.especie:
                especies.add(_normalize(rec.especie))
            if rec.producto:
                productos.add(_normalize(rec.producto))
            if rec.variedad:
                variedades.add(_normalize(rec.variedad))
            if rec.cliente:
                clientes.add(_normalize(rec.cliente))
            if rec.temporada:
                temporadas.add(_normalize(rec.temporada))

    return CerCsvIndex(records, especies, productos, variedades, clientes, temporadas)


def find_cer_records_by_query(csv_path: str, query_text: str, limit: int = 40) -> list[CerCsvRecord]:
    query_norm = _normalize(query_text)
    if not query_norm:
        return []
    query_tokens = [tok for tok in _tokenize(query_norm) if tok not in STOPWORDS]
    query_roots = _token_roots(query_norm)
    if not query_tokens:
        if not query_roots:
            return []

    index = load_cer_index(csv_path)
    ranked: list[tuple[int, CerCsvRecord]] = []

    for rec in index.records:
        text = rec.searchable_text
        if not text:
            continue

        overlap = sum(1 for tok in query_tokens if tok in text)
        especie_root_overlap = 0
        if rec.especie and query_roots:
            especie_roots = _token_roots(rec.especie)
            especie_root_overlap = len(query_roots & especie_roots)
        if overlap <= 0 and especie_root_overlap <= 0:
            continue

        score = overlap * 6
        if especie_root_overlap:
            score += min(especie_root_overlap * 10, 20)
        if rec.especie and _contains_with_plural_support(query_norm, rec.especie):
            score += 14
        if rec.producto and _contains_with_plural_support(query_norm, rec.producto):
            score += 16
        if rec.variedad and _contains_with_plural_support(query_norm, rec.variedad):
            score += 8
        if rec.cliente and _contains_with_plural_support(query_norm, rec.cliente):
            score += 6
        if rec.temporada and _contains_with_plural_support(query_norm, rec.temporada):
            score += 5

        ranked.append((score, rec))

    ranked.sort(
        key=lambda item: (
            item[0],
            item[1].temporada,
            item[1].producto,
            item[1].especie,
            item[1].variedad,
        ),
        reverse=True,
    )

    out: list[CerCsvRecord] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for _, rec in ranked:
        key = (
            _normalize(rec.temporada),
            _normalize(rec.cliente),
            _normalize(rec.producto),
            _normalize(rec.especie),
            _normalize(rec.variedad),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
        if len(out) >= max(1, int(limit)):
            break

    return out


def detect_cer_entities(csv_path: str, text: str) -> dict[str, set[str]]:
    signals = {
        "especies": set(),
        "productos": set(),
        "variedades": set(),
        "clientes": set(),
        "temporadas": set(),
    }

    norm_text = _normalize(text)
    if not norm_text:
        return signals

    index = load_cer_index(csv_path)
    query_roots = _token_roots(norm_text)

    # Detección por especie en texto directo (incluye variantes como ciruela/ciruelo).
    for especie_norm in index.especies:
        if not especie_norm:
            continue
        if _contains_with_plural_support(norm_text, especie_norm):
            signals["especies"].add(especie_norm)
            continue
        especie_roots = _token_roots(especie_norm)
        if query_roots and especie_roots and (query_roots & especie_roots):
            signals["especies"].add(especie_norm)

    for rec in find_cer_records_by_query(csv_path, norm_text, limit=80):
        if rec.especie:
            especie_norm = _normalize(rec.especie)
            especie_roots = _token_roots(especie_norm)
            if _contains_with_plural_support(norm_text, especie_norm) or (
                query_roots and especie_roots and (query_roots & especie_roots)
            ):
                signals["especies"].add(especie_norm)
        if rec.producto:
            signals["productos"].add(rec.producto)
        if rec.variedad:
            signals["variedades"].add(rec.variedad)
        if rec.cliente:
            signals["clientes"].add(rec.cliente)
        if rec.temporada:
            signals["temporadas"].add(rec.temporada)

    return signals


def build_cer_csv_hints_block(csv_path: str, query_text: str, limit: int = 12) -> str:
    recs = find_cer_records_by_query(csv_path, query_text, limit=max(1, int(limit)))
    if not recs:
        return "- sin señales CER.csv"

    lines: list[str] = []
    for i, rec in enumerate(recs, start=1):
        lines.append(
            f"{i}. producto={rec.producto or 'N/D'} | cultivo={rec.especie or 'N/D'} | variedad={rec.variedad or 'N/D'} | cliente={rec.cliente or 'N/D'} | temporada={rec.temporada or 'N/D'}"
        )
    return "\n".join(lines)
