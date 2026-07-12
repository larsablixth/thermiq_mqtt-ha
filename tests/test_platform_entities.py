"""Unit tests for the number/select/switch entities.

The entities read the shared heatpump state and write via send_mqtt_reg,
so a HeatPump with a mocked hass and a mocked send_mqtt_reg is enough -
no full Home Assistant instance is needed.
"""

from unittest.mock import AsyncMock, MagicMock

from custom_components.thermiq_mqtt.heatpump import HeatPump
from custom_components.thermiq_mqtt.number import ThermIQNumber
from custom_components.thermiq_mqtt.select import ThermIQSelect
from custom_components.thermiq_mqtt.switch import ThermIQSwitch


def _make_heatpump(has_data=True):
    hass = MagicMock()
    hass.loop.time.return_value = 1000.0
    entry = MagicMock()
    entry.data = {"id_name": "vp1"}
    hp = HeatPump(hass, entry)
    hp._hpstate["mqtt_counter"] = 1 if has_data else 0
    if has_data:
        hp._last_message_time = 1000.0
    hp.send_mqtt_reg = AsyncMock()  # type: ignore[method-assign]
    return hp


# --- Number -----------------------------------------------------------


def test_number_reads_register_and_bounds():
    hp = _make_heatpump()
    entity = ThermIQNumber(hp, "indoor_requested_t")  # r32, 0..50
    assert entity.native_value is None
    hp._hpstate["r32"] = 20
    assert entity.native_value == 20
    assert entity.native_min_value == 0
    assert entity.native_max_value == 50
    assert entity.available is True


async def test_number_write_sends_mqtt():
    hp = _make_heatpump()
    entity = ThermIQNumber(hp, "indoor_requested_t")
    hp._hpstate["r32"] = 20
    await entity.async_set_native_value(21)
    hp.send_mqtt_reg.assert_awaited_once_with("indoor_requested_t", 21, 0xFFFF)
    assert hp._hpstate["r32"] == 21


async def test_number_write_ignored_without_data():
    hp = _make_heatpump(has_data=False)
    entity = ThermIQNumber(hp, "indoor_requested_t")
    await entity.async_set_native_value(21)
    hp.send_mqtt_reg.assert_not_awaited()


async def test_number_write_skipped_when_unchanged():
    hp = _make_heatpump()
    entity = ThermIQNumber(hp, "indoor_requested_t")
    hp._hpstate["r32"] = 21
    await entity.async_set_native_value(21)
    hp.send_mqtt_reg.assert_not_awaited()


def test_number_unavailable_before_first_message():
    hp = _make_heatpump(has_data=False)
    entity = ThermIQNumber(hp, "indoor_requested_t")
    assert entity.available is False


# --- Select -----------------------------------------------------------


def test_select_maps_register_to_option():
    hp = _make_heatpump()
    entity = ThermIQSelect(hp, "main_mode")  # r33
    assert entity.current_option is None
    hp._hpstate["r33"] = 2
    assert entity.current_option is not None
    assert entity.current_option.startswith("2 - ")
    assert len(entity.options) == 5


async def test_select_write_sends_mqtt():
    hp = _make_heatpump()
    entity = ThermIQSelect(hp, "main_mode")
    hp._hpstate["r33"] = 0
    option = entity.options[2]
    await entity.async_select_option(option)
    hp.send_mqtt_reg.assert_awaited_once_with("main_mode", 2, 0xFFFF)
    assert hp._hpstate["r33"] == 2


async def test_select_rejects_unknown_option():
    hp = _make_heatpump()
    entity = ThermIQSelect(hp, "main_mode")
    await entity.async_select_option("not a mode")
    hp.send_mqtt_reg.assert_not_awaited()


# --- Switch -----------------------------------------------------------


def test_switch_reads_register():
    hp = _make_heatpump()
    entity = ThermIQSwitch(hp, "heatpump_evu_block")  # reg 'evu'
    assert entity.is_on is None
    hp._hpstate["evu"] = 1
    assert entity.is_on is True
    hp._hpstate["evu"] = 0
    assert entity.is_on is False


async def test_switch_turn_on_off_sends_mqtt():
    hp = _make_heatpump()
    entity = ThermIQSwitch(hp, "heatpump_evu_block")
    hp._hpstate["evu"] = 0
    await entity.async_turn_on()
    hp.send_mqtt_reg.assert_awaited_once_with("heatpump_evu_block", 1, 0xFFFF)
    hp.send_mqtt_reg.reset_mock()
    await entity.async_turn_off()
    hp.send_mqtt_reg.assert_awaited_once_with("heatpump_evu_block", 0, 0xFFFF)


async def test_switch_write_ignored_without_data():
    hp = _make_heatpump(has_data=False)
    entity = ThermIQSwitch(hp, "heatpump_evu_block")
    await entity.async_turn_on()
    hp.send_mqtt_reg.assert_not_awaited()
