"""Data models returned by :class:`~pyfenixcontrol.FenixControlClient`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .const import DEFAULT_DIV_FACTOR, HOLIDAY_DATE_FORMAT, HOLIDAY_EPOCH_DATE

if TYPE_CHECKING:
    from collections.abc import Mapping


def decode_temperature(entry: Mapping[str, Any] | None) -> float | None:
    """Decode a ``{"value", "divFactor"}`` entry (°F x divFactor) to °C."""
    if not entry:
        return None
    value = entry.get("value")
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    div = entry.get("divFactor") or 1
    fahrenheit = value / div
    return (fahrenheit - 32.0) * 5.0 / 9.0


def encode_temperature(celsius: float, div_factor: int = DEFAULT_DIV_FACTOR) -> int:
    """Encode °C to the raw API value (°F x divFactor, rounded)."""
    return round((celsius * 9.0 / 5.0 + 32.0) * div_factor)


def parse_holiday_date(value: str | None) -> datetime | None:
    """Parse a holiday ``H1``/``H2`` string to a naive local datetime.

    The backend stores holiday dates in the installation's local time without
    an offset. Returns ``None`` for the "no holiday" sentinel or invalid input.
    """
    if not value or value == HOLIDAY_EPOCH_DATE:
        return None
    try:
        return datetime.strptime(value, HOLIDAY_DATE_FORMAT)  # noqa: DTZ007
    except (TypeError, ValueError):
        return None


def format_holiday_date(value: datetime) -> str:
    """Format a datetime for the holiday ``H1``/``H2`` fields."""
    return value.strftime(HOLIDAY_DATE_FORMAT)


def _prop(props: Mapping[str, Any], key: str) -> Any:
    entry = props.get(key)
    if isinstance(entry, dict):
        return entry.get("value")
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class Room:
    """A room (``Zn``) of an installation and the thermostats in it."""

    id: str
    name: str | None
    device_ids: tuple[str, ...]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Room:
        """Build from an entry of an installation's ``rooms`` list."""
        devices = data.get("devices") or []
        return cls(
            id=str(data.get("Zn", "")),
            name=_as_str(data.get("Rn")),
            device_ids=tuple(
                str(dev["Id_deviceId"])
                for dev in devices
                if isinstance(dev, dict) and dev.get("Id_deviceId")
            ),
        )


@dataclass(frozen=True, slots=True)
class Installation:
    """An installation (home) the account administers."""

    id: str
    name: str | None
    rooms: tuple[Room, ...]
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> Installation:
        """Build from an entry of ``/installations/admins/{sub}``."""
        rooms = data.get("rooms") or []
        return cls(
            id=str(data.get("id", "")),
            name=_as_str(data.get("Il")),
            rooms=tuple(Room.from_api(r) for r in rooms if isinstance(r, dict)),
            raw=data,
        )


@dataclass(frozen=True, slots=True)
class Device:
    """Current state of one thermostat.

    Field reference (property keys of the device twin):

    * ``Cm`` - preset mode, see :class:`~pyfenixcontrol.PresetMode`
    * ``Hs`` - heating state (:class:`~pyfenixcontrol.HvacState`); while in
      holiday mode it carries the holiday mode instead
    * ``H1``/``H2`` - holiday start/end, ``H3[0]`` - scheduled holiday mode,
      ``H4`` - currently active holiday mode (primary holiday indicator)
    * ``Ma`` - target, ``At`` - room, ``ft`` - floor, ``Sp`` - holiday target
      temperature
    """

    id: str
    name: str | None
    installation_id: str
    installation_name: str | None
    room_id: str
    software_version: str | None
    model: str | None
    target_temperature: float | None
    room_temperature: float | None
    floor_temperature: float | None
    holiday_target_temperature: float | None
    preset_mode: int | None
    hvac_state: int | None
    holiday_start: str | None
    holiday_end: str | None
    holiday_mode: int
    active_holiday_mode: int
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(
        cls,
        device_id: str,
        props: Mapping[str, Any],
        installation: Installation,
        room: Room,
    ) -> Device:
        """Build from the device twin ``content`` properties."""
        h3 = _prop(props, "H3")
        holiday_mode = _as_int(h3[0]) if isinstance(h3, list) and h3 else None
        name = _prop(props, "Rn")
        return cls(
            id=device_id,
            name=_as_str(name),
            installation_id=installation.id,
            installation_name=installation.name,
            room_id=room.id,
            software_version=_as_str(_prop(props, "Sv")),
            model=None if _prop(props, "Ty") is None else str(_prop(props, "Ty")),
            target_temperature=decode_temperature(props.get("Ma")),
            room_temperature=decode_temperature(props.get("At")),
            floor_temperature=decode_temperature(props.get("ft")),
            holiday_target_temperature=decode_temperature(props.get("Sp")),
            preset_mode=_as_int(_prop(props, "Cm")),
            hvac_state=_as_int(_prop(props, "Hs")),
            holiday_start=_as_str(_prop(props, "H1")),
            holiday_end=_as_str(_prop(props, "H2")),
            holiday_mode=holiday_mode or 0,
            active_holiday_mode=_as_int(_prop(props, "H4")) or 0,
            raw=props,
        )

    @property
    def holiday_end_date(self) -> datetime | None:
        """Holiday end as naive local datetime, ``None`` if not scheduled."""
        return parse_holiday_date(self.holiday_end)

    @property
    def holiday_active(self) -> bool:
        """Return True while a holiday mode is in effect (``H4`` != 0)."""
        return self.active_holiday_mode != 0


@dataclass(frozen=True, slots=True)
class EnergyMetric:
    """One aggregated energy consumption bucket of a room (Wh)."""

    start: datetime | None
    value: float
    processed_value: float
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> EnergyMetric:
        """Build from an item of the ``metricsAggregat/consommation`` endpoint."""
        start: datetime | None = None
        start_raw = data.get("startDateOfMetric")
        if isinstance(start_raw, str):
            try:
                start = datetime.fromisoformat(start_raw)
            except ValueError:
                start = None
            else:
                start = (
                    start.replace(tzinfo=UTC)
                    if start.tzinfo is None
                    else start.astimezone(UTC)
                )
        return cls(
            start=start,
            value=_as_float(data.get("sum")),
            processed_value=_as_float(data.get("processedDataWithAggregator")),
            raw=data,
        )


def _as_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)
