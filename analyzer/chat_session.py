"""Per-(account, match) chat session storage.

A session is the agent message history for one player's conversation about one
match: the review is the opening turns, follow-up questions append to it. One
JSON file per session under data/sessions/ so threads survive server restarts.
Assistant blocks arrive as SDK objects and are serialized via pydantic; they
load back as plain dicts, which every consumer already handles.
"""

import json
from pathlib import Path

from anthropic.types import MessageParam

_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"


def _path(account_id: int, match_id: int) -> Path:
    return _DIR / f"{account_id}-{match_id}.json"


def _jsonable(o):
    if hasattr(o, "model_dump"):
        return o.model_dump()
    raise TypeError(f"not JSON serializable: {type(o)}")


def load(account_id: int, match_id: int) -> list[MessageParam] | None:
    try:
        return json.loads(_path(account_id, match_id).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save(account_id: int, match_id: int, messages: list[MessageParam]) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    _path(account_id, match_id).write_text(
        json.dumps(messages, default=_jsonable)
    )


def list_matches(account_id: int) -> list[int]:
    """Match ids with a session for this account, most recently updated first."""
    files = sorted(
        _DIR.glob(f"{account_id}-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [int(p.stem.split("-", 1)[1]) for p in files]
