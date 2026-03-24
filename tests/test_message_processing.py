"""Tests for MQTT message processing in HeatPump.message_received.

Verifies that:
- Temperature decimal combination (r01+r02, r03+r04) runs once, not per key
- Register values are correctly stored in _hpstate
- Post-processing fields (mqtt_counter, time, communication_status) are set
- Non-ThermIQ messages are rejected
- Invalid JSON is handled gracefully
"""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

import sys
import os

# Add the repo root to path so we can import custom_components
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import (
    make_config_entry,
    make_hass,
    make_mqtt_message,
    SAMPLE_MQTT_PAYLOAD,
)


@pytest.fixture
def heatpump():
    """Create a real HeatPump instance with mocked HA dependencies."""
    hass = make_hass()
    entry = make_config_entry()

    with patch("custom_components.thermiq_mqtt.heatpump.mqtt"):
        from custom_components.thermiq_mqtt.heatpump import HeatPump

        hp = HeatPump(hass, entry)
        # Simulate update_config without awaiting mqtt subscribe
        hp._langid = 0
        hp._dbg = False
        hp._mqtt_base = "ThermIQ/ThermIQ-mqtt/"
        hp._hexFormat = False
        hp._data_topic = hp._mqtt_base + "data"
        hp._cmd_topic = hp._mqtt_base + "write"
        hp._set_topic = hp._mqtt_base + "set"
        hp._hpstate["mqtt_counter"] = 0
    return hp


class TestTemperatureDecimalCombination:
    """Test that r01/r02 and r03/r04 decimal combination works correctly."""

    @pytest.mark.asyncio
    async def test_indoor_temp_combined_correctly(self, heatpump):
        """r01 should be r01 + r02/10, computed exactly once."""
        msg = make_mqtt_message(SAMPLE_MQTT_PAYLOAD)
        await heatpump.message_received(msg)

        # r01=21, r02=3 → indoor_t should be 21.3
        assert heatpump._hpstate["r01"] == pytest.approx(21.3)

    @pytest.mark.asyncio
    async def test_target_temp_combined_correctly(self, heatpump):
        """r03 should be r03 + r04/10, computed exactly once."""
        msg = make_mqtt_message(SAMPLE_MQTT_PAYLOAD)
        await heatpump.message_received(msg)

        # r03=22, r04=5 → indoor_target_t should be 22.5
        assert heatpump._hpstate["r03"] == pytest.approx(22.5)

    @pytest.mark.asyncio
    async def test_temp_not_corrupted_by_loop(self, heatpump):
        """Regression: post-processing must run once, not per JSON key.

        The original bug ran r01 += r02/10 inside the for loop,
        so with N keys the result was r01 + N*(r02/10).
        With our sample payload (~20 keys), this would give ~27 instead of 21.3.
        """
        msg = make_mqtt_message(SAMPLE_MQTT_PAYLOAD)
        await heatpump.message_received(msg)

        # If the bug existed, r01 would be ~21 + 20*0.3 = 27.0
        # Correct value is 21.3
        assert heatpump._hpstate["r01"] < 22.0
        assert heatpump._hpstate["r01"] == pytest.approx(21.3)

    @pytest.mark.asyncio
    async def test_multiple_messages_dont_accumulate(self, heatpump):
        """Processing two messages should not keep adding decimals."""
        msg = make_mqtt_message(SAMPLE_MQTT_PAYLOAD)
        await heatpump.message_received(msg)
        first_r01 = heatpump._hpstate["r01"]

        # Second message with same values
        await heatpump.message_received(msg)
        second_r01 = heatpump._hpstate["r01"]

        # The raw values get overwritten each time, so result should be same
        assert second_r01 == pytest.approx(first_r01)


class TestRegisterMapping:
    """Test that MQTT registers are correctly mapped to _hpstate."""

    @pytest.mark.asyncio
    async def test_decimal_registers_mapped_to_hex(self, heatpump):
        """d000 should map to r00, d005 to r05, etc."""
        msg = make_mqtt_message(SAMPLE_MQTT_PAYLOAD)
        await heatpump.message_received(msg)

        assert heatpump._hpstate["r00"] == 5   # outdoor_t
        assert heatpump._hpstate["r05"] == 35  # supplyline_t
        assert heatpump._hpstate["r06"] == 28  # returnline_t
        assert heatpump._hpstate["r07"] == 48  # boiler_t

    @pytest.mark.asyncio
    async def test_hex_registers_stored_directly(self, heatpump):
        """Registers in hex format (r00, r05) should be stored as-is."""
        payload = dict(SAMPLE_MQTT_PAYLOAD)
        # Replace decimal with hex format
        del payload["d000"]
        payload["r00"] = 7
        msg = make_mqtt_message(payload)
        await heatpump.message_received(msg)

        assert heatpump._hpstate["r00"] == 7

    @pytest.mark.asyncio
    async def test_evu_register_mapped(self, heatpump):
        """EVU register should be stored under 'evu' key."""
        payload = dict(SAMPLE_MQTT_PAYLOAD)
        payload["EVU"] = 1
        msg = make_mqtt_message(payload)
        await heatpump.message_received(msg)

        assert heatpump._hpstate["evu"] == 1


class TestPostProcessing:
    """Test post-processing fields after message_received."""

    @pytest.mark.asyncio
    async def test_mqtt_counter_incremented(self, heatpump):
        """mqtt_counter should increment by 1 per message."""
        assert heatpump._hpstate["mqtt_counter"] == 0

        msg = make_mqtt_message(SAMPLE_MQTT_PAYLOAD)
        await heatpump.message_received(msg)
        assert heatpump._hpstate["mqtt_counter"] == 1

        await heatpump.message_received(msg)
        assert heatpump._hpstate["mqtt_counter"] == 2

    @pytest.mark.asyncio
    async def test_time_extracted(self, heatpump):
        """time_str should be set from the 'time' field."""
        msg = make_mqtt_message(SAMPLE_MQTT_PAYLOAD)
        await heatpump.message_received(msg)

        assert heatpump._hpstate["time_str"] == "2026-03-24T12:00:00"

    @pytest.mark.asyncio
    async def test_time_uppercase_key(self, heatpump):
        """Should also handle 'Time' (capitalized) key."""
        payload = dict(SAMPLE_MQTT_PAYLOAD)
        del payload["time"]
        payload["Time"] = "2026-03-24T13:00:00"
        msg = make_mqtt_message(payload)
        await heatpump.message_received(msg)

        assert heatpump._hpstate["time_str"] == "2026-03-24T13:00:00"

    @pytest.mark.asyncio
    async def test_communication_status_ok(self, heatpump):
        """communication_status should be set from vp_read."""
        msg = make_mqtt_message(SAMPLE_MQTT_PAYLOAD)
        await heatpump.message_received(msg)

        assert heatpump._hpstate["communication_status"] == "Ok"

    @pytest.mark.asyncio
    async def test_communication_status_default(self, heatpump):
        """Without vp_read, communication_status defaults to 'Ok'."""
        payload = dict(SAMPLE_MQTT_PAYLOAD)
        del payload["vp_read"]
        msg = make_mqtt_message(payload)
        await heatpump.message_received(msg)

        assert heatpump._hpstate["communication_status"] == "Ok"

    @pytest.mark.asyncio
    async def test_bus_event_fired(self, heatpump):
        """A bus event should be fired after processing."""
        msg = make_mqtt_message(SAMPLE_MQTT_PAYLOAD)
        await heatpump.message_received(msg)

        heatpump._hass.bus.fire.assert_called_with(
            "thermiq_mqtt_vp1_msg_rec_event", {}
        )


class TestMessageRejection:
    """Test that invalid messages are handled gracefully."""

    @pytest.mark.asyncio
    async def test_non_thermiq_message_rejected(self, heatpump):
        """Messages not from ThermIQ should be logged and ignored."""
        payload = {"Client_Name": "SomeOtherDevice", "d000": 5}
        msg = make_mqtt_message(payload)
        await heatpump.message_received(msg)

        # mqtt_counter should NOT increment
        assert heatpump._hpstate["mqtt_counter"] == 0

    @pytest.mark.asyncio
    async def test_invalid_json_handled(self, heatpump):
        """Invalid JSON should not raise an exception."""
        msg = MagicMock()
        msg.payload = "not valid json {{"
        # Should not raise
        await heatpump.message_received(msg)
        assert heatpump._hpstate["mqtt_counter"] == 0
