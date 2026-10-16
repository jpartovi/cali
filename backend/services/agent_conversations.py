"""Conversations behind proposals the user hasn't accepted yet, for follow-up requests.

When the agent proposes a create/update/delete, the app shows it until the user taps check
or x. Meanwhile the user can send a follow-up ("actually, the day after", "add Henry too",
or something else entirely): the agent picks up the same conversation, so it knows what it proposed.

Kept in this process's memory (the backend runs one uvicorn process): a proposal is answered
within minutes, and one lost to a deploy just means asking again.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# A proposal left on screen longer than this can't be followed up
CONVERSATION_TTL_SECONDS = 60 * 60
MAX_CONVERSATIONS = 500


@dataclass
class AgentConversation:
    user_id: str
    messages: List[Any]  # The agent's full message history (LangChain messages), system prompt included
    prompt_kinds: Optional[List[str]]  # Request kinds the prompt covers so far; None for the full prompt
    prepass_people: Optional[dict]  # Everyone the requests so far talked about (checks guests on the next proposal)
    created_at: float = field(default_factory=time.time)


_lock = threading.Lock()
_conversations: Dict[str, AgentConversation] = {}


def _prune(now: float) -> None:
    for key in [k for k, c in _conversations.items() if now - c.created_at > CONVERSATION_TTL_SECONDS]:
        del _conversations[key]
    while len(_conversations) > MAX_CONVERSATIONS:
        del _conversations[next(iter(_conversations))]  # Oldest first (insertion order)


def save_conversation(request_id: str, conversation: AgentConversation) -> None:
    with _lock:
        _conversations[request_id] = conversation
        _prune(time.time())


def get_conversation(request_id: str, user_id: str) -> Optional[AgentConversation]:
    """The conversation behind a proposal, if it's this user's and hasn't expired."""
    with _lock:
        _prune(time.time())
        conversation = _conversations.get(request_id)
    if conversation is None or conversation.user_id != user_id:
        return None
    return conversation


def _person_key(person: Dict[str, Any]) -> str:
    return (person.get("email") or person.get("contact_id") or person.get("display_name") or "").strip().lower()


def merge_prepass_people(earlier: Optional[dict], latest: Optional[dict]) -> Optional[dict]:
    """People from the follow-up first, then anyone the earlier requests found that it didn't."""
    if not earlier or not latest:
        return latest or earlier
    seen = {
        _person_key(p)
        for p in [*latest.get("people", []), *latest.get("maybe", []), *(p for g in latest.get("ambiguous", []) for p in g)]
    }
    return {
        **latest,
        "status": "found",
        "people": latest.get("people", []) + [p for p in earlier.get("people", []) if _person_key(p) not in seen],
        "ambiguous": latest.get("ambiguous", [])
        + [g for g in earlier.get("ambiguous", []) if not any(_person_key(p) in seen for p in g)],
        "maybe": latest.get("maybe", []) + [p for p in earlier.get("maybe", []) if _person_key(p) not in seen],
    }
