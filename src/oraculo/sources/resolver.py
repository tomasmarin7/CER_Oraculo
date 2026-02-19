from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Any


def _project_root() -> Path:
    # .../CER_Oraculo_Publico/src/oraculo/sources/resolver.py -> parents[3] = root
    return Path(__file__).resolve().parents[3]


def _norm_token(s: Optional[str]) -> str:
    if not s:
        return ""
    s = str(s).strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.upper()
    # dejar solo alfanumérico
    s = "".join(ch for ch in s if ch.isalnum())
    return s


def _humanize(s: Optional[str]) -> str:
    if not s:
        return ""
    return str(s).replace("_", " ").strip()


@dataclass(frozen=True)
class SourceRecord:
    pdf: str
    url_pdf: str
    url_estudio: str
    temporada: str
    cliente: str
    producto: str
    especie: str
    variedad: str

    def label(self) -> str:
        # evita underscores del filename, mejor para Telegram
        parts = [self.temporada, self.cliente, self.producto, f"{self.especie} {self.variedad}".strip()]
        return " | ".join([p for p in parts if p])


class SourceResolver:
    def __init__(self, csv_path: Optional[str] = None) -> None:
        if csv_path:
            self.csv_path = Path(csv_path)
        else:
            self.csv_path = _project_root() / "CER.csv"
        self._loaded = False
        self._by_pdf: Dict[str, SourceRecord] = {}
        self._by_meta: Dict[Tuple[str, str, str, str, str], SourceRecord] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"No encontré CSV de fuentes CER en {self.csv_path}. "
                f"Colócalo en la raíz del proyecto (CER.csv) o pasa csv_path."
            )

        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pdf = (row.get("pdf") or "").strip()
                if not pdf:
                    continue

                rec = SourceRecord(
                    pdf=pdf,
                    url_pdf=(row.get("url_pdf") or "").strip(),
                    url_estudio=(row.get("url_estudio") or "").strip(),
                    temporada=(row.get("temporada") or "").strip(),
                    cliente=(row.get("cliente") or "").strip(),
                    producto=(row.get("producto") or "").strip(),
                    especie=(row.get("especie") or "").strip(),
                    variedad=(row.get("variedad") or "").strip(),
                )

                self._by_pdf[pdf.strip().lower()] = rec

                meta_key = (
                    _norm_token(rec.temporada),
                    _norm_token(rec.cliente),
                    _norm_token(rec.producto),
                    _norm_token(rec.especie),
                    _norm_token(rec.variedad),
                )
                self._by_meta[meta_key] = rec

        self._loaded = True

    def resolve(self, payload: Dict[str, Any]) -> Optional[SourceRecord]:
        self._load()

        pdf_filename = (payload.get("pdf_filename") or payload.get("pdf") or "").strip()
        if pdf_filename:
            rec = self._by_pdf.get(pdf_filename.lower())
            if rec:
                return rec

        # fallback por metadata (muy robusto si el filename “raro” no coincide)
        meta_key = (
            _norm_token(payload.get("temporada")),
            _norm_token(payload.get("cliente")),
            _norm_token(payload.get("producto")),
            _norm_token(payload.get("especie")),
            _norm_token(payload.get("variedad")),
        )
        if all(meta_key):
            rec = self._by_meta.get(meta_key)
            if rec:
                return rec

        return None


# Singleton simple (carga una sola vez)
_RESOLVER: Optional[SourceResolver] = None


def get_source_resolver() -> SourceResolver:
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = SourceResolver()
    return _RESOLVER


def format_sources_from_hits(hits: List[Dict[str, Any]]) -> str:
    """
    Formatea las fuentes para Telegram con un estilo limpio y profesional.
    Solo muestra links, sin nombres de archivo ni detalles técnicos.
    """
    try:
        resolver = get_source_resolver()
    except FileNotFoundError:
        return ""

    seen = set()
    lines: List[str] = []

    for h in hits:
        payload = h.get("payload") or {}
        try:
            rec = resolver.resolve(payload)
        except FileNotFoundError:
            return ""
        if not rec:
            continue

        key = rec.pdf.lower()
        if key in seen:
            continue
        seen.add(key)

        # Crear una descripción limpia del estudio
        parts = []
        if rec.temporada:
            parts.append(rec.temporada)
        if rec.producto:
            parts.append(rec.producto)
        if rec.especie:
            esp_var = f"{rec.especie} {rec.variedad}".strip()
            parts.append(esp_var)
        
        description = " - ".join(parts) if parts else "Estudio"

        # Formato limpio para Telegram: viñeta + descripción + link
        chosen_link = rec.url_pdf or rec.url_estudio
        if not chosen_link:
            continue
        lines.append(f"• {description}: {chosen_link}")

    if not lines:
        return ""

    return "───────────────────────\n*Fuentes:*\n" + "\n".join(lines)
