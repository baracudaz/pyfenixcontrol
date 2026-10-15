"""Tests for FenixControlClient."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from pyfenixcontrol import (
    AbstractAuth,
    EnergyPeriod,
    FenixControlAuthError,
    FenixControlClient,
    FenixControlConnectionError,
    FenixControlResponseError,
    HolidayMode,
    Installation,
    PresetMode,
)

from .conftest import USER_ID, FakeCloud, StaticAuth

PROPS_PATH = "/api/iotmanagement/v1/configuration/dev-1/dev-1/v1/content/"
PROPS2_PATH = "/api/iotmanagement/v1/configuration/dev-2/dev-2/v1/content/"
INSTALLATIONS_PATH = f"/api/businessmodule/v1/installations/admins/{USER_ID}"
USERINFO_PATH = "/id/connect/userinfo"
CONFIG_PATH = "/api/iotmanagement/v1/devices/twin/properties/config/replace"
JOB_PATH = "/api/iotmanagement/v1/devices/installationcontroljob"
ENERGY_PREFIX = "/api/DataProcessing/v1/metricsAggregat/consommation/room"

INSTALLATIONS = [
    {
        "id": "inst-1",
        "Il": "Home",
        "rooms": [
            {
                "Zn": "room-1",
                "devices": [{"Id_deviceId": "dev-1"}, {"Id_deviceId": "dev-2"}],
            }
        ],
    }
]
PROPS = {"Rn": {"value": "Living"}, "At": {"value": 680, "divFactor": 10}}


def test_defaults_to_production_urls(auth: StaticAuth) -> None:
    fenix = FenixControlClient(auth)
    assert fenix._api_url == "https://vs2-fe-apim-prod.azure-api.net"
    assert fenix._identity_url == "https://vs2-fe-identity-prod.azurewebsites.net"


# -- transport -------------------------------------------------------------


async def test_sends_bearer_token(client: FenixControlClient, cloud: FakeCloud) -> None:
    cloud.add("GET", PROPS_PATH, payload=PROPS)
    assert await client.get_device_properties("dev-1") == PROPS
    [req] = cloud.requests
    assert req.headers["Authorization"] == "Bearer token-0"
    assert req.headers["Accept"] == "application/json"


async def test_retries_transient_5xx(
    client: FenixControlClient, cloud: FakeCloud, mock_sleep: AsyncMock
) -> None:
    cloud.add("GET", PROPS_PATH, status=502)
    cloud.add("GET", PROPS_PATH, status=503)
    cloud.add("GET", PROPS_PATH, payload=PROPS)
    assert await client.get_device_properties("dev-1") == PROPS
    assert [c.args for c in mock_sleep.await_args_list] == [(1,), (2,)]


async def test_gives_up_after_max_retries(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    for _ in range(3):
        cloud.add("GET", PROPS_PATH, status=500)
    with pytest.raises(FenixControlResponseError) as exc:
        await client.get_device_properties("dev-1")
    assert exc.value.status == 500
    assert len(cloud.requests) == 3


async def test_max_retries_is_configurable(auth: StaticAuth, cloud: FakeCloud) -> None:
    fenix = FenixControlClient(auth, api_url=f"{cloud.url}/api", max_retries=0)
    cloud.add("GET", PROPS_PATH, status=500)
    with pytest.raises(FenixControlResponseError):
        await fenix.get_device_properties("dev-1")
    assert len(cloud.requests) == 1


@pytest.mark.parametrize("status", [302, 400, 403, 404])
async def test_does_not_retry_other_errors(
    client: FenixControlClient, cloud: FakeCloud, status: int
) -> None:
    cloud.add("GET", PROPS_PATH, status=status, headers={"Location": "/elsewhere"})
    with pytest.raises(FenixControlResponseError) as exc:
        await client.get_device_properties("dev-1")
    assert exc.value.status == status
    assert len(cloud.requests) == 1


async def test_invalid_json(client: FenixControlClient, cloud: FakeCloud) -> None:
    cloud.add("GET", PROPS_PATH, text="<html>")
    with pytest.raises(FenixControlResponseError, match="invalid JSON"):
        await client.get_device_properties("dev-1")


@pytest.mark.parametrize("exc", [aiohttp.ClientConnectionError("boom"), TimeoutError()])
async def test_connection_errors_are_wrapped(
    client: FenixControlClient, auth: StaticAuth, exc: Exception
) -> None:
    with (
        patch.object(auth.session, "request", side_effect=exc),
        pytest.raises(FenixControlConnectionError),
    ):
        await client.get_device_properties("dev-1")


async def test_401_invalidates_token_and_retries_once(
    client: FenixControlClient, auth: StaticAuth, cloud: FakeCloud
) -> None:
    cloud.add("GET", PROPS_PATH, status=401)
    cloud.add("GET", PROPS_PATH, payload=PROPS)
    assert await client.get_device_properties("dev-1") == PROPS
    assert auth.invalidations == 1
    assert cloud.requests[1].headers["Authorization"] == "Bearer token-1"


async def test_401_retry_does_not_consume_5xx_budget(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    cloud.add("GET", PROPS_PATH, status=500)
    cloud.add("GET", PROPS_PATH, status=401)
    cloud.add("GET", PROPS_PATH, status=500)
    cloud.add("GET", PROPS_PATH, payload=PROPS)
    assert await client.get_device_properties("dev-1") == PROPS


async def test_persistent_401_raises_auth_error(
    client: FenixControlClient, auth: StaticAuth, cloud: FakeCloud
) -> None:
    cloud.add("GET", PROPS_PATH, status=401)
    cloud.add("GET", PROPS_PATH, status=401)
    with pytest.raises(FenixControlAuthError):
        await client.get_device_properties("dev-1")
    assert auth.invalidations == 1


async def test_auth_errors_from_token_provider_propagate(
    client: FenixControlClient, auth: StaticAuth
) -> None:
    with (
        patch.object(
            auth,
            "async_get_access_token",
            AsyncMock(side_effect=FenixControlAuthError("bad password")),
        ),
        pytest.raises(FenixControlAuthError),
    ):
        await client.get_device_properties("dev-1")


async def test_properties_must_be_object(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    cloud.add("GET", PROPS_PATH, payload=[])
    with pytest.raises(FenixControlResponseError):
        await client.get_device_properties("dev-1")


async def test_default_invalidate_hook_is_noop(session: aiohttp.ClientSession) -> None:
    class MinimalAuth(AbstractAuth):
        async def async_get_access_token(self) -> str:
            return "t"

    minimal = MinimalAuth(session)
    await minimal.async_invalidate_access_token()
    assert minimal.session is session


# -- account & topology ----------------------------------------------------


async def test_get_user_id(auth: StaticAuth, cloud: FakeCloud) -> None:
    fenix = FenixControlClient(auth, identity_url=f"{cloud.url}/id")
    assert fenix.user_id is None
    cloud.add("GET", USERINFO_PATH, payload={"sub": "abc"})
    assert await fenix.get_user_id() == "abc"
    assert fenix.user_id == "abc"


@pytest.mark.parametrize("payload", [{"name": "x"}, [1], {"sub": ""}])
async def test_get_user_id_without_sub(
    client: FenixControlClient, cloud: FakeCloud, payload: object
) -> None:
    cloud.add("GET", USERINFO_PATH, payload=payload)
    with pytest.raises(FenixControlResponseError):
        await client.get_user_id()


async def test_get_installations_fetches_user_id_first(
    auth: StaticAuth, cloud: FakeCloud
) -> None:
    fenix = FenixControlClient(
        auth, api_url=f"{cloud.url}/api", identity_url=f"{cloud.url}/id"
    )
    cloud.add("GET", USERINFO_PATH, payload={"sub": USER_ID})
    cloud.add("GET", INSTALLATIONS_PATH, payload=INSTALLATIONS)
    [inst] = await fenix.get_installations()
    assert inst.id == "inst-1"
    assert inst.rooms[0].device_ids == ("dev-1", "dev-2")


async def test_get_installations_no_content(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    cloud.add("GET", INSTALLATIONS_PATH, status=204)
    assert await client.get_installations() == []


async def test_get_installations_rejects_non_list(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    cloud.add("GET", INSTALLATIONS_PATH, payload={"oops": 1})
    with pytest.raises(FenixControlResponseError):
        await client.get_installations()


async def test_get_devices_skips_failing_device(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    cloud.add("GET", INSTALLATIONS_PATH, payload=INSTALLATIONS)
    cloud.add("GET", PROPS_PATH, payload=PROPS)
    cloud.add("GET", PROPS2_PATH, status=404)
    [device] = await client.get_devices()
    assert device.id == "dev-1"
    assert device.name == "Living"
    assert device.installation_id == "inst-1"
    assert device.room_temperature == pytest.approx(20.0)


async def test_get_devices_propagates_auth_error(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    cloud.add("GET", PROPS_PATH, status=401)
    cloud.add("GET", PROPS_PATH, status=401)
    with pytest.raises(FenixControlAuthError):
        await client.get_devices([Installation.from_api(INSTALLATIONS[0])])
    assert not cloud.requests_to("GET", PROPS2_PATH)


async def test_get_devices_propagates_connection_error(
    client: FenixControlClient, auth: StaticAuth
) -> None:
    with (
        patch.object(auth.session, "request", side_effect=aiohttp.ClientError()),
        pytest.raises(FenixControlConnectionError),
    ):
        await client.get_devices([Installation.from_api(INSTALLATIONS[0])])


async def test_trigger_installation_update(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    path = "/api/iotmanagement/v1/devices/userconnected"
    cloud.add("PUT", path, status=200)
    await client.trigger_installation_update("inst-1")
    [req] = cloud.requests_to("PUT", path)
    assert req.json == {"A1": USER_ID, "In": "inst-1"}


# -- control ---------------------------------------------------------------


async def test_set_target_temperature(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    cloud.add("PUT", CONFIG_PATH, payload={"ok": True})
    await client.set_target_temperature("dev-1", 21.5)
    [req] = cloud.requests
    assert req.json == {
        "Id_deviceId": "dev-1",
        "S1": "dev-1",
        "configurationVersion": "v1.0",
        "data": [
            {"wattsType": "Dm", "wattsTypeValue": 6},
            {"wattsType": "Ma", "wattsTypeValue": 707},
        ],
    }


async def test_set_preset_mode(client: FenixControlClient, cloud: FakeCloud) -> None:
    cloud.add("PUT", CONFIG_PATH, status=204)
    await client.set_preset_mode("dev-1", PresetMode.PROGRAM)
    assert cloud.requests[0].json["data"] == [{"wattsType": "Dm", "wattsTypeValue": 2}]


async def test_set_preset_mode_rejects_unknown_value(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    with pytest.raises(ValueError, match="Invalid preset mode"):
        await client.set_preset_mode("dev-1", 3)
    assert not cloud.requests


async def test_set_holiday_schedule(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    cloud.add("PUT", JOB_PATH, payload={})
    await client.set_holiday_schedule(
        "inst-1",
        datetime(2026, 12, 20, 8, 0),
        datetime(2026, 12, 27, 18, 30),
        HolidayMode.REDUCE,
    )
    assert cloud.requests[0].json == {
        "In": "inst-1",
        "A1": USER_ID,
        "data": [
            {
                "timestamp": None,
                "wattsType": "H1",
                "wattsTypeValue": "20/12/2026 08:00:00",
            },
            {
                "timestamp": None,
                "wattsType": "H2",
                "wattsTypeValue": "27/12/2026 18:30:00",
            },
            {"timestamp": None, "wattsType": "H3", "wattsTypeValue": [2, 0, 0]},
        ],
    }


@pytest.mark.parametrize(
    ("start_day", "end_day", "mode", "match"),
    [
        (2, 3, HolidayMode.NONE, "cancel"),
        (3, 2, HolidayMode.OFF, "after start"),
    ],
)
async def test_set_holiday_schedule_validation(
    client: FenixControlClient,
    start_day: int,
    end_day: int,
    mode: HolidayMode,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        await client.set_holiday_schedule(
            "inst-1",
            datetime(2026, 1, start_day, tzinfo=UTC),
            datetime(2026, 1, end_day, tzinfo=UTC),
            mode,
        )


async def test_cancel_holiday_schedule(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    cloud.add("PUT", JOB_PATH, payload={})
    await client.cancel_holiday_schedule("inst-1")
    values = [item["wattsTypeValue"] for item in cloud.requests[0].json["data"]]
    assert values == ["00/00/1970 00:00:00", "00/00/1970 00:00:00", [0, 0, 0]]


# -- energy ----------------------------------------------------------------


async def test_get_room_energy(client: FenixControlClient, cloud: FakeCloud) -> None:
    cest = timezone(timedelta(hours=2))
    cloud.add(
        "GET",
        f"{ENERGY_PREFIX}/inst-1/room-1/{USER_ID}/Day/Wc/"
        "2026-09-30T22:00:00.000000Z/2026-10-01T10:00:00.000000Z",
        payload=[{"startDateOfMetric": "2026-09-30T22:00:00Z", "sum": 42}, "junk"],
    )
    [metric] = await client.get_room_energy(
        "inst-1",
        "room-1",
        datetime(2026, 10, 1, 0, 0, tzinfo=cest),
        datetime(2026, 10, 1, 10, 0),
        EnergyPeriod.DAY,
    )
    assert metric.value == 42
    assert metric.start == datetime(2026, 9, 30, 22, tzinfo=UTC)


async def test_get_room_energy_no_content(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    cloud.add(
        "GET",
        f"{ENERGY_PREFIX}/i/r/{USER_ID}/Hour/Wc/"
        "2026-01-01T00:00:00.000000Z/2026-01-01T00:00:00.000000Z",
        status=204,
    )
    assert await client.get_room_energy("i", "r", start, start) == []


async def test_get_room_energy_rejects_non_list(
    client: FenixControlClient, cloud: FakeCloud
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    cloud.add(
        "GET",
        f"{ENERGY_PREFIX}/i/r/{USER_ID}/Hour/Wc/"
        "2026-01-01T00:00:00.000000Z/2026-01-01T00:00:00.000000Z",
        payload={},
    )
    with pytest.raises(FenixControlResponseError):
        await client.get_room_energy("i", "r", start, start, "Hour")
