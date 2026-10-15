"""Asynchronous client for the FENIX Control cloud API."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiohttp

from .const import (
    API_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_REQUEST_TIMEOUT,
    HOLIDAY_EPOCH_DATE,
    IDENTITY_BASE_URL,
    EnergyPeriod,
    HolidayMode,
    PresetMode,
)
from .exceptions import (
    FenixControlAuthError,
    FenixControlConnectionError,
    FenixControlError,
    FenixControlResponseError,
)
from .models import (
    Device,
    EnergyMetric,
    Installation,
    encode_temperature,
    format_holiday_date,
)

if TYPE_CHECKING:
    from .auth import AbstractAuth

_LOGGER = logging.getLogger(__name__)

_HTTP_OK = 200
_HTTP_NO_CONTENT = 204
_HTTP_SUCCESS_MAX = 300
_HTTP_UNAUTHORIZED = 401
_HTTP_SERVER_ERROR = 500

#: Error bodies may echo account identifiers; only log a short prefix at debug.
_BODY_LOG_LIMIT = 256

_CONFIG_REPLACE_PATH = "/iotmanagement/v1/devices/twin/properties/config/replace"
_INSTALLATION_JOB_PATH = "/iotmanagement/v1/devices/installationcontroljob"


def _format_api_date(value: datetime) -> str:
    """Format a datetime for consumption endpoints (UTC, naive values = UTC)."""
    utc = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class FenixControlClient:
    """Client for FENIX TFT WiFi thermostats via the FENIX Control cloud.

    All network access goes through ``auth.session``; the client owns no
    resources and needs no explicit close.
    """

    def __init__(
        self,
        auth: AbstractAuth,
        *,
        api_url: str = API_BASE_URL,
        identity_url: str = IDENTITY_BASE_URL,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """Initialize the client.

        ``max_retries`` applies to HTTP 5xx responses only, with exponential
        backoff (1 s, 2 s, ...). Client errors are never retried; a single
        HTTP 401 triggers one token refresh and a retry.
        """
        self._auth = auth
        self._api_url = api_url.rstrip("/")
        self._identity_url = identity_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._max_retries = max_retries
        self._user_id: str | None = None

    @property
    def user_id(self) -> str | None:
        """Account subject id (``sub``), available after :meth:`get_user_id`."""
        return self._user_id

    # -- transport ---------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        empty_on_no_content: bool = False,
    ) -> Any:
        """Send an authenticated request and return the decoded JSON body."""
        auth_retried = False
        attempt = 0
        while True:
            token = await self._auth.async_get_access_token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                async with self._auth.session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    timeout=self._timeout,
                    allow_redirects=False,
                ) as resp:
                    status = resp.status
                    body = await resp.text()
            except (aiohttp.ClientError, TimeoutError) as err:
                msg = f"Error communicating with FENIX cloud: {err}"
                raise FenixControlConnectionError(msg) from err

            if _HTTP_OK <= status < _HTTP_SUCCESS_MAX:
                if status == _HTTP_NO_CONTENT or not body:
                    return [] if empty_on_no_content else None
                try:
                    return json.loads(body)
                except ValueError as err:
                    msg = f"{method} {url}: invalid JSON response"
                    raise FenixControlResponseError(msg, status) from err

            _LOGGER.debug(
                "%s %s -> HTTP %s: %s", method, url, status, body[:_BODY_LOG_LIMIT]
            )

            if status == _HTTP_UNAUTHORIZED:
                if auth_retried:
                    msg = f"{method} {url}: HTTP 401 with a fresh token"
                    raise FenixControlAuthError(msg)
                auth_retried = True
                await self._auth.async_invalidate_access_token()
                continue

            if status >= _HTTP_SERVER_ERROR and attempt < self._max_retries:
                delay = 2**attempt
                attempt += 1
                _LOGGER.debug(
                    "HTTP %s, retry %d/%d in %ds",
                    status,
                    attempt,
                    self._max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            msg = f"{method} {url}: HTTP {status}"
            raise FenixControlResponseError(msg, status)

    async def _get(self, path: str, *, empty_on_no_content: bool = False) -> Any:
        return await self._request(
            "GET", f"{self._api_url}{path}", empty_on_no_content=empty_on_no_content
        )

    async def _put(self, path: str, body: dict[str, Any]) -> Any:
        return await self._request("PUT", f"{self._api_url}{path}", json_body=body)

    async def _require_user_id(self) -> str:
        return self._user_id or await self.get_user_id()

    # -- account & topology ------------------------------------------------

    async def get_user_id(self) -> str:
        """Fetch and cache the account subject id from OIDC ``userinfo``."""
        data = await self._request("GET", f"{self._identity_url}/connect/userinfo")
        sub = data.get("sub") if isinstance(data, dict) else None
        if not sub:
            msg = "userinfo response has no 'sub'"
            raise FenixControlResponseError(msg)
        self._user_id = str(sub)
        return self._user_id

    async def get_installations(self) -> list[Installation]:
        """Return the installations (homes) the account administers."""
        user_id = await self._require_user_id()
        data = await self._get(
            f"/businessmodule/v1/installations/admins/{user_id}",
            empty_on_no_content=True,
        )
        if not isinstance(data, list):
            msg = "installations response is not a list"
            raise FenixControlResponseError(msg)
        return [Installation.from_api(i) for i in data if isinstance(i, dict)]

    async def get_device_properties(self, device_id: str) -> dict[str, Any]:
        """Return the raw device twin properties of one thermostat."""
        data = await self._get(
            f"/iotmanagement/v1/configuration/{device_id}/{device_id}/v1/content/"
        )
        if not isinstance(data, dict):
            msg = f"properties of device {device_id} are not an object"
            raise FenixControlResponseError(msg)
        return data

    async def get_devices(
        self, installations: list[Installation] | None = None
    ) -> list[Device]:
        """Return the state of every thermostat.

        A device whose properties cannot be fetched is skipped (and logged);
        authentication and connection errors are raised, since they affect
        the whole account.
        """
        if installations is None:
            installations = await self.get_installations()
        devices: list[Device] = []
        for installation in installations:
            for room in installation.rooms:
                for device_id in room.device_ids:
                    try:
                        props = await self.get_device_properties(device_id)
                    except (FenixControlAuthError, FenixControlConnectionError):
                        raise
                    except FenixControlError as err:
                        _LOGGER.warning("Skipping device %s: %s", device_id, err)
                        continue
                    devices.append(
                        Device.from_api(device_id, props, installation, room)
                    )
        return devices

    async def trigger_installation_update(self, installation_id: str) -> None:
        """Ask the thermostats of an installation to report fresh values.

        This is what the app sends when it is opened ("user connected").
        """
        await self._put(
            "/iotmanagement/v1/devices/userconnected",
            {"A1": await self._require_user_id(), "In": installation_id},
        )

    # -- control -----------------------------------------------------------

    async def set_target_temperature(self, device_id: str, celsius: float) -> None:
        """Set the target temperature (°C); switches the device to manual."""
        await self._put(
            _CONFIG_REPLACE_PATH,
            {
                "Id_deviceId": device_id,
                "S1": device_id,
                "configurationVersion": "v1.0",
                "data": [
                    {"wattsType": "Dm", "wattsTypeValue": int(PresetMode.MANUAL)},
                    {"wattsType": "Ma", "wattsTypeValue": encode_temperature(celsius)},
                ],
            },
        )

    async def set_preset_mode(self, device_id: str, mode: PresetMode | int) -> None:
        """Set the operating mode of a thermostat."""
        try:
            mode = PresetMode(mode)
        except ValueError as err:
            msg = f"Invalid preset mode: {mode}"
            raise ValueError(msg) from err
        await self._put(
            _CONFIG_REPLACE_PATH,
            {
                "Id_deviceId": device_id,
                "S1": device_id,
                "configurationVersion": "v1.0",
                "data": [{"wattsType": "Dm", "wattsTypeValue": int(mode)}],
            },
        )

    async def set_holiday_schedule(
        self,
        installation_id: str,
        start: datetime,
        end: datetime,
        mode: HolidayMode | int,
    ) -> None:
        """Schedule a holiday for a whole installation.

        ``start``/``end`` are written as wall-clock time of the installation;
        pass naive or local datetimes, they are not converted.
        """
        mode = HolidayMode(mode)
        if mode is HolidayMode.NONE:
            msg = "Use cancel_holiday_schedule() to clear a holiday"
            raise ValueError(msg)
        if end <= start:
            msg = "Holiday end must be after start"
            raise ValueError(msg)
        await self._put_holiday(
            installation_id,
            format_holiday_date(start),
            format_holiday_date(end),
            int(mode),
        )

    async def cancel_holiday_schedule(self, installation_id: str) -> None:
        """Remove the holiday schedule of an installation."""
        await self._put_holiday(
            installation_id, HOLIDAY_EPOCH_DATE, HOLIDAY_EPOCH_DATE, 0
        )

    async def _put_holiday(
        self, installation_id: str, start: str, end: str, mode: int
    ) -> None:
        await self._put(
            _INSTALLATION_JOB_PATH,
            {
                "In": installation_id,
                "A1": await self._require_user_id(),
                "data": [
                    {"timestamp": None, "wattsType": "H1", "wattsTypeValue": start},
                    {"timestamp": None, "wattsType": "H2", "wattsTypeValue": end},
                    {
                        "timestamp": None,
                        "wattsType": "H3",
                        "wattsTypeValue": [mode, 0, 0],
                    },
                ],
            },
        )

    # -- energy ------------------------------------------------------------

    async def get_room_energy(
        self,
        installation_id: str,
        room_id: str,
        start: datetime,
        end: datetime,
        period: EnergyPeriod | str = EnergyPeriod.HOUR,
    ) -> list[EnergyMetric]:
        """Return energy consumption (Wh) of a room between ``start`` and ``end``.

        Datetimes are converted to UTC; naive values are taken as UTC.
        """
        user_id = await self._require_user_id()
        period = EnergyPeriod(period)
        data = await self._get(
            "/DataProcessing/v1/metricsAggregat/consommation/room/"
            f"{installation_id}/{room_id}/{user_id}/{period.value}/Wc/"
            f"{_format_api_date(start)}/{_format_api_date(end)}",
            empty_on_no_content=True,
        )
        if not isinstance(data, list):
            msg = "energy response is not a list"
            raise FenixControlResponseError(msg)
        return [EnergyMetric.from_api(item) for item in data if isinstance(item, dict)]
