"""Tests for HeatPump MQTT message handling and availability."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.thermiq_mqtt.heatpump import HeatPump, AVAILABILITY_TIMEOUT


def _make_heatpump(now=1000.0):
    hass = MagicMock()
    hass.loop.time.return_value = now
    entry = MagicMock()
    entry.data = {"id_name": "vp1"}
    hp = HeatPump(hass, entry)
    # mqtt_counter is normally initialised in update_config()
    hp._hpstate["mqtt_counter"] = 0
    return hp, hass


def _message(payload: dict):
    msg = MagicMock()
    msg.payload = json.dumps(payload)
    return msg


async def test_message_populates_state_and_combines_decimals():
    hp, hass = _make_heatpump()
    await hp.message_received(
        _message(
            {
                "Client_Name": "ThermIQ_x",
                "r00": 21,
                "r01": 20,
                "r02": 5,
                "r03": 18,
                "r04": 3,
            }
        )
    )
    assert hp._hpstate["r00"] == 21
    assert hp._hpstate["r01"] == pytest.approx(20.5)  # 20 + 5/10
    assert hp._hpstate["r03"] == pytest.approx(18.3)  # 18 + 3/10
    assert hp._hpstate["mqtt_counter"] == 1
    hass.bus.fire.assert_called()


async def test_decimals_not_combined_when_absent():
    hp, _ = _make_heatpump()
    # A partial message without r01/r02 must not touch r01
    await hp.message_received(_message({"Client_Name": "ThermIQ_x", "r00": 7}))
    assert hp._hpstate["r01"] is None


async def test_foreign_payload_is_ignored():
    hp, _ = _make_heatpump()
    await hp.message_received(_message({"Client_Name": "Other", "r00": 1}))
    assert hp._hpstate["r00"] is None
    assert hp._hpstate["mqtt_counter"] == 0


async def test_invalid_json_does_not_raise():
    hp, _ = _make_heatpump()
    msg = MagicMock()
    msg.payload = "{not valid json"
    await hp.message_received(msg)  # must not raise
    assert hp._hpstate["mqtt_counter"] == 0


async def test_unknown_key_does_not_abort_processing():
    hp, _ = _make_heatpump()
    # 'dXX' makes int() fail; r00 must still be stored
    await hp.message_received(
        _message({"Client_Name": "ThermIQ_x", "dXX": 1, "r00": 9})
    )
    assert hp._hpstate["r00"] == 9


async def test_availability_reflects_last_message():
    hp, hass = _make_heatpump(now=1000.0)
    assert hp.available is False  # nothing received yet
    await hp.message_received(_message({"Client_Name": "ThermIQ_x", "r00": 1}))
    assert hp.available is True
    # advance the clock past the timeout -> unavailable
    hass.loop.time.return_value = 1000.0 + AVAILABILITY_TIMEOUT.total_seconds() + 1
    assert hp.available is False


def _make_writable_heatpump():
    """A heatpump with the topics send_mqtt_reg needs, and a mocked publish."""
    hp, hass = _make_heatpump()
    hp._cmd_topic = "ThermIQ/ThermIQ-mqtt/write"
    hp._set_topic = "ThermIQ/ThermIQ-mqtt/set"
    hp._hexFormat = False
    return hp, hass


async def test_send_mqtt_reg_publishes_value_in_range():
    hp, hass = _make_writable_heatpump()
    with patch(
        "custom_components.thermiq_mqtt.heatpump.mqtt.async_publish",
        new=AsyncMock(),
    ) as publish:
        # indoor_requested_t (r32) allows 0..50
        await hp.send_mqtt_reg("indoor_requested_t", 21, 0xFFFF)
        hass.async_create_task.assert_called_once()
        publish.assert_called_once()
        topic, payload = publish.call_args[0][1], publish.call_args[0][2]
        assert topic == hp._cmd_topic
        assert json.loads(payload) == {"d050": 21}


async def test_send_mqtt_reg_rejects_value_out_of_range():
    hp, hass = _make_writable_heatpump()
    with patch(
        "custom_components.thermiq_mqtt.heatpump.mqtt.async_publish",
        new=AsyncMock(),
    ) as publish:
        await hp.send_mqtt_reg("indoor_requested_t", 95, 0xFFFF)
        publish.assert_not_called()
        hass.async_create_task.assert_not_called()


async def test_send_mqtt_reg_rejects_non_boolean_for_switch_register():
    hp, hass = _make_writable_heatpump()
    with patch(
        "custom_components.thermiq_mqtt.heatpump.mqtt.async_publish",
        new=AsyncMock(),
    ) as publish:
        await hp.send_mqtt_reg("heatpump_evu_block", 5, 0xFFFF)
        publish.assert_not_called()


async def test_send_mqtt_reg_accepts_boolean_for_switch_register():
    hp, hass = _make_writable_heatpump()
    with patch(
        "custom_components.thermiq_mqtt.heatpump.mqtt.async_publish",
        new=AsyncMock(),
    ) as publish:
        await hp.send_mqtt_reg("heatpump_evu_block", 1, 0xFFFF)
        publish.assert_called_once()
        topic, payload = publish.call_args[0][1], publish.call_args[0][2]
        assert topic == hp._set_topic
        assert json.loads(payload) == {"EVU": 1}


async def test_send_mqtt_reg_rejects_non_numeric_value():
    hp, hass = _make_writable_heatpump()
    with patch(
        "custom_components.thermiq_mqtt.heatpump.mqtt.async_publish",
        new=AsyncMock(),
    ) as publish:
        await hp.send_mqtt_reg("indoor_requested_t", "high", 0xFFFF)
        publish.assert_not_called()
