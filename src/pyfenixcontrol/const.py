"""Constants and enumerations for the FENIX Control cloud API."""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Final

API_BASE_URL: Final = "https://vs2-fe-apim-prod.azure-api.net"
IDENTITY_BASE_URL: Final = "https://vs2-fe-identity-prod.azurewebsites.net"

# Public OAuth2 client of the FENIX Control mobile app (PKCE, no secret).
# Pass your own ``client_id`` to :class:`~pyfenixcontrol.PasswordAuth` once a
# dedicated client is issued.
DEFAULT_CLIENT_ID: Final = "b1760b2e-69f1-4e89-8233-5840a9accdf8"
DEFAULT_REDIRECT_URI: Final = "fenix://callback"
OEM_CLIENT: Final = "fenix"
DEFAULT_SCOPES: Final = (
    "profile openid offline_access "
    "DataProcessing.Read DataProcessing.Write "
    "Device.Read Device.Write "
    "Installation.Read Installation.Write "
    "IOTManagement.Read IOTManagement.Write "
    "Room.Read Room.Write "
    "TermOfUse.Read TermOfUse.Write"
)

DEFAULT_REQUEST_TIMEOUT: Final = 10.0
DEFAULT_MAX_RETRIES: Final = 2

# Sentinel the backend uses in H1/H2 for "no holiday schedule". Not a valid date.
HOLIDAY_EPOCH_DATE: Final = "00/00/1970 00:00:00"
HOLIDAY_DATE_FORMAT: Final = "%d/%m/%Y %H:%M:%S"

#: Divisor applied to raw temperature values when none is given by the API.
DEFAULT_DIV_FACTOR: Final = 10


class PresetMode(IntEnum):
    """Operating mode of a thermostat (``Cm`` read / ``Dm`` write)."""

    OFF = 0
    HOLIDAYS = 1
    PROGRAM = 2
    DEFROST = 4
    BOOST = 5
    MANUAL = 6


class HvacState(IntEnum):
    """Heating state reported in ``Hs`` outside holiday mode."""

    IDLE = 0
    HEATING = 1
    OFF = 2


class HolidayMode(IntEnum):
    """Holiday behaviour of an installation (``H3[0]`` / ``H4``)."""

    NONE = 0
    OFF = 1
    REDUCE = 2
    DEFROST = 5
    SUNDAY = 8


class EnergyPeriod(StrEnum):
    """Aggregation period of the energy consumption endpoint."""

    HOUR = "Hour"
    DAY = "Day"
    WEEK = "Week"
    MONTH = "Month"
    QUARTER = "Quarter"
    YEAR = "Year"
