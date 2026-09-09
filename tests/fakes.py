"""Test doubles. No network, no tokens, no sleeping.

The clock is injected rather than real, so the cache and grace-window tests
finish instantly. Tests that need ``sleep`` do not get run, and a test that
does not get run protects nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, ChatMemberMember, Message, User


class Clock:
    """A monotonic clock that only moves when a test says so."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Boom(Exception):
    """Whatever a transport raises when Telegram is unreachable."""


class FakeApi:
    """Scripted ``getChatMember`` / ``getChatAdministrators``.

    ``members`` maps ``(chat_id, user_id)`` to a status string, or to a mapping
    to be returned verbatim, or to an exception to be raised. Unknown pairs
    answer ``"left"``, which is what Telegram does for someone who was never
    in the chat.
    """

    def __init__(
        self,
        members: dict[tuple[int, int], Any] | None = None,
        *,
        admins: dict[int, list[Any]] | None = None,
        broken: bool = False,
    ) -> None:
        self.members = members or {}
        self.admins = admins or {}
        self.broken = broken
        self.calls: list[tuple[int, int]] = []
        self.admin_calls: list[int] = []

    async def get_chat_member(self, chat_id: int, user_id: int) -> Any:
        self.calls.append((chat_id, user_id))
        if self.broken:
            raise Boom("telegram unreachable")
        answer = self.members.get((chat_id, user_id), "left")
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, str):
            return {"status": answer}
        return answer

    async def get_chat_administrators(self, chat_id: int) -> Any:
        self.admin_calls.append(chat_id)
        if self.broken:
            raise Boom("telegram unreachable")
        return self.admins.get(chat_id, [])


class RecordingSession(BaseSession):
    """An aiogram session that answers from a script and remembers the calls.

    Needed because aiogram's models are frozen: the only way to see what
    ``message.answer(...)`` would have sent is to catch it at the session.
    """

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        #: ``(chat_id, user_id)`` -> status string, for getChatMember
        self.members: dict[tuple[int, int], str] = {}
        #: chat_id -> what getChatAdministrators should answer
        self.administrators: dict[int, list[Any]] = {}
        self.broken = False

    @property
    def sent_texts(self) -> list[str]:
        return [getattr(r, "text", "") for r in self.requests if hasattr(r, "text")]

    def method_names(self) -> list[str]:
        return [type(r).__name__ for r in self.requests]

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        name = type(method).__name__
        self.requests.append(method)
        if name == "GetChatMember":
            if self.broken:
                raise Boom("telegram unreachable")
            status = self.members.get(
                (int(method.chat_id), int(method.user_id)),
                "left",  # type: ignore[attr-defined]
            )
            return ChatMemberMember.model_construct(
                status=status, user=User(id=1, is_bot=False, first_name="x")
            )
        if name == "GetChatAdministrators":
            if self.broken:
                raise Boom("telegram unreachable")
            return self.administrators.get(int(method.chat_id), [])  # type: ignore[attr-defined]
        if name == "SendMessage":
            return Message.model_construct(
                message_id=1,
                date=datetime.now(timezone.utc),
                chat=Chat(id=1, type="private"),
            )
        return True

    async def stream_content(  # type: ignore[override]
        self, url: str, **kwargs: Any
    ) -> Any:  # pragma: no cover - never used in tests
        raise NotImplementedError


def mocked_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    # A syntactically valid token that is not, and never was, a real one.
    return Bot(token="42:TESTTESTTESTTESTTESTTESTTEST", session=session), session


def user(user_id: int, username: str | None = None) -> User:
    return User(
        id=user_id,
        is_bot=False,
        first_name=f"User{user_id}",
        username=username,
    )


def chat(chat_id: int, kind: str = "private") -> Chat:
    return Chat(id=chat_id, type=kind)


def message(
    *,
    text: str = "hello",
    from_user: User | None = None,
    in_chat: Chat | None = None,
    **extra: Any,
) -> Message:
    return Message.model_construct(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=in_chat or chat(1),
        from_user=from_user or user(1),
        text=text,
        **extra,
    )
