from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RespuestaOraculo:
    texto: str
    rag_usado: str = "none"
    fuentes: list[str] = field(default_factory=list)
