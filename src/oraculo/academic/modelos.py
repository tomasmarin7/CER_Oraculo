from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AcademicResearchResult:
    text: str
    task_id: str | None = None
    has_successful_answer: bool | None = None
