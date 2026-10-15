# CLAUDE.md

Guidance for Claude Code in this repository.

## What this is

`pyfenixcontrol` – async Python client for the FENIX Control cloud (FENIX TFT WiFi
thermostats). It exists because Home Assistant core requires all API code to live
in a PyPI library; the HA integration (`fenix_control` in core, `fenix_tft` on
HACS) only talks to this package.

Public, MIT-licensed repository. Never commit credentials, real installation or
device IDs, account `sub` values, or anything from FENIX/Watts internal
documentation (NDA) – use invented IDs in tests and docs.

## Commands

```bash
uv sync                                   # create .venv with dev deps
uv run pytest --cov                       # tests (must stay ~100% coverage)
uv run ruff format . && uv run ruff check . --fix
uv run mypy                               # strict, covers src and tests
uv build && uvx twine check --strict dist/*
```

CI (`.github/workflows/ci.yml`) runs exactly these on Python 3.12–3.14 and fails
below 95 % coverage.

## Layout

```
src/pyfenixcontrol/
├── auth.py        AbstractAuth (token provider) + PasswordAuth (OAuth2 PKCE login)
├── client.py      FenixControlClient – all endpoints, retry/401 handling
├── models.py      Installation, Room, Device, EnergyMetric + temperature/holiday codecs
├── const.py       URLs, default OAuth client, enums (PresetMode, HvacState, HolidayMode, EnergyPeriod)
└── exceptions.py  FenixControlError → Auth / Connection / Response
tests/             run against FakeCloud (local aiohttp server), no real account
```

## Design rules

- **No Home Assistant imports, no HA concepts.** Time zones, "today", daily totals,
  optimistic state, polling – all belong in the integration.
- **The client never sees credentials.** It only calls
  `AbstractAuth.async_get_access_token()` / `async_invalidate_access_token()`.
  The HA core integration will plug in an `application_credentials`-based auth
  once FENIX/Watts issue a dedicated OAuth client; `PasswordAuth` must keep
  working for HACS meanwhile.
- **Error contract** (the integration maps these 1:1):
  `FenixControlAuthError` → reauth, `FenixControlConnectionError` → retry later,
  `FenixControlResponseError` → unexpected API behaviour (has `.status`).
  Wrap every `aiohttp.ClientError` / `TimeoutError`; never leak them.
- Retries: only 5xx (backoff 1 s, 2 s …); one token refresh + retry on 401;
  redirects are not followed on API calls.
- Models are frozen dataclasses built via `from_api()`, tolerant of missing or
  malformed fields, and keep the original payload in `.raw`. Prefer adding a
  typed field over making callers read `.raw`.
- Public API is what `__init__.py` exports. Changing it is a breaking change →
  bump the version accordingly (SemVer) and note it in `CHANGELOG.md`.
- Every new endpoint or field gets a test with the exact request payload asserted
  against `FakeCloud`.

## API field reference (device twin keys)

`Cm` preset mode · `Dm` preset mode (write) · `Hs` heating state (holiday mode while
in holiday) · `Ma` target · `At` room · `bo` floor · `Sp` holiday target ·
`H1`/`H2` holiday start/end (`dd/mm/YYYY HH:MM:SS`, sentinel `00/00/1970 00:00:00`) ·
`H3[0]` scheduled holiday mode · `H4` active holiday mode · `Rn` name · `Sv`
firmware · `Ty` type. Temperatures are °F × `divFactor`.

Open questions with the vendor (do not guess, keep current behaviour until answered):
floor temperature `Ft` vs `bo`; whether `connectionState` is exposed via APIM;
units of signal strength `Dc`; whether third parties may send `Uc` (refresh).

## Releasing

Bump `__version__` in `src/pyfenixcontrol/__init__.py`, update `CHANGELOG.md`,
publish a GitHub release tagged `vX.Y.Z`; `release.yml` publishes to PyPI via
trusted publishing. **Do not release to PyPI until the PyPI project owner is
decided** (the repository may still move to a FENIX GitHub organization).
