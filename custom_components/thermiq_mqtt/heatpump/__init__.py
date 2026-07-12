import logging, json

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.components import mqtt

from ..const import (
    DOMAIN,
    CONF_ID,
    CONF_MQTT_NODE,
    CONF_MQTT_HEX,
    CONF_MQTT_DBG,
    CONF_LANGUAGE,
    AVAILABLE_LANGUAGES,
)

# import ThermIQ register defines
from .thermiq_regs import reg_id

_LOGGER = logging.getLogger(__name__)


class HeatPump:

    _dbg = True
    _mqtt_base = ""
    _hexFormat = False
    _langid = 0

    unsubscribe_callback: Callable[[], None]

    # ###
    async def message_received(self, message):
        """Handle new MQTT messages."""
        _LOGGER.debug("%s: message.payload:[%s]", self._id, message.payload)
        try:
            json_dict = json.loads(message.payload)
        except ValueError:
            _LOGGER.error("MQTT payload could not be parsed as JSON")
            _LOGGER.debug("Erroneous JSON: %s", message.payload)
            return

        if not isinstance(json_dict, dict) or str(json_dict.get("Client_Name", ""))[:8] != "ThermIQ_":
            _LOGGER.error("JSON result was not from ThermIQ-mqtt")
            return

        # Track which registers arrived in this message
        received = set()

        for k in json_dict.keys():
            # A malformed key must not abort processing of the remaining keys
            try:
                kstore = k.lower()
                dstore = k
                if kstore == "evu":
                    dstore = 'd300'
                # # Create hex notation if incoming register is decimal format
                # Named registers must be longer than 4 characters to avoid confusion
                if k[0] == "d" and len(k) < 5:
                    reg = int(k[1:])
                    kstore = "r" + format(reg, "02x")
                    dstore = "d" + format(reg, "03d")
                    if len(kstore) != 3:
                        kstore = k
                # Create decimal notation if incoming register is hex format
                if k[0] == "r" and len(k) == 3:
                    reg = int(k[1:], 16)
                    dstore = "d" + format(reg, "03d")

                _LOGGER.debug(
                    "[%s] [%s] [%s] [%s]", self._id, kstore, json_dict[k], dstore
                )

                # Store the value under its canonical register key. The
                # number/select/switch/sensor/binary_sensor entities read from
                # this shared state when the msg_rec_event fires below, so the
                # incoming heatpump values always win over the UI.
                self._hpstate[kstore] = json_dict[k]
                received.add(kstore)
            except (ValueError, KeyError, IndexError, TypeError) as err:
                _LOGGER.warning(
                    "Could not process key [%s] in MQTT message: %s", k, err
                )

        # Do some post processing of data received
        # r01/r03 are combined with their decimal parts r02/r04, but only when
        # they arrived in this message - otherwise the decimal part would
        # compound onto an already combined value
        r01 = self._hpstate.get("r01")
        r02 = self._hpstate.get("r02")
        if "r01" in received and isinstance(r01, (int, float)) and isinstance(r02, (int, float)):
            self._hpstate["r01"] = r01 + r02 / 10

        r03 = self._hpstate.get("r03")
        r04 = self._hpstate.get("r04")
        if "r03" in received and isinstance(r03, (int, float)) and isinstance(r04, (int, float)):
            self._hpstate["r03"] = r03 + r04 / 10

        self._hpstate["mqtt_counter"] += 1

        if "time" in json_dict:
            self._hpstate["time_str"] = json_dict["time"]
        elif "Time" in json_dict:
            self._hpstate["time_str"] = json_dict["Time"]

        if "vp_read" in json_dict:
            self._hpstate["communication_status"] = json_dict["vp_read"]
        else:
            self._hpstate["communication_status"] = "Ok"

        if "app_info" in json_dict:
            self._hpstate["app_info"] = json_dict["app_info"]

        self._hass.bus.fire(
            self._domain + "_" + self._id + "_msg_rec_event", {}
        )

    def __init__(self, hass, entry: ConfigEntry):
        self._hass = hass
        self._entry = entry
        self._hpstate = {}
        self._domain = DOMAIN
        self._id = entry.data[CONF_ID]
        self._id_reg = {}
        self.unsubscribe_callback = None

        # Create reverse lookup dictionary (id_reg->reg_number)
        # Registers start as None (unknown) until the first MQTT message;
        # -1 would make every bitmask test true and light up all alarms
        for k, v in reg_id.items():
            self._id_reg[v[0]] = k
            self._hpstate[v[0]] = None

    async def setup_mqtt(self):
        self._hpstate["time_str"] = self._data_topic
        self.unsubscribe_callback = await mqtt.async_subscribe(
            self._hass,
            self._data_topic,
            self.message_received,
        )

    async def update_config(self, entry):
        if self.unsubscribe_callback is not None:
            self.unsubscribe_callback()
        lang = entry.data[CONF_LANGUAGE]
        self._langid = AVAILABLE_LANGUAGES.index(lang)
        self._dbg = entry.data[CONF_MQTT_DBG]
        self._mqtt_base = entry.data[CONF_MQTT_NODE] + "/"
        self._hexFormat = entry.data[CONF_MQTT_HEX]
        self._data_topic = self._mqtt_base + "data"
        if self._dbg is True:
            # Debug mode diverts all writes to dbg_ topics so that a real
            # heatpump is never affected. The topics must be derived AFTER
            # applying the prefix, otherwise debug writes would still reach
            # the real heatpump
            self._cmd_topic = self._mqtt_base + "dbg_write"
            self._set_topic = self._mqtt_base + "dbg_set"
            _LOGGER.warning(
                "MQTT Debug write enabled, writes are diverted to [%s]",
                self._cmd_topic,
            )
        else:
            self._cmd_topic = self._mqtt_base + "write"
            self._set_topic = self._mqtt_base + "set"
        self._hpstate["mqtt_counter"] = 0

        # Provide some debug info
        _LOGGER.debug(
            f"INFO: {self._domain}_{self._id} mqtt_node: [{entry.data[CONF_MQTT_NODE]}]"
        )

        _LOGGER.debug("Language[%s]", self._langid)

        if self._hexFormat == True:
            _LOGGER.debug("INFO: Using HEX format")





    async def async_reset(self):
        """Reset this heatpump: unsubscribe from MQTT.

        The number/select/switch/sensor/binary_sensor entities are removed by
        async_unload_platforms during config entry unload.
        """
        if self.unsubscribe_callback is not None:
            self.unsubscribe_callback()
            self.unsubscribe_callback = None
        return True

    @property
    def hpstate(self):
        return self._hpstate

    def set_value(self, item, value):
        """Set value for sensor."""
        _LOGGER.debug("set_value(" + item + ")=" + str(value))
        self._hpstate[item] = value

    def get_value(self, item):
        """Get value for sensor."""
        res = self._hpstate.get(item)
        _LOGGER.debug("get_value(%s)=%s", item, res)
        return res

    def update_state(self, command, state_command):
        """Send MQTT message to ThermIQ."""
        _LOGGER.debug("update_state:" + command + " " + state_command)
        # self._data[state_command] = self._client.command(command)
        # hass.async_create_task(
        #     mqtt.async_publish(
        #         self._hass, conf.cmd_topic, self._data[state_command]
        #     )
        # )

    # ### ##################################################################
    # Write a specific value_id with data; value_id is translated to a register
    # number. Used by the number/select/switch entities to write to the pump.

    async def send_mqtt_reg(self, register_id, value, bitmask) -> None:
        """Service to send a message."""

        register = reg_id[register_id][0]
        _LOGGER.debug("register:[%s]", register)

        if not (isinstance(value, int) or isinstance(value, float)) or value is None:
            _LOGGER.error("No MQTT message sent due to missing value:[%s]", value)
            return

        if bitmask is None:
            bitmask = 0xFFFF

        ## check the bitmask
        # value = value | bitmask
        if register_id == "room_sensor_set_t":
            value = float(value)
        else:
            value = int(value) & int(bitmask)

        if not (register in self._id_reg):
            _LOGGER.error("No MQTT message sent due to unknown register:[%s]", register)
            return

        # Lets use the decimal register notation in the MQTT message towards ThermIQ-MQTT to improve human readability

        if register == "indr_t":
            topic = self._set_topic
            payload = json.dumps({"INDR_T": value})
        elif register == "evu":
            topic = self._set_topic
            payload = json.dumps({"EVU": value})
        elif self._hexFormat:
            # dreg = "d" + format(int(register[1:], 16), "03d")
            topic = self._cmd_topic
            payload = json.dumps({register: value})
        else:
            dreg = "d" + format(int(register[1:], 16), "03d")
            topic = self._cmd_topic
            payload = json.dumps({dreg: value})

        _LOGGER.debug("topic:[%s]", topic)
        _LOGGER.debug("payload:[%s]", payload)
        self._hass.async_create_task(
            mqtt.async_publish(
                self._hass, topic, payload, qos=2, retain=False
            )
        )
