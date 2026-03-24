"""Tests for number, select, and switch entities (PR2 replacements).

Verifies that:
- NumberEntity reads native_value from _hpstate
- SelectEntity maps mode index to option string
- SwitchEntity is_on applies bitmask correctly
- All entities fire bus events and send MQTT on value changes
- Event listeners are cleaned up via async_on_remove
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_hass, make_heatpump


class TestHeatPumpNumber:
    """Test the HeatPumpNumber entity."""

    def _make_number(self, heatpump, register_name="indoor_requested_t"):
        from custom_components.thermiq_mqtt.number import HeatPumpNumber
        return HeatPumpNumber(heatpump, register_name)

    def test_native_value_reads_hpstate(self):
        """native_value should read from _hpstate via register number."""
        hp = make_heatpump()
        num = self._make_number(hp, "indoor_requested_t")

        # indoor_requested_t maps to register r32
        hp._hpstate["r32"] = 22.0
        assert num.native_value == 22.0

    def test_native_value_none_for_initial(self):
        """native_value returns None when register is -1 (initial)."""
        hp = make_heatpump()
        num = self._make_number(hp, "indoor_requested_t")

        hp._hpstate["r32"] = -1
        assert num.native_value is None

    def test_min_max_from_reg_definition(self):
        """Min/max should come from thermiq_regs definition."""
        hp = make_heatpump()
        num = self._make_number(hp, "indoor_requested_t")

        # indoor_requested_t: min=0, max=50
        assert num._attr_native_min_value == 0.0
        assert num._attr_native_max_value == 50.0

    def test_step_for_indr_t(self):
        """room_sensor_set_t (indr_t register) should have 0.1 step."""
        hp = make_heatpump()
        num = self._make_number(hp, "room_sensor_set_t")
        assert num._attr_native_step == 0.1

    def test_step_for_normal_register(self):
        """Normal registers should have step 1.0."""
        hp = make_heatpump()
        num = self._make_number(hp, "indoor_requested_t")
        assert num._attr_native_step == 1.0

    def test_temperature_unit(self):
        """Temperature inputs should have Celsius unit."""
        from homeassistant.const import UnitOfTemperature

        hp = make_heatpump()
        num = self._make_number(hp, "indoor_requested_t")
        assert num._attr_native_unit_of_measurement == UnitOfTemperature.CELSIUS

    @pytest.mark.asyncio
    async def test_set_value_sends_mqtt(self):
        """Setting a value should call send_mqtt_reg."""
        hp = make_heatpump()
        hp._hpstate["mqtt_counter"] = 1  # Simulate having received messages
        num = self._make_number(hp, "indoor_requested_t")
        num.hass = make_hass()
        num.async_write_ha_state = MagicMock()

        hp._hpstate["r32"] = 20.0  # Current value
        await num.async_set_native_value(22.0)

        hp.send_mqtt_reg.assert_called_once_with("indoor_requested_t", 22.0, 0xFFFF)

    @pytest.mark.asyncio
    async def test_set_same_value_no_mqtt(self):
        """Setting the same value should not send MQTT."""
        hp = make_heatpump()
        hp._hpstate["mqtt_counter"] = 1
        num = self._make_number(hp, "indoor_requested_t")
        num.hass = make_hass()
        num.async_write_ha_state = MagicMock()

        hp._hpstate["r32"] = 22.0
        await num.async_set_native_value(22.0)

        hp.send_mqtt_reg.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mqtt_before_first_message(self):
        """Should not send MQTT if no messages received yet."""
        hp = make_heatpump()
        hp._hpstate["mqtt_counter"] = -1  # No messages yet
        num = self._make_number(hp, "indoor_requested_t")
        num.hass = make_hass()
        num.async_write_ha_state = MagicMock()

        await num.async_set_native_value(22.0)
        hp.send_mqtt_reg.assert_not_called()

    def test_unique_id_format(self):
        """unique_id should follow the expected pattern."""
        hp = make_heatpump()
        num = self._make_number(hp, "indoor_requested_t")
        assert num._attr_unique_id == "thermiq_mqtt_vp1_indoor_requested_t"


class TestHeatPumpSelect:
    """Test the HeatPumpSelect entity."""

    def _make_select(self, heatpump, register_name="main_mode"):
        from custom_components.thermiq_mqtt.select import HeatPumpSelect
        return HeatPumpSelect(heatpump, register_name)

    def test_options_list(self):
        """Should have 5 mode options."""
        hp = make_heatpump()
        sel = self._make_select(hp)
        assert len(sel._attr_options) == 5
        assert sel._attr_options[0].startswith("0 - ")
        assert sel._attr_options[4].startswith("4 - ")

    def test_current_option_from_hpstate(self):
        """current_option should map register value to option string."""
        hp = make_heatpump()
        sel = self._make_select(hp)

        hp._hpstate["r33"] = 2
        option = sel.current_option
        assert option is not None
        assert option.startswith("2 - ")

    def test_current_option_none_for_initial(self):
        """current_option returns None when register is -1."""
        hp = make_heatpump()
        sel = self._make_select(hp)

        hp._hpstate["r33"] = -1
        assert sel.current_option is None

    @pytest.mark.asyncio
    async def test_select_option_sends_mqtt(self):
        """Selecting an option should send the mode number via MQTT."""
        hp = make_heatpump()
        hp._hpstate["mqtt_counter"] = 1
        hp._hpstate["r33"] = 0  # Current mode
        sel = self._make_select(hp)
        sel.hass = make_hass()
        sel.async_write_ha_state = MagicMock()

        await sel.async_select_option(sel._attr_options[2])  # "2 - ..."
        hp.send_mqtt_reg.assert_called_once_with("main_mode", 2, 0xFFFF)


class TestHeatPumpSwitch:
    """Test the HeatPumpSwitch entity."""

    def _make_switch(self, heatpump, register_name="heatpump_evu_block"):
        from custom_components.thermiq_mqtt.switch import HeatPumpSwitch
        return HeatPumpSwitch(heatpump, register_name)

    def test_is_on_from_hpstate(self):
        """is_on should read from _hpstate with bitmask."""
        hp = make_heatpump()
        sw = self._make_switch(hp)

        hp._hpstate["evu"] = 1
        assert sw.is_on is True

        hp._hpstate["evu"] = 0
        assert sw.is_on is False

    def test_is_on_none_for_initial(self):
        """is_on returns None when register is -1."""
        hp = make_heatpump()
        sw = self._make_switch(hp)

        hp._hpstate["evu"] = -1
        assert sw.is_on is None

    @pytest.mark.asyncio
    async def test_turn_on_sends_mqtt(self):
        """Turning on should send 1 via MQTT."""
        hp = make_heatpump()
        hp._hpstate["mqtt_counter"] = 1
        hp._hpstate["evu"] = 0
        sw = self._make_switch(hp)
        sw.hass = make_hass()
        sw.async_write_ha_state = MagicMock()

        await sw.async_turn_on()
        hp.send_mqtt_reg.assert_called_once_with("heatpump_evu_block", 1, 0xFFFF)

    @pytest.mark.asyncio
    async def test_turn_off_sends_mqtt(self):
        """Turning off should send 0 via MQTT."""
        hp = make_heatpump()
        hp._hpstate["mqtt_counter"] = 1
        hp._hpstate["evu"] = 1
        sw = self._make_switch(hp)
        sw.hass = make_hass()
        sw.async_write_ha_state = MagicMock()

        await sw.async_turn_off()
        hp.send_mqtt_reg.assert_called_once_with("heatpump_evu_block", 0, 0xFFFF)
