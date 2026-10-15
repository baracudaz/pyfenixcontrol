"""Tests for data models and value codecs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pyfenixcontrol import (
    Device,
    EnergyMetric,
    Installation,
    decode_temperature,
    encode_temperature,
    parse_holiday_date,
)
from pyfenixcontrol.models import format_holiday_date

INSTALLATION_JSON = {
    "id": "inst-1",
    "Il": "Home",
    "rooms": [
        {"Zn": "room-1", "Rn": "Living", "devices": [{"Id_deviceId": "dev-1"}]},
        {"Zn": "room-2", "devices": [{"Id_deviceId": "dev-2"}, {"foo": 1}, "bad"]},
        "not-a-room",
    ],
}


def _entry(value: object, div: int = 10) -> dict[str, object]:
    return {"value": value, "divFactor": div}


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (_entry(680), 20.0),  # 68.0 °F
        (_entry(32, 1), 0.0),
        ({"value": 212}, 100.0),  # missing divFactor -> 1
        ({"value": 680, "divFactor": 0}, 360.0),  # zero divFactor -> 1
        (None, None),
        ({}, None),
        ({"value": None}, None),
        ({"value": True}, None),
        ({"value": "68"}, None),
    ],
)
def test_decode_temperature(
    entry: dict[str, object] | None, expected: float | None
) -> None:
    result = decode_temperature(entry)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


@pytest.mark.parametrize(("celsius", "raw"), [(20.0, 680), (21.5, 707), (5.0, 410)])
def test_encode_temperature_roundtrip(celsius: float, raw: int) -> None:
    assert encode_temperature(celsius) == raw
    assert decode_temperature(_entry(raw)) == pytest.approx(celsius, abs=0.06)


def test_holiday_date_helpers() -> None:
    assert parse_holiday_date("24/12/2026 08:30:00") == datetime(2026, 12, 24, 8, 30)
    assert parse_holiday_date("00/00/1970 00:00:00") is None
    assert parse_holiday_date("garbage") is None
    assert parse_holiday_date(None) is None
    assert format_holiday_date(datetime(2026, 1, 2, 3, 4, 5)) == "02/01/2026 03:04:05"


def test_installation_from_api() -> None:
    inst = Installation.from_api(INSTALLATION_JSON)
    assert inst.id == "inst-1"
    assert inst.name == "Home"
    assert [r.id for r in inst.rooms] == ["room-1", "room-2"]
    assert inst.rooms[0].name == "Living"
    assert inst.rooms[0].device_ids == ("dev-1",)
    assert inst.rooms[1].name is None
    assert inst.rooms[1].device_ids == ("dev-2",)


def test_device_from_api_full() -> None:
    inst = Installation.from_api(INSTALLATION_JSON)
    props = {
        "Rn": {"value": "Bathroom"},
        "Sv": {"value": "1.2.3"},
        "Ty": {"value": 7},
        "Ma": _entry(707),
        "At": _entry(680),
        "bo": _entry(770),
        "Sp": _entry(500),
        "Cm": {"value": 1},
        "Hs": {"value": 2},
        "H1": {"value": "01/12/2026 00:00:00"},
        "H2": {"value": "10/12/2026 18:00:00"},
        "H3": {"value": [2, 0, 0]},
        "H4": {"value": 2},
    }
    device = Device.from_api("dev-1", props, inst, inst.rooms[0])
    assert device.name == "Bathroom"
    assert device.installation_id == "inst-1"
    assert device.installation_name == "Home"
    assert device.room_id == "room-1"
    assert device.software_version == "1.2.3"
    assert device.model == "7"
    assert device.target_temperature == pytest.approx(21.5)
    assert device.room_temperature == pytest.approx(20.0)
    assert device.floor_temperature == pytest.approx(25.0)
    assert device.holiday_target_temperature == pytest.approx(10.0)
    assert device.preset_mode == 1
    assert device.hvac_state == 2
    assert device.holiday_mode == 2
    assert device.active_holiday_mode == 2
    assert device.holiday_active
    assert device.holiday_end_date == datetime(2026, 12, 10, 18)
    assert device.raw is props


def test_device_from_api_sparse() -> None:
    inst = Installation.from_api(INSTALLATION_JSON)
    device = Device.from_api(
        "dev-2",
        {
            "Cm": {"value": 6.0},
            "H3": {"value": []},
            "Rn": "not-an-entry",
            "Hs": {"value": True},
        },
        inst,
        inst.rooms[1],
    )
    assert device.name is None
    assert device.model is None
    assert device.target_temperature is None
    assert device.preset_mode == 6
    assert device.hvac_state is None
    assert device.holiday_mode == 0
    assert device.active_holiday_mode == 0
    assert not device.holiday_active
    assert device.holiday_end_date is None


def test_energy_metric_from_api() -> None:
    metric = EnergyMetric.from_api(
        {
            "startDateOfMetric": "2026-10-01T10:00:00Z",
            "sum": 120,
            "processedDataWithAggregator": 118.5,
        }
    )
    assert metric.start == datetime(2026, 10, 1, 10, tzinfo=UTC)
    assert metric.value == 120.0
    assert metric.processed_value == 118.5

    naive = EnergyMetric.from_api({"startDateOfMetric": "2026-10-01T10:00:00"})
    assert naive.start == datetime(2026, 10, 1, 10, tzinfo=UTC)
    assert naive.value == 0.0

    offset = EnergyMetric.from_api({"startDateOfMetric": "2026-10-01T12:00:00+02:00"})
    assert offset.start == datetime(2026, 10, 1, 10, tzinfo=UTC)

    assert EnergyMetric.from_api({}).start is None

    broken = EnergyMetric.from_api({"startDateOfMetric": "nope", "sum": "12"})
    assert broken.start is None
    assert broken.value == 0.0
