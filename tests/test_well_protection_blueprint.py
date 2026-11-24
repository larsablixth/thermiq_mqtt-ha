"""The well protection blueprint's decision, rendered by Home Assistant itself.

The templates are pulled out of the blueprint YAML and rendered with HA's own
template engine, with the heat pump's entities set as states.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

BLUEPRINT = (
    Path(__file__).parent.parent
    / "custom_components"
    / "thermiq_mqtt"
    / "blueprints"
    / "well_protection.yaml"
)
BRINE = "sensor.thermiq_mqtt_vp1_brine_out_t"
PUMP = "binary_sensor.thermiq_mqtt_vp1_brine_pump_on"
ALARM = "number.thermiq_mqtt_vp1_brine_min_t"
CURVE_MAX = "number.thermiq_mqtt_vp1_integral1_curve_max"


class _Loader(yaml.SafeLoader):
    """SafeLoader that accepts the blueprint's !input tags."""


_Loader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))


def _load() -> dict[str, Any]:
    data: dict[str, Any] = yaml.load(
        BLUEPRINT.read_text(encoding="utf-8"), Loader=_Loader
    )
    return data


def _defaults() -> dict[str, Any]:
    inputs = _load()["blueprint"]["input"]
    return {k: v["default"] for k, v in inputs.items() if "default" in v}


def _templates() -> dict[str, Any]:
    variables: dict[str, Any] = _load()["actions"][0]["variables"]
    return variables


def _decide(
    hass: HomeAssistant,
    brine: float | str,
    curve_max: float = 55,
    alarm: float | str = -8,
    pump: str = "on",
    **inputs: Any,
) -> tuple[str, str]:
    """Set the heat pump's states, return (decision, new_max)."""
    hass.states.async_set(BRINE, str(brine))
    hass.states.async_set(PUMP, pump)
    hass.states.async_set(ALARM, str(alarm))
    hass.states.async_set(CURVE_MAX, str(curve_max))
    variables = {**_defaults(), **inputs}
    templates = _templates()
    decision = str(Template(templates["decision"], hass).async_render(variables))
    new_max = Template(templates["new_max"], hass).async_render(
        {**variables, "decision": decision.strip()}
    )
    return decision.strip(), str(new_max)


# Defaults: alarm -8, margin 2 -> ease below -6; recover 3 -> restore at -3


async def test_brine_well_above_the_limit(hass):
    assert _decide(hass, 0) == ("normal", "55.0")


async def test_brine_near_the_alarm_eases(hass):
    assert _decide(hass, -6.5) == ("ease", "45.0")


async def test_stays_eased_until_recovered(hass):
    """Already eased: -4 is above the trigger but not yet recovered."""
    assert _decide(hass, -4, curve_max=45)[0] == "ease"
    assert _decide(hass, -3, curve_max=45) == ("normal", "55.0")


async def test_not_eased_in_the_recovery_band(hass):
    """Not eased yet: -4 is above the trigger, so nothing to do."""
    assert _decide(hass, -4, curve_max=55)[0] == "normal"


async def test_ignores_the_reading_while_the_brine_pump_stands(hass):
    assert _decide(hass, -10, pump="off") == ("hold", "hold")


async def test_no_reading_changes_nothing(hass):
    assert _decide(hass, "unavailable") == ("hold", "hold")


async def test_alarm_off_uses_the_fallback_limit(hass):
    """-15 means the heat pump's alarm is off: fall back to -5."""
    assert _decide(hass, -4.5, alarm=-15)[0] == "normal"
    assert _decide(hass, -5.5, alarm=-15)[0] == "ease"


async def test_unreadable_alarm_uses_the_fallback_limit(hass):
    assert _decide(hass, -5.5, alarm="unavailable")[0] == "ease"


async def test_margin_and_eased_max_are_settings(hass):
    assert _decide(hass, -5, margin=4)[0] == "ease"
    assert _decide(hass, -7, eased_max=48) == ("ease", "48.0")


@pytest.mark.parametrize(
    ("curve_max", "brine", "writes"),
    [(55, 0, False), (45, -7, False), (55, -7, True), (45, 0, True)],
)
async def test_writes_only_on_change(hass, curve_max, brine, writes):
    _, new_max = _decide(hass, brine, curve_max=curve_max)
    condition = _load()["actions"][1]["value_template"]
    out = Template(condition, hass).async_render(
        {"new_max": new_max, "curve_max_entity": CURVE_MAX}
    )
    assert out is writes
