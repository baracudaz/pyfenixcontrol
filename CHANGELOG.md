# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-11-08

### Added
- `Device.defrost_temperature` (`df`) and `Device.boost_temperature` (`bo`): the
  setpoints the thermostat uses in Defrost and Boost mode; `None` if missing or
  outside the property's range.
- `Device.target_temperature_range`, `Device.defrost_temperature_range` and
  `Device.boost_temperature_range`: the `(min, max)` range in °C the device reports
  for `Ma`, `df` and `bo` (for example 2–15 °C for frost protection on TFT
  thermostats, 10–30 °C for the WiFi Box's manual target). The cloud doesn't
  enforce these ranges.
- `FenixControlClient.set_setpoint(device_id, mode, celsius)`: switch to manual,
  Defrost or Boost and set that mode's setpoint. `set_target_temperature` is now
  `set_setpoint` with manual mode (same request as before).

## [0.3.0] - 2026-11-08

### Added
- `Device.active_target_temperature`: the setpoint the thermostat currently regulates
  to (`Sp`: manual, program, Defrost, Boost or holiday setpoint); `None` for the
  placeholder values reported in off mode.

## [0.2.0] - 2026-11-08

### Added
- `Device.model_name`: product name of the device type (`Ty`): TFT Wi-Fi (P07542),
  TFT Wi-Fi RF (P08639), WiFi Box (P08681); `None` for unknown types.

## [0.1.1] - 2026-11-08

### Fixed
- `Device.floor_temperature` is read from `ft`; it was read from `bo`, which
  holds the boost setpoint. Devices that don't report `ft` get `None`.

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
