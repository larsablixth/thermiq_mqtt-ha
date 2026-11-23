"""The floor pre-heat blueprint's decision, rendered by Home Assistant itself.

The templates are pulled out of the blueprint YAML and rendered with HA's own
template engine at a frozen clock. Prices are given as the blueprint keeps
them internally ([[timestamp, price], ...]); the Nordpool/Entso-e/Tibber
conversions are the same templates as the hot water blueprint's and are
checked to stay identical.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

FOLDER = Path(__file__).parent.parent / "custom_components" / "thermiq_mqtt"
BLUEPRINT = FOLDER / "blueprints" / "floor_preheat.yaml"
HOTWATER = FOLDER / "blueprints" / "hotwater_cheap_hours.yaml"
TZ = ZoneInfo("Europe/Stockholm")
# A Thursday in January: Vattenfall's peak hours apply
DAY = datetime(2026, 1, 15, tzinfo=TZ)
# Vattenfall time tariff, SEK/kWh incl. VAT: energy tax + off-peak transfer
OTHER = 0.45 + 0.305
PEAK = 0.46


class _Loader(yaml.SafeLoader):
    """SafeLoader that accepts the blueprint's !input tags."""


_Loader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))


@pytest.fixture(autouse=True)
async def stockholm(hass: HomeAssistant) -> None:
    """Peak hours are local time: run HA in the zone the test data is in."""
    await hass.config.async_set_time_zone("Europe/Stockholm")


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
    return data


def _defaults() -> dict[str, Any]:
    inputs = _load(BLUEPRINT)["blueprint"]["input"]
    return {k: v["default"] for k, v in inputs.items() if "default" in v}


def _slots(prices: list[float], minutes: int = 60) -> list[list[float]]:
    return [
        [(DAY + timedelta(minutes=minutes * i)).timestamp(), p]
        for i, p in enumerate(prices)
    ]


def _render(hass: HomeAssistant, template: str, variables: dict[str, Any]) -> Any:
    return Template(template, hass).async_render(variables)


async def _decide(hass, freezer, at_hour: float, prices, **inputs) -> str:
    freezer.move_to(DAY + timedelta(hours=at_hour, seconds=30))
    variables = {
        **_defaults(),
        "price_source": "nordpool",
        "attr_slots": (
            _slots(prices) if prices and not isinstance(prices[0], list) else prices
        ),
        "tibber_slots": [],
        **inputs,
    }
    template = _load(BLUEPRINT)["actions"][1]["variables"]["decision"]
    return str(_render(hass, template, variables)).strip()


async def test_spot_gap_alone(hass, freezer):
    """Spot 0.30 now, 1.50 from 06; with no fees 1.50/0.30 easily clears."""
    prices = [0.30] * 6 + [1.50] * 18 + [0.30] * 24
    assert await _decide(hass, freezer, 2, prices) == "boost"
    assert await _decide(hass, freezer, 8, prices) == "coast"


async def test_fixed_costs_shrink_the_gap(hass, freezer):
    """Spot 0.30 -> 0.60 doubles, but with 0.755 of fixed costs it is
    1.355/1.055 = 1.28: below 1.3."""
    prices = [0.30] * 6 + [0.60] * 18 + [0.30] * 24
    assert await _decide(hass, freezer, 2, prices) == "boost"
    assert await _decide(hass, freezer, 2, prices, other_fees=OTHER) == "normal"


async def test_time_tariff_alone_makes_winter_nights_worth_it(hass, freezer):
    """Flat spot: only Vattenfall's day/night fee differs, and on a winter
    weekday that is enough (1.815 / 1.355 = 1.34)."""
    flat = [0.6] * 48
    fees = {"other_fees": OTHER, "peak_fee": PEAK}
    assert await _decide(hass, freezer, 2, flat, **fees) == "boost"
    assert await _decide(hass, freezer, 10, flat, **fees) == "coast"
    assert await _decide(hass, freezer, 2, flat, other_fees=OTHER) == "normal"


async def test_no_peak_outside_peak_months(hass, freezer):
    flat = [0.6] * 48
    fees = {"other_fees": OTHER, "peak_fee": PEAK, "peak_months": ["6"]}
    assert await _decide(hass, freezer, 2, flat, **fees) == "normal"


async def test_peak_beyond_the_look_ahead_is_ignored(hass, freezer):
    prices = [0.3] * 48
    prices[20] = 3.0  # 18 hours after 02:00
    assert await _decide(hass, freezer, 2, prices) == "normal"
    assert await _decide(hass, freezer, 2, prices, window_hours=16) == "normal"
    assert await _decide(hass, freezer, 14, prices) == "boost"


async def test_uses_tomorrows_prices(hass, freezer):
    """At 22:00 the peak is tomorrow morning: visible once published."""
    today = [0.3] * 24
    tomorrow = [0.3] * 6 + [2.0] * 4 + [0.3] * 14
    assert await _decide(hass, freezer, 22, today + tomorrow) == "boost"
    assert await _decide(hass, freezer, 22, today) == "normal"


async def test_negative_price_always_boosts(hass, freezer):
    prices = [0.3] * 48
    prices[2] = -0.5
    assert await _decide(hass, freezer, 2, prices) == "boost"


async def test_quarter_hour_prices(hass, freezer):
    prices = [0.3] * 192
    prices[3 * 4 + 1] = 5.0  # 03:15, the very next quarter
    assert await _decide(hass, freezer, 3, _slots(prices, minutes=15)) == "boost"


async def test_no_prices(hass, freezer):
    assert await _decide(hass, freezer, 2, []) == "unknown"


@pytest.mark.parametrize(
    ("decision", "expected"),
    [("boost", 22.0), ("coast", 20.0), ("normal", 21.0), ("unknown", 21.0)],
)
async def test_new_target(hass, decision, expected):
    template = _load(BLUEPRINT)["actions"][1]["variables"]["new_target"]
    out = _render(hass, template, {**_defaults(), "decision": decision})
    assert float(out) == expected


def test_price_conversions_match_the_hot_water_blueprint():
    """Same sources, same shapes: the conversion templates must not drift
    apart (the hot water tests exercise them)."""
    floor, hot = _load(BLUEPRINT), _load(HOTWATER)
    assert floor["variables"]["attr_slots"] == hot["variables"]["attr_slots"]
    assert floor["actions"][0] == hot["actions"][0]
