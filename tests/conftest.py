"""Shared fixtures for pyfenixcontrol tests.

Tests talk to a real local aiohttp server (:class:`FakeCloud`) that plays the
API and identity server, so the full HTTP stack (redirects, headers, bodies)
is exercised without mocking aiohttp internals.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from pyfenixcontrol import AbstractAuth, FenixControlClient

USER_ID = "user-sub-1"


@dataclass
class Recorded:
    """A request received by the fake cloud."""

    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: str

    @property
    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def form(self) -> dict[str, str]:
        from urllib.parse import parse_qsl  # noqa: PLC0415

        return dict(parse_qsl(self.body))


@dataclass
class _Reply:
    status: int = 200
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class FakeCloud:
    """Queue canned replies per ``(method, path)`` and record requests."""

    def __init__(self) -> None:
        self._replies: dict[tuple[str, str], deque[_Reply]] = defaultdict(deque)
        self.requests: list[Recorded] = []
        self.url = ""

    def add(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        payload: Any = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = (
            text
            if text is not None
            else ("" if payload is None else json.dumps(payload))
        )
        hdrs = dict(headers or {})
        if payload is not None:
            hdrs.setdefault("Content-Type", "application/json")
        self._replies[(method, path)].append(_Reply(status, body, hdrs))

    def requests_to(self, method: str, path: str) -> list[Recorded]:
        return [r for r in self.requests if r.method == method and r.path == path]

    async def handle(self, request: web.Request) -> web.Response:
        self.requests.append(
            Recorded(
                method=request.method,
                path=request.path,
                query=dict(request.query),
                headers=dict(request.headers),
                body=await request.text(),
            )
        )
        queue = self._replies.get((request.method, request.path))
        if not queue:
            return web.Response(
                status=599, text=f"unexpected {request.method} {request.path}"
            )
        reply = queue.popleft()
        return web.Response(status=reply.status, text=reply.body, headers=reply.headers)


class StaticAuth(AbstractAuth):
    """Auth returning a fixed token; counts invalidations."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        super().__init__(session)
        self.invalidations = 0

    async def async_get_access_token(self) -> str:
        return f"token-{self.invalidations}"

    async def async_invalidate_access_token(self) -> None:
        self.invalidations += 1


@pytest.fixture
async def cloud() -> AsyncIterator[FakeCloud]:
    fake = FakeCloud()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    server = TestServer(app)
    await server.start_server()
    fake.url = str(server.make_url("")).rstrip("/")
    yield fake
    await server.close()


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True)
    ) as client_session:
        yield client_session


@pytest.fixture
def auth(session: aiohttp.ClientSession) -> StaticAuth:
    return StaticAuth(session)


@pytest.fixture
def client(auth: StaticAuth, cloud: FakeCloud) -> FenixControlClient:
    fenix = FenixControlClient(
        auth, api_url=f"{cloud.url}/api", identity_url=f"{cloud.url}/id"
    )
    fenix._user_id = USER_ID
    return fenix


@pytest.fixture(autouse=True)
def mock_sleep() -> Iterator[AsyncMock]:
    with patch("pyfenixcontrol.client.asyncio.sleep", new=AsyncMock()) as sleep:
        yield sleep
