from __future__ import annotations

import re
import os
import unicodedata
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

NS_MAIN = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_REL = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _normalize_text(text: str) -> str:
    text = str(text or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _col_idx(ref: str) -> int:
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return 0
    acc = 0
    for ch in m.group(1):
        acc = acc * 26 + (ord(ch) - 64)
    return acc


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _normalize_text(text)) if len(t) >= 4]


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.findall("x:si", NS_MAIN):
        txt = "".join(t.text or "" for t in si.findall(".//x:t", NS_MAIN))
        out.append(txt)
    return out


def _sheet_name_to_path(zf: zipfile.ZipFile) -> dict[str, str]:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    id_to_target: dict[str, str] = {}
    for rel in rels.findall("r:Relationship", NS_REL):
        rid = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if not rid or not target:
            continue
        if target.startswith("/"):
            target = target[1:]
        if not target.startswith("xl/"):
            target = f"xl/{target}"
        id_to_target[rid] = target

    out: dict[str, str] = {}
    for sheet in wb.findall("x:sheets/x:sheet", NS_MAIN):
        name = sheet.attrib.get("name", "")
        rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
        path = id_to_target.get(rid, "")
        if name and path:
            out[name] = path
    return out


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    t = cell.attrib.get("t")
    if t == "inlineStr":
        node = cell.find("x:is/x:t", NS_MAIN)
        return (node.text or "").strip() if node is not None else ""
    v = cell.find("x:v", NS_MAIN)
    if v is None:
        return ""
    raw = (v.text or "").strip()
    if t == "s" and raw.isdigit():
        idx = int(raw)
        if 0 <= idx < len(shared):
            return shared[idx]
    return raw


def _iter_sheet_rows(zf: zipfile.ZipFile, path: str, shared: list[str]) -> Iterable[dict[int, str]]:
    root = ET.fromstring(zf.read(path))
    for row in root.findall(".//x:sheetData/x:row", NS_MAIN):
        vals: dict[int, str] = {}
        for cell in row.findall("x:c", NS_MAIN):
            idx = _col_idx(cell.attrib.get("r", ""))
            if idx <= 0:
                continue
            vals[idx] = _cell_value(cell, shared)
        if vals:
            yield vals


@dataclass(slots=True)
class SagExcelIndex:
    product_text: dict[str, str]
    product_objective_text: dict[str, str]
    product_auths: dict[str, set[str]]
    auth_products: dict[str, set[str]]
    product_composition: dict[str, str]
    product_name: dict[str, str]


@lru_cache(maxsize=2)
def _load_index(xlsx_path: str) -> SagExcelIndex:
    effective_path = (xlsx_path or "").strip() or os.getenv("SAG_EXCEL_PATH", "SAG - copia.xlsx")
    path = Path(effective_path)
    if not path.exists():
        return SagExcelIndex({}, {}, {}, {}, {}, {})

    with zipfile.ZipFile(path) as zf:
        shared = _read_shared_strings(zf)
        sheets = _sheet_name_to_path(zf)
        flat_path = sheets.get("flat")
        comp_path = sheets.get("composicion")
        if not flat_path:
            return SagExcelIndex({}, {}, {}, {}, {}, {})

        product_text: dict[str, set[str]] = {}
        product_objective_text: dict[str, set[str]] = {}
        product_auths: dict[str, set[str]] = {}
        auth_products: dict[str, set[str]] = {}
        product_name: dict[str, str] = {}
        product_composition_parts: dict[str, set[str]] = {}

        flat_rows = _iter_sheet_rows(zf, flat_path, shared)
        header = next(flat_rows, {})
        col_pid = next((i for i, v in header.items() if v == "producto_id"), 0)
        col_auth = next((i for i, v in header.items() if v == "autorizacion_sag_numero_normalizado"), 0)
        col_name = next((i for i, v in header.items() if v == "nombre_comercial"), 0)
        col_group = next((i for i, v in header.items() if v == "grupo_quimico"), 0)
        col_obj = next((i for i, v in header.items() if v == "objetivo"), 0)
        col_obj_norm = next((i for i, v in header.items() if v == "objetivo_normalizado"), 0)
        col_obj_cat = next((i for i, v in header.items() if v == "categoria_objetivo"), 0)

        for row in flat_rows:
            pid = _normalize_text(row.get(col_pid, ""))
            if not pid:
                continue
            auth = _normalize_text(row.get(col_auth, ""))
            name = str(row.get(col_name, "")).strip()
            group = str(row.get(col_group, "")).strip()
            if name and pid not in product_name:
                product_name[pid] = name
            if group:
                product_text.setdefault(pid, set()).add(group)
            objetivo = str(row.get(col_obj, "")).strip()
            objetivo_norm = str(row.get(col_obj_norm, "")).strip()
            objetivo_cat = str(row.get(col_obj_cat, "")).strip()
            if objetivo:
                product_objective_text.setdefault(pid, set()).add(objetivo)
            if objetivo_norm:
                product_objective_text.setdefault(pid, set()).add(objetivo_norm)
            if objetivo_cat:
                product_objective_text.setdefault(pid, set()).add(objetivo_cat)
            if auth:
                product_auths.setdefault(pid, set()).add(auth)
                auth_products.setdefault(auth, set()).add(pid)

        if comp_path:
            comp_rows = _iter_sheet_rows(zf, comp_path, shared)
            comp_header = next(comp_rows, {})
            c_pid = next((i for i, v in comp_header.items() if v == "producto_id"), 0)
            c_ing = next((i for i, v in comp_header.items() if v == "ingrediente"), 0)
            c_rol = next((i for i, v in comp_header.items() if v == "rol"), 0)
            c_conc = next((i for i, v in comp_header.items() if v == "concentracion_texto"), 0)

            for row in comp_rows:
                pid = _normalize_text(row.get(c_pid, ""))
                if not pid:
                    continue
                ing = str(row.get(c_ing, "")).strip()
                rol = str(row.get(c_rol, "")).strip()
                conc = str(row.get(c_conc, "")).strip()
                if not ing:
                    continue
                phrase = ing
                if rol:
                    phrase = f"{phrase} ({rol})"
                if conc:
                    phrase = f"{phrase}: {conc}"
                product_composition_parts.setdefault(pid, set()).add(phrase)
                product_text.setdefault(pid, set()).add(ing)
                product_objective_text.setdefault(pid, set())

        product_text_joined = {
            pid: _normalize_text(" | ".join(sorted(vals)))
            for pid, vals in product_text.items()
        }
        product_objective_text_joined = {
            pid: _normalize_text(" | ".join(sorted(vals)))
            for pid, vals in product_objective_text.items()
        }
        product_composition = {
            pid: " | ".join(sorted(parts))
            for pid, parts in product_composition_parts.items()
            if parts
        }

        return SagExcelIndex(
            product_text=product_text_joined,
            product_objective_text=product_objective_text_joined,
            product_auths=product_auths,
            auth_products=auth_products,
            product_composition=product_composition,
            product_name=product_name,
        )


def find_products_by_ingredient(xlsx_path: str, ingredient_hint: str) -> tuple[set[str], set[str]]:
    needle = _normalize_text(ingredient_hint)
    if not needle:
        return set(), set()
    needle_tokens = _tokens(needle)
    idx = _load_index(xlsx_path)
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


def find_products_by_objective(xlsx_path: str, objective_hint: str) -> tuple[set[str], set[str]]:
    needle = _normalize_text(objective_hint)
    if not needle:
        return set(), set()
    needle_tokens = _tokens(needle)
    idx = _load_index(xlsx_path)
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


def get_product_composition(xlsx_path: str, product_id: str) -> str:
    pid = _normalize_text(product_id)
    if not pid:
        return ""
    idx = _load_index(xlsx_path)
    return str(idx.product_composition.get(pid, "")).strip()
