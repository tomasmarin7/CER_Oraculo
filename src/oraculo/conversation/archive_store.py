from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .modelos import SesionChat


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _archive_base_dir() -> Path:
    return _project_root() / "data" / "conversations"


def _iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=UTC).isoformat()


def _session_file_path(session_id: str, started_at_ts: int) -> Path:
    dt = datetime.fromtimestamp(int(started_at_ts), tz=UTC)
    day_dir = _archive_base_dir() / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.day:02d}"
    day_dir.mkdir(parents=True, exist_ok=True)
    safe_session_id = session_id.replace("/", "_").replace(" ", "_")
    return day_dir / f"{safe_session_id}.json"


def _build_payload(
    sesion: SesionChat,
    status: str,
    ended_at_ts: int | None = None,
    close_reason: str = "",
) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    for msg in sesion.mensajes:
        turns.append(
            {
                "ts": _iso(msg.ts),
                "role": msg.rol,
                "text": msg.texto,
            }
        )

    router_trace = sesion.flow_data.get("router_trace")
    return {
        "session_id": sesion.session_id,
        "user_id": sesion.user_id,
        "channel": "telegram",
        "started_at": _iso(sesion.started_at_ts),
        "ended_at": _iso(ended_at_ts),
        "status": status,
        "state": sesion.estado,
        "last_activity_ts": _iso(sesion.last_activity_ts),
        "expire_at": _iso(sesion.expire_at),
        "turns": turns,
        "meta": {
            "last_rag_used": sesion.last_rag_used,
            "last_sources": list(sesion.last_sources),
            "router_trace": list(router_trace) if isinstance(router_trace, list) else [],
            "close_reason": close_reason,
        },
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def persist_session_archive(sesion: SesionChat) -> None:
    if not sesion.session_id:
        return
    path = _session_file_path(sesion.session_id, sesion.started_at_ts)
    payload = _build_payload(sesion, status="open")
    _atomic_write_json(path, payload)


def close_session_archive(sesion: SesionChat, reason: str = "") -> None:
    if not sesion.session_id:
        return
    path = _session_file_path(sesion.session_id, sesion.started_at_ts)
    payload = _build_payload(
        sesion,
        status="closed",
        ended_at_ts=sesion.last_activity_ts,
        close_reason=reason,
    )
    _atomic_write_json(path, payload)
