"""The hot water blueprint's decision, rendered by Home Assistant itself.

The templates are pulled out of the blueprint YAML and rendered with HA's own
template engine at a frozen clock, against fake price data in each source's
shape.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

BLUEPRINT = (
    Path(__file__).parent.parent
    / "custom_components"
    / "thermiq_mqtt"
    / "blueprints"
    / "hotwater_cheap_hours.yaml"
)
SENSOR = "sensor.price"
STOP = "number.thermiq_mqtt_vp1_hotwater_stop_t"
TZ = ZoneInfo("Europe/Stockholm")
# A Thursday in January: Vattenfall's peak hours apply
DAY = datetime(2026, 1, 15, tzinfo=TZ)


class _Loader(yaml.SafeLoader):
    """SafeLoader that accepts the blueprint's !input tags."""


_Loader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))


@pytest.fixture(autouse=True)
async def stockholm(hass: HomeAssistant) -> None:
    """Peak hours are local time: run HA in the zone the test data is in."""
    await hass.config.async_set_time_zone("Europe/Stockholm")


def _blueprint() -> dict[str, Any]:
    data: dict[str, Any] = yaml.load(
        BLUEPRINT.read_text(encoding="utf-8"), Loader=_Loader
    )
    return data


def _defaults() -> dict[str, Any]:
    inputs = _blueprint()["blueprint"]["input"]
    return {k: v["default"] for k, v in inputs.items() if "default" in v}


def _decision_step() -> dict[str, str]:
    step: dict[str, str] = _blueprint()["actions"][1]["variables"]
    return step


def _slots(prices: list[float], minutes: int = 60) -> list[list[float]]:
    """[[timestamp, price], ...] from midnight, as the blueprint keeps them."""
    return [
        [(DAY + timedelta(minutes=minutes * i)).timestamp(), p]
        for i, p in enumerate(prices)
    ]


def _render(hass: HomeAssistant, template: str, variables: dict[str, Any]) -> Any:
    return Template(template, hass).async_render(variables)


async def _decide(hass: HomeAssistant, freezer, at_hour: float, slots, **inputs) -> str:
    freezer.move_to(DAY + timedelta(hours=at_hour, seconds=30))
    variables = {
        **_defaults(),
        "price_source": "nordpool",
        "attr_slots": slots,
        "tibber_slots": [],
        **inputs,
    }
    return str(_render(hass, _decision_step()["decision"], variables)).strip()


# 48 hours: two days, cheap 02-05 on both, an evening peak
TWO_DAYS = ([0.5] * 2 + [0.1] * 3 + [0.5] * 12 + [2.0] * 3 + [0.5] * 4) * 2


async def test_cheapest_hours_top_up(hass, freezer):
    slots = _slots(TWO_DAYS)
    assert await _decide(hass, freezer, 3, slots) == "cheap"
    assert await _decide(hass, freezer, 8, slots) == "wait"
    assert await _decide(hass, freezer, 18, slots) == "wait"


async def test_number_of_cheap_hours(hass, freezer):
    slots = _slots(TWO_DAYS)
    # 3 cheap hours: 02, 03, 04; asking for 1 leaves only the first match
    assert await _decide(hass, freezer, 4, slots, cheap_hours=3) == "cheap"
    assert await _decide(hass, freezer, 6, slots, cheap_hours=3) == "wait"
    # Asking for 6 also takes in the next-cheapest 0.5 hours
    assert await _decide(hass, freezer, 6, slots, cheap_hours=6) == "cheap"


async def test_before_tomorrows_prices_the_day_is_the_window(hass, freezer):
    """Only today known at 21:00: tonight's last hours are not 'the cheapest
    of what is left', they are ranked against the whole of today."""
    today = _slots(TWO_DAYS[:24])
    assert await _decide(hass, freezer, 21, today) == "wait"
    assert await _decide(hass, freezer, 3, today) == "cheap"


async def test_peak_fee_moves_cheap_hours_to_the_night(hass, freezer):
    """Flat spot all day: only the grid fee tells night from day."""
    flat = _slots([0.5] * 48)
    # Flat everything: every slot ties, so every slot counts as cheap
    assert await _decide(hass, freezer, 10, flat) == "cheap"
    assert await _decide(hass, freezer, 10, flat, peak_fee=0.46) == "wait"
    assert await _decide(hass, freezer, 23, flat, peak_fee=0.46) == "cheap"


async def test_peak_fee_only_in_peak_months(hass, freezer):
    flat = _slots([0.5] * 48)
    summer_only = ["6", "7"]
    assert (
        await _decide(hass, freezer, 10, flat, peak_fee=0.46, peak_months=summer_only)
        == "cheap"
    )


async def test_quarter_hour_prices(hass, freezer):
    prices = [0.5] * 192
    for day in (0, 96):  # 13:00-14:00 is the cheap hour, both days
        prices[day + 13 * 4 : day + 13 * 4 + 4] = [0.05] * 4
    slots = _slots(prices, minutes=15)
    assert await _decide(hass, freezer, 13.5, slots, cheap_hours=1) == "cheap"
    assert await _decide(hass, freezer, 14.25, slots, cheap_hours=1) == "wait"


async def test_no_prices(hass, freezer):
    assert await _decide(hass, freezer, 3, []) == "unknown"


async def test_tibber_source_uses_tibber_slots(hass, freezer):
    freezer.move_to(DAY + timedelta(hours=3, seconds=30))
    variables = {
        **_defaults(),
        "price_source": "tibber",
        "attr_slots": [],
        "tibber_slots": _slots(TWO_DAYS),
    }
    out = _render(hass, _decision_step()["decision"], variables)
    assert str(out).strip() == "cheap"


@pytest.mark.parametrize(
    ("decision", "stop", "expected"),
    [
        ("cheap", 50, 47.0),  # just below stop: top up
        ("wait", 50, 35.0),  # low start: wait for cheap power
        ("unknown", 50, 35.0),
        ("cheap", 36, 35.0),  # never below the lowest accepted
        ("cheap", None, 35.0),  # stop temperature unknown: stay low
    ],
)
async def test_new_start(hass, decision, stop, expected):
    if stop is not None:
        hass.states.async_set(STOP, str(stop))
    variables = {**_defaults(), "decision": decision}
    out = _render(hass, _decision_step()["new_start"], variables)
    assert float(out) == expected


@pytest.mark.parametrize(
    ("decision", "stop", "max_start", "expected"),
    [
        ("cheap", 59, 40, 40.0),  # pump keeps at most 40: ask for 40, not 56
        ("cheap", 50, 60, 47.0),  # the default limits nothing
        ("wait", 59, 40, 35.0),
        ("cheap", 59, 30, 35.0),  # never below the lowest accepted
    ],
)
async def test_new_start_respects_the_pumps_limit(
    hass, decision, stop, max_start, expected
):
    hass.states.async_set(STOP, str(stop))
    variables = {**_defaults(), "decision": decision, "max_start": max_start}
    out = _render(hass, _decision_step()["new_start"], variables)
    assert float(out) == expected


def test_max_start_is_off_by_default():
    assert _defaults()["max_start"] == 60


async def test_nordpool_and_entsoe_attributes(hass):
    start = DAY + timedelta(hours=2)
    hass.states.async_set(
        SENSOR,
        "0",
        {
            "raw_today": [
                {"start": start, "value": 0.1},
                {"start": start, "value": None},
            ],
            "raw_tomorrow": None,
            "prices": [{"time": start.isoformat(), "price": 0.2}],
        },
    )
    tpl = _blueprint()["variables"]["attr_slots"]
    base = {**_defaults(), "price_sensor": SENSOR}
    assert _render(hass, tpl, {**base, "price_source": "nordpool"}) == [
        [start.timestamp(), 0.1]
    ]
    assert _render(hass, tpl, {**base, "price_source": "entsoe"}) == [
        [start.timestamp(), 0.2]
    ]


async def test_tibber_reply_is_normalised(hass):
    start = DAY + timedelta(hours=2)
    reply = {
        "prices": {
            "Home": [
                {"start_time": start.isoformat(), "price": 0.42},
                {"start_time": (start + timedelta(hours=1)).isoformat(), "price": 0.4},
            ],
            "Cabin": [{"start_time": start.isoformat(), "price": 9.0}],
        }
    }
    step = _blueprint()["actions"][0]["then"][1]["variables"]["tibber_slots"]
    out = _render(hass, step, {"tibber_reply": reply})
    assert out == [[start.timestamp(), 0.42], [start.timestamp() + 3600, 0.4]]
    # A failed call leaves no reply: no prices, not an error
    assert _render(hass, step, {}) == []


async def test_blueprint_installed_into_config(hass, tmp_path):
    """The integration copies its blueprints where HA looks for blueprints,
    rewrites one only when it changed, and survives a read-only config."""
    from custom_components.thermiq_mqtt import (
        _install_blueprints,
        async_install_blueprints,
    )

    shipped = sorted(p.name for p in BLUEPRINT.parent.glob("*.yaml"))
    assert {"hotwater_cheap_hours.yaml", "floor_preheat.yaml"} <= set(shipped)
    folder = tmp_path / "blueprints" / "automation" / "thermiq_mqtt"
    target = folder / BLUEPRINT.name
    assert _install_blueprints(str(tmp_path)) == shipped
    assert target.read_bytes() == BLUEPRINT.read_bytes()
    # Unchanged: not written again
    assert _install_blueprints(str(tmp_path)) == []
    # An older copy is replaced by the shipped one, the others left alone
    target.write_text("old")
    assert _install_blueprints(str(tmp_path)) == [BLUEPRINT.name]
    assert target.read_bytes() == BLUEPRINT.read_bytes()

    # A config folder that cannot be written is logged, not raised
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the folder should be")
    hass.config.config_dir = str(blocked)
    await async_install_blueprints(hass)
