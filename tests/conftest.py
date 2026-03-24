"""Shared test fixtures for ThermIQ MQTT tests."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from custom_components.thermiq_mqtt.heatpump.thermiq_regs import reg_id


def make_config_entry(
    id_name="vp1",
    mqtt_node="ThermIQ/ThermIQ-mqtt",
    language="en",
    hexformat=False,
    mqtt_dbg=False,
    migrate_data=False,
):
    """Create a mock ConfigEntry."""
    entry = MagicMock()
    entry.data = {
        "id_name": id_name,
        "mqtt_node": mqtt_node,
        "language": language,
        "hexformat": hexformat,
        "thermiq_dbg": mqtt_dbg,
        "migrate_data": migrate_data,
    }
    entry.entry_id = "test_entry_id"
    entry.add_update_listener = MagicMock(return_value=MagicMock())
    entry.async_on_unload = MagicMock()
    return entry


def make_hass():
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.bus = MagicMock()
    hass.bus.async_listen = MagicMock(return_value=MagicMock())
    hass.bus.fire = MagicMock()
    hass.async_create_task = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


def make_heatpump(hass=None, entry=None):
    """Create a HeatPump-like object with realistic _hpstate."""
    if hass is None:
        hass = make_hass()
    if entry is None:
        entry = make_config_entry()

    hp = MagicMock()
    hp._hass = hass
    hp._domain = "thermiq_mqtt"
    hp._id = entry.data["id_name"]
    hp._langid = 0  # English
    hp._hexFormat = False
    hp._dbg = False

    # Build _hpstate and _id_reg like HeatPump.__init__ does
    hp._hpstate = {}
    hp._id_reg = {}
    for k, v in reg_id.items():
        hp._id_reg[v[0]] = k
        hp._hpstate[v[0]] = -1

    hp._hpstate["mqtt_counter"] = 0
    hp._hpstate["time_str"] = ""
    hp._hpstate["communication_status"] = "Ok"

    hp.send_mqtt_reg = AsyncMock()

    return hp


def make_mqtt_message(payload_dict):
    """Create a mock MQTT message."""
    msg = MagicMock()
    msg.payload = json.dumps(payload_dict)
    return msg


# A realistic MQTT payload from a ThermIQ device
SAMPLE_MQTT_PAYLOAD = {
    "Client_Name": "ThermIQ_room2",
    "d000": 5,      # r00 = outdoor_t = 5°C
    "d001": 21,     # r01 = indoor_t = 21°C (integer part)
    "d002": 3,      # r02 = indoor_dec_t = 0.3°C
    "d003": 22,     # r03 = indoor_target_t = 22°C (integer part)
    "d004": 5,      # r04 = indoor_target_dec_t = 0.5°C
    "d005": 35,     # r05 = supplyline_t
    "d006": 28,     # r06 = returnline_t
    "d007": 48,     # r07 = boiler_t
    "d008": 2,      # r08 = brine_out_t
    "d009": 5,      # r09 = brine_in_t
    "d013": 3,      # r0d = binary sensors (boiler_3kw_on bit)
    "d016": 1,      # r10 = binary sensors (brine_pump_on)
    "d050": 100,    # r32 = indoor_requested_t
    "d051": 2,      # r33 = main_mode (select_input)
    "time": "2026-03-24T12:00:00",
    "vp_read": "Ok",
}
