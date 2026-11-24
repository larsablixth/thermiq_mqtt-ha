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


def _decision_vars() -> dict[str, Any]:
    """The variables step holding the decision, wherever it sits."""
    for action in _load(BLUEPRINT)["actions"]:
        if "decision" in action.get("variables", {}):
            variables: dict[str, Any] = action["variables"]
            return variables
    raise AssertionError("no decision step in the blueprint")


def _forecast_step() -> dict[str, Any]:
    """The step that fetches and normalises the hourly forecast."""
    for action in _load(BLUEPRINT)["actions"]:
        if "weather.get_forecasts" in str(action):
            step: dict[str, Any] = action
            return step
    raise AssertionError("no forecast step in the blueprint")


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
        "forecast_slots": [],
        **inputs,
    }
    template = _decision_vars()["decision"]
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
    template = _decision_vars()["new_target"]
    out = _render(hass, template, {**_defaults(), "decision": decision})
    assert float(out) == expected


# Spot 0.30 now, 1.50 from 06: a clear boost at 02:00 without weather
SPIKE = [0.30] * 6 + [1.50] * 18 + [0.30] * 24
WEATHER = "weather.home"


def _forecast(start_hour: float, temps: list[float]) -> list[list[float]]:
    """Hourly forecast from start_hour on, as the blueprint keeps it."""
    return [
        [(DAY + timedelta(hours=start_hour + i)).timestamp(), c]
        for i, c in enumerate(temps)
    ]


async def _decide_with_weather(hass, freezer, now_c, temps, prices=SPIKE, **inputs):
    hass.states.async_set(WEATHER, "cloudy", {"temperature": now_c})
    return await _decide(
        hass,
        freezer,
        2,
        prices,
        weather_entity=WEATHER,
        forecast_slots=_forecast(3, temps),
        **inputs,
    )


async def test_much_warmer_ahead_skips_the_boost(hass, freezer):
    """-10 now, about -3 on average over the next 8 h: 7 °C warmer."""
    temps = [-8, -6, -4, -3, -2, -1, 0, 0]
    assert await _decide_with_weather(hass, freezer, -10, temps) == "normal"


async def test_slightly_warmer_ahead_still_boosts(hass, freezer):
    temps = [-9, -8, -8, -7, -7, -7, -7, -7]
    assert await _decide_with_weather(hass, freezer, -10, temps) == "boost"


async def test_colder_ahead_still_boosts(hass, freezer):
    assert await _decide_with_weather(hass, freezer, -5, [-12] * 8) == "boost"


async def test_warmer_by_is_a_setting(hass, freezer):
    temps = [-7] * 8  # 3 °C warmer than now
    assert await _decide_with_weather(hass, freezer, -10, temps) == "boost"
    assert (
        await _decide_with_weather(hass, freezer, -10, temps, warmer_by=2) == "normal"
    )


async def test_only_the_look_ahead_counts(hass, freezer):
    """A warm spell beyond the 8 h look-ahead does not stop the boost."""
    temps = [-10] * 8 + [10] * 16
    assert await _decide_with_weather(hass, freezer, -10, temps) == "boost"


async def test_negative_price_boosts_whatever_the_weather(hass, freezer):
    prices = [0.3] * 48
    prices[2] = -0.5
    assert await _decide_with_weather(hass, freezer, -10, [5] * 8, prices) == "boost"


async def test_no_forecast_ignores_the_weather(hass, freezer):
    """No hourly forecast (or no weather entity): behaves as before."""
    assert await _decide_with_weather(hass, freezer, -10, []) == "boost"
    hass.states.async_set(WEATHER, "unavailable", {})
    assert (
        await _decide(
            hass,
            freezer,
            2,
            SPIKE,
            weather_entity=WEATHER,
            forecast_slots=_forecast(3, [10] * 8),
        )
        == "boost"
    )


async def test_forecast_reply_is_normalised(hass):
    """weather.get_forecasts reply -> [[timestamp, °C], ...], skipping
    entries without a temperature."""
    template = _forecast_step()["then"][1]["variables"]["forecast_slots"]
    reply = {
        WEATHER: {
            "forecast": [
                {"datetime": "2026-01-15T03:00:00+01:00", "temperature": -4.5},
                {"datetime": "2026-01-15T04:00:00+01:00", "temperature": None},
                {"datetime": "2026-01-15T05:00:00+01:00", "temperature": -3},
            ]
        }
    }
    out = _render(hass, template, {"weather_entity": WEATHER, "forecast_reply": reply})
    assert out == [
        [(DAY + timedelta(hours=3)).timestamp(), -4.5],
        [(DAY + timedelta(hours=5)).timestamp(), -3],
    ]


def test_forecast_is_off_by_default():
    assert _defaults()["weather_entity"] == ""


def test_price_conversions_match_the_hot_water_blueprint():
    """Same sources, same shapes: the conversion templates must not drift
    apart (the hot water tests exercise them)."""
    floor, hot = _load(BLUEPRINT), _load(HOTWATER)
    assert floor["variables"]["attr_slots"] == hot["variables"]["attr_slots"]
    assert floor["actions"][0] == hot["actions"][0]
