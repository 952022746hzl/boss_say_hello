from dataclasses import dataclass
from typing import Protocol, AsyncIterator
from pathlib import Path
from boss_say_hello.domain import Job


class BrowserFailure(RuntimeError):
    pass


class ActionBlocked(BrowserFailure):
    """No side effect has been attempted because pause was requested."""


@dataclass(frozen=True)
class SessionInfo:
    account_id: str | None
    logged_in: bool


@dataclass(frozen=True)
class AttachmentInfo:
    id: str
    name: str


@dataclass(frozen=True)
class BrowserCapabilities:
    greeting_limit: int | None
    initial_contact_behavior: str


@dataclass(frozen=True)
class ActionResult:
    outcome: str
    detail: str


class BrowserPort(Protocol):
    async def open(self, profile: Path) -> None: ...
    async def session(self) -> SessionInfo: ...
    def iter_recommended(self) -> AsyncIterator[Job]: ...
    async def attachments(self) -> list[AttachmentInfo]: ...
    async def capabilities(self) -> BrowserCapabilities: ...
    async def close(self) -> None: ...
    def bind_control(self, control) -> None: ...
    async def refresh_job(self, job: Job) -> Job: ...
    async def open_contact(self, job: Job) -> ActionResult: ...
    async def send_text(self, job: Job, text: str) -> ActionResult: ...
    async def send_attachment(self, job: Job, attachment_id: str) -> ActionResult: ...
    async def return_to_list(self) -> bool: ...
