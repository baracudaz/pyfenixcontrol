"""Asynchronous Python client for the FENIX Control cloud API."""

from __future__ import annotations

from .auth import AbstractAuth, PasswordAuth
from .client import FenixControlClient
from .const import EnergyPeriod, HolidayMode, HvacState, PresetMode
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
    Room,
    decode_temperature,
    encode_temperature,
    parse_holiday_date,
)

__version__ = "0.1.0"

__all__ = [
    "AbstractAuth",
    "Device",
    "EnergyMetric",
    "EnergyPeriod",
    "FenixControlAuthError",
    "FenixControlClient",
    "FenixControlConnectionError",
    "FenixControlError",
    "FenixControlResponseError",
    "HolidayMode",
    "HvacState",
    "Installation",
    "PasswordAuth",
    "PresetMode",
    "Room",
    "__version__",
    "decode_temperature",
    "encode_temperature",
    "parse_holiday_date",
]
