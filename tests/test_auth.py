"""Tests for PasswordAuth (OAuth2 PKCE login against the identity server)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Iterator
from unittest.mock import patch

import aiohttp
import pytest
from yarl import URL

from pyfenixcontrol import (
    FenixControlAuthError,
    FenixControlConnectionError,
    FenixControlResponseError,
    PasswordAuth,
)
from pyfenixcontrol.auth import generate_pkce_pair, parse_login_form

from .conftest import FakeCloud

STATE = "fixed-random"
AUTHORIZE = "/id/connect/authorize"
LOGIN = "/id/Account/Login"
CALLBACK = "/id/connect/authorize/callback"
TOKEN = "/id/connect/token"

LOGIN_PAGE = """
<html><body><form method="post">
  <input type="hidden" name="ReturnUrl" value="/connect/authorize/callback?x=1">
  <input name="Username"><input name="Password" type="password">
  <input name="__RequestVerificationToken" type="hidden" value="csrf-123">
  <input name="__RequestVerificationToken" type="hidden" value="ignored-dup">
</form></body></html>
"""
APP_REDIRECT = f"fenix://callback#code=auth-code&id_token=idt&state={STATE}"


@pytest.fixture(autouse=True)
def fixed_random() -> Iterator[None]:
    with patch("pyfenixcontrol.auth.secrets.token_urlsafe", return_value=STATE):
        yield


@pytest.fixture
def password_auth(session: aiohttp.ClientSession, cloud: FakeCloud) -> PasswordAuth:
    return PasswordAuth(
        session,
        "user@example.com",
        "s3cret",
        client_id="my-client",
        identity_url=f"{cloud.url}/id/",
    )


def add_login_flow(cloud: FakeCloud, *, access: str = "access-1") -> None:
    cloud.add("GET", AUTHORIZE, status=302, headers={"Location": f"{LOGIN}?r=1"})
    cloud.add("GET", LOGIN, text=LOGIN_PAGE)
    cloud.add("POST", LOGIN, status=302, headers={"Location": f"{CALLBACK}?x=1"})
    cloud.add("GET", CALLBACK, status=302, headers={"Location": APP_REDIRECT})
    cloud.add(
        "POST",
        TOKEN,
        payload={
            "access_token": access,
            "refresh_token": "refresh-1",
            "expires_in": 3600,
        },
    )


def test_parse_login_form() -> None:
    assert parse_login_form(LOGIN_PAGE) == (
        "csrf-123",
        "/connect/authorize/callback?x=1",
    )
    assert parse_login_form("<input name='ReturnUrl' value='x'>") is None
    assert parse_login_form("<p>no form</p>") is None


def test_generate_pkce_pair() -> None:
    verifier, challenge = generate_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.decode().rstrip("=")


async def test_full_login(password_auth: PasswordAuth, cloud: FakeCloud) -> None:
    add_login_flow(cloud)
    assert await password_auth.async_get_access_token() == "access-1"

    [authorize] = cloud.requests_to("GET", AUTHORIZE)
    assert authorize.query["client_id"] == "my-client"
    assert authorize.query["oemclient"] == "fenix"
    assert authorize.query["code_challenge_method"] == "S256"
    assert authorize.query["redirect_uri"] == "fenix://callback"

    [login] = cloud.requests_to("POST", LOGIN)
    assert login.form == {
        "ReturnUrl": "/connect/authorize/callback?x=1",
        "Username": "user@example.com",
        "Password": "s3cret",
        "button": "login",
        "__RequestVerificationToken": "csrf-123",
    }

    [token] = cloud.requests_to("POST", TOKEN)
    assert token.form["grant_type"] == "authorization_code"
    assert token.form["code"] == "auth-code"
    assert token.form["code_verifier"] == STATE
    assert token.form["client_id"] == "my-client"

    # Cached: no further requests.
    count = len(cloud.requests)
    assert await password_auth.async_get_access_token() == "access-1"
    assert len(cloud.requests) == count


async def test_login_with_cached_identity_session(
    password_auth: PasswordAuth, cloud: FakeCloud
) -> None:
    cloud.add("GET", AUTHORIZE, status=302, headers={"Location": APP_REDIRECT})
    cloud.add("POST", TOKEN, payload={"access_token": "a", "refresh_token": "r"})
    await password_auth.async_login()
    assert await password_auth.async_get_access_token() == "a"
    assert not cloud.requests_to("GET", LOGIN)


async def test_login_clears_identity_cookies(
    password_auth: PasswordAuth, cloud: FakeCloud, session: aiohttp.ClientSession
) -> None:
    session.cookie_jar.update_cookies(
        {"idsrv.session": "other-account"}, response_url=URL(cloud.url)
    )
    assert any(c.key == "idsrv.session" for c in session.cookie_jar)
    add_login_flow(cloud)
    await password_auth.async_login()
    assert "idsrv.session" not in cloud.requests[0].headers.get("Cookie", "")


async def test_invalid_credentials(
    password_auth: PasswordAuth, cloud: FakeCloud
) -> None:
    cloud.add("GET", AUTHORIZE, status=302, headers={"Location": LOGIN})
    cloud.add("GET", LOGIN, text=LOGIN_PAGE)
    cloud.add("POST", LOGIN, text=LOGIN_PAGE)  # re-rendered with an error
    with pytest.raises(FenixControlAuthError, match="Invalid username or password"):
        await password_auth.async_get_access_token()


@pytest.mark.parametrize(
    ("step", "status", "error"),
    [
        ("authorize", 500, FenixControlResponseError),
        ("authorize", 400, FenixControlAuthError),
        ("authorize", 200, FenixControlAuthError),
        ("page", 503, FenixControlResponseError),
        ("page", 404, FenixControlAuthError),
        ("submit", 500, FenixControlResponseError),
        ("submit", 403, FenixControlAuthError),
    ],
)
async def test_login_step_failures(
    password_auth: PasswordAuth,
    cloud: FakeCloud,
    step: str,
    status: int,
    error: type[Exception],
) -> None:
    if step == "authorize":
        cloud.add("GET", AUTHORIZE, status=status)
    else:
        cloud.add("GET", AUTHORIZE, status=302, headers={"Location": LOGIN})
        if step == "page":
            cloud.add("GET", LOGIN, status=status)
        else:
            cloud.add("GET", LOGIN, text=LOGIN_PAGE)
            cloud.add("POST", LOGIN, status=status)
    with pytest.raises(error):
        await password_auth.async_login()


async def test_login_form_missing(
    password_auth: PasswordAuth, cloud: FakeCloud
) -> None:
    cloud.add("GET", AUTHORIZE, status=302, headers={"Location": LOGIN})
    cloud.add("GET", LOGIN, text="<html>maintenance</html>")
    with pytest.raises(FenixControlResponseError, match="Login form not found"):
        await password_auth.async_login()


@pytest.mark.parametrize(
    ("fragment", "match"),
    [
        ("code=c&id_token=i&state=other", "state mismatch"),
        (f"code=c&state={STATE}", "missing code or id_token"),
        (f"id_token=i&state={STATE}", "missing code or id_token"),
    ],
)
async def test_bad_authorization_response(
    password_auth: PasswordAuth, cloud: FakeCloud, fragment: str, match: str
) -> None:
    cloud.add(
        "GET",
        AUTHORIZE,
        status=302,
        headers={"Location": f"fenix://callback#{fragment}"},
    )
    with pytest.raises(FenixControlAuthError, match=match):
        await password_auth.async_login()


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 400, "payload": {"error": "invalid_grant"}}, FenixControlAuthError),
        ({"status": 502}, FenixControlResponseError),
        ({"payload": {"access_token": "a"}}, FenixControlResponseError),
        ({"payload": {"refresh_token": "r"}}, FenixControlResponseError),
        ({"payload": ["a"]}, FenixControlResponseError),
        ({"text": "not json"}, FenixControlResponseError),
        ({"text": ""}, FenixControlResponseError),
    ],
)
async def test_token_exchange_failures(
    password_auth: PasswordAuth,
    cloud: FakeCloud,
    kwargs: dict[str, object],
    error: type[Exception],
) -> None:
    cloud.add("GET", AUTHORIZE, status=302, headers={"Location": APP_REDIRECT})
    cloud.add("POST", TOKEN, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(error):
        await password_auth.async_login()


async def test_refresh_when_expired(
    password_auth: PasswordAuth, cloud: FakeCloud
) -> None:
    add_login_flow(cloud)
    await password_auth.async_login()
    cloud.add("POST", TOKEN, payload={"access_token": "access-2", "expires_in": 3600})
    await password_auth.async_invalidate_access_token()

    assert await password_auth.async_get_access_token() == "access-2"
    refresh = cloud.requests_to("POST", TOKEN)[-1]
    assert refresh.form == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-1",
        "client_id": "my-client",
    }
    # Refresh token kept when the response omits it.
    cloud.add("POST", TOKEN, payload={"access_token": "access-3"})
    await password_auth.async_invalidate_access_token()
    assert await password_auth.async_get_access_token() == "access-3"
    assert cloud.requests_to("POST", TOKEN)[-1].form["refresh_token"] == "refresh-1"


async def test_short_lived_token_is_refreshed_next_time(
    password_auth: PasswordAuth, cloud: FakeCloud
) -> None:
    cloud.add("GET", AUTHORIZE, status=302, headers={"Location": APP_REDIRECT})
    cloud.add(
        "POST",
        TOKEN,
        payload={"access_token": "a", "refresh_token": "r", "expires_in": 30},
    )
    await password_auth.async_login()
    cloud.add("POST", TOKEN, payload={"access_token": "b"})
    assert await password_auth.async_get_access_token() == "b"


@pytest.mark.parametrize("refresh_status", [400, 401, 500])
async def test_failed_refresh_falls_back_to_login(
    password_auth: PasswordAuth, cloud: FakeCloud, refresh_status: int
) -> None:
    add_login_flow(cloud)
    await password_auth.async_login()
    cloud.add("POST", TOKEN, status=refresh_status)
    add_login_flow(cloud, access="access-relogin")
    await password_auth.async_invalidate_access_token()
    assert await password_auth.async_get_access_token() == "access-relogin"
    assert len(cloud.requests_to("GET", AUTHORIZE)) == 2


async def test_concurrent_callers_share_one_login(
    password_auth: PasswordAuth, cloud: FakeCloud
) -> None:
    add_login_flow(cloud)
    tokens = await asyncio.gather(
        *(password_auth.async_get_access_token() for _ in range(5))
    )
    assert tokens == ["access-1"] * 5
    assert len(cloud.requests_to("GET", AUTHORIZE)) == 1


@pytest.mark.parametrize("exc", [aiohttp.ClientConnectionError(), TimeoutError()])
async def test_connection_error_is_not_auth_error(
    password_auth: PasswordAuth, exc: Exception
) -> None:
    with (
        patch.object(password_auth.session, "request", side_effect=exc),
        pytest.raises(FenixControlConnectionError),
    ):
        await password_auth.async_get_access_token()


async def test_connection_error_during_refresh_propagates(
    password_auth: PasswordAuth, cloud: FakeCloud
) -> None:
    add_login_flow(cloud)
    await password_auth.async_login()
    await password_auth.async_invalidate_access_token()
    with (
        patch.object(
            password_auth.session,
            "request",
            side_effect=aiohttp.ClientConnectionError(),
        ),
        pytest.raises(FenixControlConnectionError),
    ):
        await password_auth.async_get_access_token()


def test_token_url(password_auth: PasswordAuth, cloud: FakeCloud) -> None:
    assert password_auth.token_url == f"{cloud.url}/id/connect/token"
