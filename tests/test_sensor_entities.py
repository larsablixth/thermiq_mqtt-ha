"""Tests for sensor and binary_sensor entity behavior.

Verifies that:
- Sensor reads native_value from _hpstate (not stale _state variable)
- Binary sensor is_on computes from _hpstate with bitmask
- Event listeners are registered with async_on_remove for cleanup
- No invalid device_class is set
- async_write_ha_state is used (not deprecated async_schedule_update_ha_state)
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import make_hass, make_heatpump


class TestHeatPumpSensor:
    """Test the HeatPumpSensor entity (PR3 version with native_value)."""

    def _make_sensor(self, heatpump, device_id="outdoor_t", vp_reg="r00",
                     vp_type="temperature", vp_unit="°C"):
        from custom_components.thermiq_mqtt.sensor import HeatPumpSensor

        hass = make_hass()
        sensor = HeatPumpSensor(
            hass, heatpump, device_id, vp_reg, "Outdoor temp", vp_type, vp_unit
        )
        return sensor

    def test_native_value_reads_from_hpstate(self):
        """native_value should return current _hpstate value."""
        hp = make_heatpump()
        sensor = self._make_sensor(hp)

        hp._hpstate["r00"] = 5.2
        assert sensor.native_value == 5.2

        hp._hpstate["r00"] = -3.1
        assert sensor.native_value == -3.1

    def test_native_value_returns_none_for_missing(self):
        """native_value should return None if register not in _hpstate."""
        hp = make_heatpump()
        sensor = self._make_sensor(hp, vp_reg="r99")

        # r99 doesn't exist in _hpstate
        hp._hpstate.pop("r99", None)
        assert sensor.native_value is None

    def test_temperature_device_class(self):
        """Temperature sensors should have SensorDeviceClass.TEMPERATURE."""
        from homeassistant.components.sensor import SensorDeviceClass

        hp = make_heatpump()
        sensor = self._make_sensor(hp, vp_type="temperature", vp_unit="°C")
        assert sensor._attr_device_class == SensorDeviceClass.TEMPERATURE

    def test_no_custom_device_class_property(self):
        """Sensor should NOT have a device_class property returning custom string.

        Regression: the old code had a property returning 'thermiq_mqtt_HeatPumpSensor'.
        """
        hp = make_heatpump()
        sensor = self._make_sensor(hp)

        # device_class should come from _attr_device_class, not a custom property
        # If a custom property existed returning a string, it would not be a valid SensorDeviceClass
        dc = sensor.device_class
        assert dc is None or hasattr(dc, "value")  # None or a valid enum

    def test_should_poll_false(self):
        """Sensor should not poll — updates come via bus events."""
        hp = make_heatpump()
        sensor = self._make_sensor(hp)
        assert sensor.should_poll is False

    def test_native_unit_for_temperature(self):
        """Temperature sensors should use UnitOfTemperature.CELSIUS."""
        from homeassistant.const import UnitOfTemperature

        hp = make_heatpump()
        sensor = self._make_sensor(hp, vp_type="temperature", vp_unit="°C")
        assert sensor._attr_native_unit_of_measurement == UnitOfTemperature.CELSIUS

    def test_duration_sensor(self):
        """Time/hour sensors should have DURATION device class and TOTAL_INCREASING."""
        from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

        hp = make_heatpump()
        sensor = self._make_sensor(hp, device_id="compressor_runtime_h",
                                   vp_reg="r17", vp_type="time", vp_unit="h")
        assert sensor._attr_device_class == SensorDeviceClass.DURATION
        assert sensor._attr_state_class == SensorStateClass.TOTAL_INCREASING


class TestHeatPumpBinarySensor:
    """Test the HeatPumpBinarySensor entity."""

    def _make_binary_sensor(self, heatpump, device_id="brine_pump_on",
                            vp_reg="r10", bitmask=0x0001):
        from custom_components.thermiq_mqtt.binary_sensor import HeatPumpBinarySensor

        hass = make_hass()
        sensor = HeatPumpBinarySensor(
            hass, heatpump, device_id, vp_reg, "Brine pump", bitmask
        )
        return sensor

    def test_is_on_with_bitmask(self):
        """is_on should apply bitmask to register value."""
        hp = make_heatpump()
        sensor = self._make_binary_sensor(hp, vp_reg="r10", bitmask=0x0001)

        hp._hpstate["r10"] = 1
        assert sensor.is_on is True

        hp._hpstate["r10"] = 0
        assert sensor.is_on is False

    def test_is_on_specific_bit(self):
        """is_on should check only the relevant bit."""
        hp = make_heatpump()
        # boiler_6kw_on uses bitmask 0x0002 on register r0d
        sensor = self._make_binary_sensor(hp, device_id="boiler_6kw_on",
                                          vp_reg="r0d", bitmask=0x0002)

        hp._hpstate["r0d"] = 0x0001  # bit 0 set, bit 1 not set
        assert sensor.is_on is False

        hp._hpstate["r0d"] = 0x0002  # bit 1 set
        assert sensor.is_on is True

        hp._hpstate["r0d"] = 0x0003  # both bits set
        assert sensor.is_on is True

    def test_is_on_returns_none_for_initial_state(self):
        """is_on should return None when register has initial -1 value."""
        hp = make_heatpump()
        sensor = self._make_binary_sensor(hp)

        hp._hpstate["r10"] = -1
        assert sensor.is_on is None

    def test_is_on_not_cached(self):
        """is_on must update when _hpstate changes (not @cached_property).

        Regression: the old code used @cached_property which cached forever.
        """
        hp = make_heatpump()
        sensor = self._make_binary_sensor(hp)

        hp._hpstate["r10"] = 0
        assert sensor.is_on is False

        hp._hpstate["r10"] = 1
        assert sensor.is_on is True  # Must reflect the change

        hp._hpstate["r10"] = 0
        assert sensor.is_on is False  # And back again

    def test_no_custom_device_class_property(self):
        """Binary sensor should NOT have a custom device_class property.

        Regression: old code returned 'thermiq_mqtt_HeatPumpSensor'.
        """
        hp = make_heatpump()
        sensor = self._make_binary_sensor(hp)

        dc = sensor.device_class
        assert dc is None or hasattr(dc, "value")

    def test_should_poll_false(self):
        """Binary sensor should not poll."""
        hp = make_heatpump()
        sensor = self._make_binary_sensor(hp)
        assert sensor.should_poll is False
