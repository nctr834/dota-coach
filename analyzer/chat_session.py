"""Per-(account, match) chat session storage.

A session is the agent message history for one player's conversation about one
match: the review is the opening turns, follow-up questions append to it. Keyed
"<account_id>-<match_id>" so a thread is resumable.

The in-memory store here is a placeholder; swap _STORE for a NoSQL-backed
implementation (same load/save interface) when deploying.
"""

from anthropic.types import MessageParam


def _key(account_id: int, match_id: int) -> str:
    return f"{account_id}-{match_id}"


class _InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, list[MessageParam]] = {}

    def load(self, account_id: int, match_id: int) -> list[MessageParam] | None:
        return self._data.get(_key(account_id, match_id))

    def save(
        self, account_id: int, match_id: int, messages: list[MessageParam]
    ) -> None:
        self._data[_key(account_id, match_id)] = messages


_STORE = _InMemoryStore()


def load(account_id: int, match_id: int) -> list[MessageParam] | None:
    return _STORE.load(account_id, match_id)


def save(account_id: int, match_id: int, messages: list[MessageParam]) -> None:
    _STORE.save(account_id, match_id, messages)
