# pyfenixcontrol

Asynchronous Python client for the **FENIX Control** cloud, used by FENIX TFT WiFi
thermostats. It powers the FENIX integration for [Home Assistant](https://www.home-assistant.io/),
but has no Home Assistant dependency and can be used from any asyncio code.

- Fully typed (`py.typed`), `asyncio` + `aiohttp`, Python 3.12+
- Read thermostats, rooms and installations; set target temperature and operating mode
- Installation-wide holiday schedules
- Room energy consumption at hourly … yearly aggregation
- Pluggable authentication: bundled e-mail/password login, or bring your own OAuth2 tokens
- Automatic token refresh, single retry on HTTP 401, backoff on HTTP 5xx

## Installation

```bash
pip install pyfenixcontrol
```

## Quick start

```python
import asyncio

import aiohttp

from pyfenixcontrol import FenixControlClient, PasswordAuth, PresetMode


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        auth = PasswordAuth(session, "you@example.com", "password")
        client = FenixControlClient(auth)

        for device in await client.get_devices():
            print(device.name, device.room_temperature, device.target_temperature)

        await client.set_target_temperature(device.id, 21.5)
        await client.set_preset_mode(device.id, PresetMode.PROGRAM)


asyncio.run(main())
```

## Authentication

The client never sees credentials. It asks an `AbstractAuth` for a bearer token:

```python
from pyfenixcontrol import AbstractAuth


class MyAuth(AbstractAuth):
    async def async_get_access_token(self) -> str:
        return await my_token_store.get_valid_token()

    async def async_invalidate_access_token(self) -> None:
        # Called after an HTTP 401; optional.
        await my_token_store.mark_expired()
```

`PasswordAuth` performs the OAuth2 authorization-code + PKCE login used by the
FENIX Control app and keeps tokens in memory. `await auth.async_login()` validates
credentials up front (e.g. in a setup wizard).

`PasswordAuth` needs an `aiohttp` session with a working cookie jar, because the
identity server tracks its login form with cookies. Cookies of the identity host
are cleared before each full login.

## API overview

| Method | Description |
|---|---|
| `get_user_id()` | Account subject id (cached on the client) |
| `get_installations()` | Installations (homes) with their rooms and device ids |
| `get_devices(installations=None)` | `Device` state of every thermostat |
| `get_device_properties(device_id)` | Raw device twin properties |
| `trigger_installation_update(installation_id)` | Ask thermostats to report fresh values |
| `set_target_temperature(device_id, celsius)` | Set target °C (switches to manual) |
| `set_preset_mode(device_id, mode)` | `PresetMode.OFF / PROGRAM / MANUAL / DEFROST / BOOST / HOLIDAYS` |
| `set_holiday_schedule(installation_id, start, end, mode)` | Holiday for the whole installation |
| `cancel_holiday_schedule(installation_id)` | Remove the holiday schedule |
| `get_room_energy(installation_id, room_id, start, end, period)` | `EnergyMetric` buckets in Wh |

### Errors

| Exception | Meaning |
|---|---|
| `FenixControlAuthError` | Credentials or tokens rejected – user action needed |
| `FenixControlConnectionError` | Network error or timeout – retry later |
| `FenixControlResponseError` | Unexpected status or body (`.status` holds the HTTP code) |

All inherit from `FenixControlError`.

### Units and conventions

- Temperatures are °C. The API transports °F × `divFactor`;
  `decode_temperature()` / `encode_temperature()` convert.
- Holiday `start`/`end` are wall-clock times of the installation (no time zone
  conversion). Energy queries convert to UTC; naive datetimes are taken as UTC.
- Every model keeps the original payload in `.raw` for fields not yet modelled.

## Development

```bash
uv sync
uv run pytest --cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Tests run against a local fake of the cloud and identity server; no account is needed.

Releases: bump `__version__`, update `CHANGELOG.md`, publish a GitHub release
tagged `vX.Y.Z`. The release workflow publishes to PyPI via trusted publishing.

## License

MIT – see [LICENSE](LICENSE).

Developed in cooperation with FENIX Trading s.r.o. FENIX and FENIX Control are
trademarks of their respective owners. The FENIX Control cloud is operated by the
manufacturer; its API may change without notice.
