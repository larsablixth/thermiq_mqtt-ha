"""The pre-heat blueprint's price decision, rendered by Home Assistant itself.

The blueprint keeps its whole decision in one template variable so it can be
exercised here without running an automation: the template is pulled out of
the YAML and rendered with HA's own template engine against a fake price
sensor, at a frozen clock.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

BLUEPRINT = (
    Path(__file__).parent.parent
    / "blueprints"
    / "automation"
    / "thermiq"
    / "price_preheat.yaml"
)
SENSOR = "sensor.price"
# 12:00 local on a fixed day; slot 12 is "now"
NOW = datetime(2026, 1, 15, 12, 0, 30, tzinfo=dt_util.get_default_time_zone())
DAY = NOW.replace(hour=0, minute=0, second=0)


class _Loader(yaml.SafeLoader):
    """SafeLoader that accepts the blueprint's !input tags."""


_Loader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))


def _blueprint() -> dict:
    data: dict = yaml.load(BLUEPRINT.read_text(encoding="utf-8"), Loader=_Loader)
    return data


def _defaults() -> dict:
    inputs = _blueprint()["blueprint"]["input"]
    return {k: v["default"] for k, v in inputs.items() if "default" in v}


def _nordpool(prices: list[float]) -> dict:
    """Hourly prices from midnight, in the Nordpool (HACS) attribute shape."""
    return {
        "raw_today": [
            {
                "start": DAY + timedelta(hours=h),
                "end": DAY + timedelta(hours=h + 1),
                "value": p,
            }
            for h, p in enumerate(prices)
        ]
    }


def _entsoe(prices: list[float]) -> dict:
    """Hourly prices from midnight, in the Entso-e attribute shape."""
    return {
        "prices": [
            {"time": (DAY + timedelta(hours=h)).isoformat(), "price": p}
            for h, p in enumerate(prices)
        ]
    }


def _flat(base: float, **at: float) -> list[float]:
    """24 hourly prices at `base`, with h<N>=price overrides."""
    prices = [base] * 24
    for key, value in at.items():
        prices[int(key[1:])] = value
    return prices


async def _decide(hass: HomeAssistant, freezer, attrs: dict, **inputs) -> str:
    freezer.move_to(NOW)
    hass.states.async_set(SENSOR, "0", attrs)
    variables = {**_defaults(), "price_sensor": SENSOR, **inputs}
    tpl = Template(_blueprint()["variables"]["decision"], hass)
    return str(tpl.async_render(variables)).strip()


@pytest.mark.parametrize("shape", [_nordpool, _entsoe])
async def test_boost_before_a_large_peak(hass, freezer, shape):
    """Spot 0.30 now, 0.90 in three hours, fee 1.0: 1.90/1.30 = 1.46."""
    attrs = shape(_flat(0.30, h15=0.90))
    assert await _decide(hass, freezer, attrs, fixed_fee=1.0) == "boost"


async def test_fixed_fee_kills_a_small_spot_gap(hass, freezer):
    """Spot doubles 0.30 -> 0.60, but in total it is 1.60/1.30 = 1.23."""
    attrs = _nordpool(_flat(0.30, h15=0.60))
    assert await _decide(hass, freezer, attrs, fixed_fee=1.0) == "normal"
    # Without fees the same spot gap would have looked worthwhile
    assert await _decide(hass, freezer, attrs, fixed_fee=0) == "boost"


async def test_peak_beyond_the_window_is_ignored(hass, freezer):
    """A peak 8 hours away is out of reach of a 6-hour slab."""
    attrs = _nordpool(_flat(0.30, h20=3.0))
    assert await _decide(hass, freezer, attrs, fixed_fee=1.0) == "normal"
    assert await _decide(hass, freezer, attrs, fixed_fee=1.0, window_hours=9) == "boost"


async def test_coast_through_the_peak_after_cheap_hours(hass, freezer):
    attrs = _nordpool(_flat(0.30, h12=1.50, h13=1.50))
    assert await _decide(hass, freezer, attrs, fixed_fee=1.0) == "coast"


async def test_coast_only_when_the_gap_clears_break_even(hass, freezer):
    attrs = _nordpool(_flat(0.30, h12=0.50))
    assert await _decide(hass, freezer, attrs, fixed_fee=1.0) == "normal"


async def test_negative_total_price_always_boosts(hass, freezer):
    attrs = _nordpool(_flat(0.30, h12=-0.50))
    assert await _decide(hass, freezer, attrs, fixed_fee=0) == "boost"


async def test_quarter_hour_slots(hass, freezer):
    """15-minute prices: the slot containing 12:00:30 is the 12:00 one."""
    slots = [
        {"start": DAY + timedelta(minutes=15 * q), "value": 0.30} for q in range(96)
    ]
    slots[12 * 4 + 1]["value"] = 5.0  # 12:15, the very next slot
    attrs = {"raw_today": slots}
    assert await _decide(hass, freezer, attrs, fixed_fee=1.0) == "boost"


async def test_no_prices_falls_back(hass, freezer):
    assert await _decide(hass, freezer, {}) == "unknown"
    assert await _decide(hass, freezer, {"raw_today": None}) == "unknown"


async def test_missing_values_are_skipped(hass, freezer):
    """Nordpool fills not-yet-published slots with None."""
    attrs = _nordpool(_flat(0.30, h15=0.90))
    attrs["raw_today"][14]["value"] = None
    assert await _decide(hass, freezer, attrs, fixed_fee=1.0) == "boost"


@pytest.mark.parametrize(
    ("decision", "expected"), [("boost", 22.0), ("coast", 20.0), ("normal", 21.0)]
)
async def test_target_follows_decision(hass, decision, expected):
    variables = {**_defaults(), "decision": decision}
    tpl = Template(_blueprint()["variables"]["target"], hass)
    assert float(tpl.async_render(variables)) == expected
