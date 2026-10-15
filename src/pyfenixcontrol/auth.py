"""Authentication for the FENIX Control cloud.

The client never deals with credentials directly. It asks an
:class:`AbstractAuth` implementation for a bearer token, so the same client
works with the bundled username/password flow (:class:`PasswordAuth`) or with
tokens managed elsewhere, e.g. Home Assistant's OAuth2 helpers.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.parse
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

import aiohttp

from .const import (
    DEFAULT_CLIENT_ID,
    DEFAULT_REDIRECT_URI,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SCOPES,
    IDENTITY_BASE_URL,
    OEM_CLIENT,
)
from .exceptions import (
    FenixControlAuthError,
    FenixControlConnectionError,
    FenixControlResponseError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)

_HTTP_OK = 200
_HTTP_FOUND = 302
_HTTP_BAD_REQUEST = 400
_HTTP_UNAUTHORIZED = 401
_HTTP_SERVER_ERROR = 500

#: Refresh the access token this many seconds before it expires.
TOKEN_EXPIRY_MARGIN = 60.0
_DEFAULT_TOKEN_LIFETIME = 3600.0

_CSRF_FIELD = "__RequestVerificationToken"
_RETURN_URL_FIELD = "ReturnUrl"


class AbstractAuth(ABC):
    """Supplies bearer tokens to :class:`~pyfenixcontrol.FenixControlClient`."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize with the HTTP session used for all requests."""
        self._session = session

    @property
    def session(self) -> aiohttp.ClientSession:
        """Return the HTTP session shared with the client."""
        return self._session

    @abstractmethod
    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing it if needed.

        Raise :class:`FenixControlAuthError` if no token can be obtained
        without user interaction.
        """

    async def async_invalidate_access_token(self) -> None:
        """Mark the current access token as rejected by the API.

        Called by the client after an HTTP 401. Implementations that can
        refresh on demand should drop the cached token so the next
        :meth:`async_get_access_token` obtains a fresh one.
        """


def generate_pkce_pair() -> tuple[str, str]:
    """Return a PKCE ``(code_verifier, code_challenge)`` pair (S256)."""
    code_verifier = secrets.token_urlsafe(96)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


class _LoginFormParser(HTMLParser):
    """Collect the hidden inputs of the identity server login form."""

    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        values = dict(attrs)
        name = values.get("name")
        value = values.get("value")
        if name in (_CSRF_FIELD, _RETURN_URL_FIELD) and value:
            self.fields.setdefault(name, value)


def parse_login_form(html: str) -> tuple[str, str] | None:
    """Return ``(csrf_token, return_url)`` from the login page, if present."""
    parser = _LoginFormParser()
    parser.feed(html)
    parser.close()
    csrf = parser.fields.get(_CSRF_FIELD)
    return_url = parser.fields.get(_RETURN_URL_FIELD)
    if csrf and return_url:
        return csrf, return_url
    return None


class PasswordAuth(AbstractAuth):
    """OAuth2 authorization-code + PKCE login using account e-mail and password.

    This drives the identity server's login form the same way the FENIX
    Control app does. Tokens are kept in memory and refreshed automatically;
    if the refresh token stops working, a full login is performed again.

    The identity server tracks the login form with cookies, so ``session``
    must have a working cookie jar. Identity-server cookies are cleared before
    every full login so a cached session can never log in a different account.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        client_id: str = DEFAULT_CLIENT_ID,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        scopes: str = DEFAULT_SCOPES,
        identity_url: str = IDENTITY_BASE_URL,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """Initialize the password-based authenticator."""
        super().__init__(session)
        self._username = username
        self._password = password
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._redirect_scheme = urllib.parse.urlsplit(redirect_uri).scheme
        self._scopes = scopes
        self._identity_url = identity_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._lock = asyncio.Lock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0

    @property
    def token_url(self) -> str:
        """Return the OAuth2 token endpoint."""
        return f"{self._identity_url}/connect/token"

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing or logging in as needed."""
        async with self._lock:
            if self._access_token and time.monotonic() < self._expires_at:
                return self._access_token
            if self._refresh_token:
                try:
                    await self._refresh()
                except (FenixControlAuthError, FenixControlResponseError) as err:
                    _LOGGER.debug("Token refresh failed (%s), logging in again", err)
                    await self._login()
            else:
                await self._login()
            if self._access_token is None:  # pragma: no cover - defensive
                msg = "No access token after login"
                raise FenixControlAuthError(msg)
            return self._access_token

    async def async_invalidate_access_token(self) -> None:
        """Force the next token request to refresh."""
        self._expires_at = 0.0

    async def async_login(self) -> None:
        """Perform a full login now, e.g. to validate credentials.

        Raises :class:`FenixControlAuthError` for invalid credentials and
        :class:`FenixControlConnectionError` if the cloud is unreachable.
        """
        async with self._lock:
            await self._login()

    # -- internals ---------------------------------------------------------

    async def _request(
        self, method: str, url: str, **kwargs: Any
    ) -> tuple[int, Mapping[str, str], str]:
        """Send a request and return ``(status, headers, body)``."""
        try:
            async with self._session.request(
                method, url, timeout=self._timeout, **kwargs
            ) as resp:
                return resp.status, resp.headers, await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            msg = f"Error communicating with identity server: {err}"
            raise FenixControlConnectionError(msg) from err

    def _store_tokens(self, body: str, *, require_refresh: bool) -> None:
        try:
            tokens = json.loads(body) if body else {}
        except ValueError as err:
            msg = "Invalid JSON from token endpoint"
            raise FenixControlResponseError(msg) from err
        access = tokens.get("access_token") if isinstance(tokens, dict) else None
        if not access:
            msg = "Token endpoint response has no access_token"
            raise FenixControlResponseError(msg)
        refresh = tokens.get("refresh_token") or (
            None if require_refresh else self._refresh_token
        )
        if not refresh:
            msg = "Token endpoint response has no refresh_token"
            raise FenixControlResponseError(msg)
        lifetime = float(tokens.get("expires_in") or _DEFAULT_TOKEN_LIFETIME)
        self._access_token = access
        self._refresh_token = refresh
        self._expires_at = time.monotonic() + max(lifetime - TOKEN_EXPIRY_MARGIN, 0)

    @staticmethod
    def _raise_for_token_status(status: int, what: str) -> None:
        if status == _HTTP_OK:
            return
        msg = f"{what} failed: HTTP {status}"
        if status in (_HTTP_BAD_REQUEST, _HTTP_UNAUTHORIZED):
            raise FenixControlAuthError(msg)
        raise FenixControlResponseError(msg, status)

    async def _refresh(self) -> None:
        _LOGGER.debug("Refreshing access token")
        status, _, body = await self._request(
            "POST",
            self.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": self._client_id,
            },
        )
        self._raise_for_token_status(status, "Token refresh")
        self._store_tokens(body, require_refresh=False)

    def _unexpected(self, step: str, status: int) -> Exception:
        msg = f"{step}: unexpected HTTP {status}"
        if status >= _HTTP_SERVER_ERROR:
            return FenixControlResponseError(msg, status)
        return FenixControlAuthError(msg)

    def _is_app_redirect(self, url: str) -> bool:
        return urllib.parse.urlsplit(url).scheme == self._redirect_scheme

    async def _login(self) -> None:
        _LOGGER.debug("Starting OAuth2 PKCE login")
        self._access_token = None
        self._refresh_token = None
        self._expires_at = 0.0
        self._session.cookie_jar.clear_domain(
            urllib.parse.urlsplit(self._identity_url).hostname or ""
        )

        code_verifier, code_challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        params = {
            "client_id": self._client_id,
            "response_type": "code id_token",
            "scope": self._scopes,
            "redirect_uri": self._redirect_uri,
            "nonce": secrets.token_urlsafe(32),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "oemclient": OEM_CLIENT,
        }
        authorize_url = (
            f"{self._identity_url}/connect/authorize?{urllib.parse.urlencode(params)}"
        )
        status, headers, _ = await self._request(
            "GET", authorize_url, allow_redirects=False
        )
        location = headers.get("Location")
        if status != _HTTP_FOUND or not location:
            raise self._unexpected("Authorization request", status)  # noqa: EM101, TRY003
        login_url = urllib.parse.urljoin(self._identity_url, location)

        if self._is_app_redirect(login_url):
            callback_url = login_url  # identity server still had a session
        else:
            callback_url = await self._submit_credentials(login_url)

        code = await self._read_authorization_code(callback_url, state)

        status, _, body = await self._request(
            "POST",
            self.token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "code_verifier": code_verifier,
                "client_id": self._client_id,
            },
        )
        self._raise_for_token_status(status, "Token exchange")
        self._store_tokens(body, require_refresh=True)
        _LOGGER.debug("OAuth2 login completed")

    async def _submit_credentials(self, login_url: str) -> str:
        status, _, page = await self._request("GET", login_url)
        if status != _HTTP_OK:
            raise self._unexpected("Login page", status)  # noqa: EM101, TRY003
        form = parse_login_form(page)
        if form is None:
            msg = "Login form not found on identity server page"
            raise FenixControlResponseError(msg, status)
        csrf_token, return_url = form

        status, headers, _ = await self._request(
            "POST",
            login_url,
            data={
                "ReturnUrl": return_url,
                "Username": self._username,
                "Password": self._password,
                "button": "login",
                _CSRF_FIELD: csrf_token,
            },
            allow_redirects=False,
        )
        location = headers.get("Location")
        if status == _HTTP_FOUND and location:
            return urllib.parse.urljoin(self._identity_url, location)
        if status == _HTTP_OK:
            # The login page is rendered again with a validation message.
            msg = "Invalid username or password"
            raise FenixControlAuthError(msg)
        raise self._unexpected("Login form submission", status)  # noqa: EM101, TRY003

    async def _read_authorization_code(self, callback_url: str, state: str) -> str:
        if not self._is_app_redirect(callback_url):
            # /connect/authorize/callback answers with a redirect to the app URI.
            _, headers, _ = await self._request(
                "GET", callback_url, allow_redirects=False
            )
            callback_url = headers.get("Location", callback_url)

        fragment = urllib.parse.parse_qs(urllib.parse.urlsplit(callback_url).fragment)
        code = fragment.get("code", [None])[0]
        id_token = fragment.get("id_token", [None])[0]
        returned_state = fragment.get("state", [None])[0]
        if returned_state != state:
            msg = "OAuth2 state mismatch in authorization response"
            raise FenixControlAuthError(msg)
        if not code or not id_token:
            msg = "Authorization response is missing code or id_token"
            raise FenixControlAuthError(msg)
        return code
