# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- README: clarify relationship with FENIX Trading s.r.o.

## [0.1.0] - 2026-10-15

First release, extracted from the `api.py` module of the
[`fenix_tft`](https://github.com/baracudaz/fenix_tft) Home Assistant integration
(v1.3.8).

### Added
- `FenixControlClient` with typed models (`Installation`, `Room`, `Device`,
  `EnergyMetric`) and enums (`PresetMode`, `HvacState`, `HolidayMode`,
  `EnergyPeriod`).
- Pluggable authentication via `AbstractAuth`; `PasswordAuth` implements the
  existing e-mail/password OAuth2 PKCE login.
- Exception hierarchy separating auth, connection and response errors.

### Changed compared to the integration's `api.py`
- No dependency on Home Assistant or BeautifulSoup (login form parsed with the
  standard library).
- Network errors and timeouts during login raise `FenixControlConnectionError`
  instead of an auth error, so a network outage no longer looks like invalid
  credentials.
- Invalid credentials are reported explicitly instead of a generic login failure.
- Concurrent requests share one login/refresh (asyncio lock) instead of a
  re-entrancy flag that let parallel callers proceed without a token.
- Identity-server cookies are cleared before a full login, so a cached identity
  session cannot authenticate a different account sharing the HTTP session.
- PUT requests now use the request timeout (previously unbounded).
- API requests no longer follow HTTP redirects.
- Energy queries take explicit `start`/`end` instead of "today in HA's time zone";
  the daily total and the "user connected" update trigger are left to the caller.
