"""Number entities for ThermIQ writable registers.

Replaces the previous approach of injecting entities into Home Assistant's
built-in input_number platform via hass.data[CONF_ENTITY_PLATFORM] (an
unsupported internal API). These are standard NumberEntity instances in the
`number` domain and update from the shared heatpump state on the msg_rec_event.
"""
import logging

from homeassistant.components.number import NumberEntity, NumberDeviceClass, NumberMode
from homeassistant.const import (
    ATTR_IDENTIFIERS,
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    ATTR_NAME,
    UnitOfTemperature,
)
from homeassistant.helpers.device_registry import DeviceEntryType

from .const import DOMAIN, MANUFACTURER, DEVVERSION
from .heatpump.thermiq_regs import (
    FIELD_REGNUM,
    FIELD_REGTYPE,
    FIELD_UNIT,
    FIELD_MINVALUE,
    FIELD_MAXVALUE,
    id_names,
    reg_id,
)

_LOGGER = logging.getLogger(__name__)

# Register types that are exposed as writable numbers
NUMBER_TYPES = ["temperature_input", "time_input", "sensor_input", "generated_input"]


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the ThermIQ number entities for a config entry."""
    heatpump = hass.data[DOMAIN].heatpumps[config_entry.data["id_name"]]
    entities = [
        ThermIQNumber(heatpump, key)
        for key in reg_id
        if reg_id[key][FIELD_REGTYPE] in NUMBER_TYPES
    ]
    async_add_entities(entities)


class ThermIQNumber(NumberEntity):
    """A writable ThermIQ register exposed as a Number."""

    _attr_should_poll = False
    _attr_mode = NumberMode.BOX

    def __init__(self, heatpump, key):
        self._heatpump = heatpump
        self._hpstate = heatpump._hpstate
        self._key = key
        self._reg = reg_id[key][FIELD_REGNUM]

        self.entity_id = f"number.{heatpump._domain}_{heatpump._id}_{key}"
        self._attr_unique_id = "uid-" + self.entity_id
        self._attr_name = (
            id_names[key][heatpump._langid] if key in id_names else key
        )
        self._attr_native_min_value = reg_id[key][FIELD_MINVALUE]
        self._attr_native_max_value = reg_id[key][FIELD_MAXVALUE]
        self._attr_native_step = 0.1 if self._reg == "indr_t" else 1

        unit = reg_id[key][FIELD_UNIT]
        if reg_id[key][FIELD_REGTYPE] == "temperature_input" or unit in ("C", "°C"):
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_icon = "mdi:thermometer"
        else:
            self._attr_native_unit_of_measurement = unit or None
            self._attr_icon = "mdi:gauge"

        self._attr_device_info = {
            ATTR_IDENTIFIERS: {(DOMAIN, heatpump._id)},
            ATTR_NAME: "Heatpump status",
            ATTR_MANUFACTURER: MANUFACTURER,
            ATTR_MODEL: DEVVERSION,
            "entry_type": DeviceEntryType.SERVICE,
        }

    @property
    def available(self):
        """Unavailable until the first message and while the pump is silent."""
        return self._heatpump.available

    @property
    def native_value(self):
        """Current register value, or None until the first MQTT message."""
        value = self._hpstate.get(self._reg)
        if isinstance(value, (int, float)):
            return value
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the heatpump."""
        # Only send once the heatpump has reported real data
        if self._heatpump._hpstate["mqtt_counter"] <= 0:
            _LOGGER.debug("Ignoring set for %s: no data from heatpump yet", self.entity_id)
            return
        if value != self._hpstate.get(self._reg):
            self._hpstate[self._reg] = value
            # Refresh all entities of this heatpump
            self._heatpump._hass.bus.fire(
                f"{self._heatpump._domain}_{self._heatpump._id}_msg_rec_event", {}
            )
            await self._heatpump.send_mqtt_reg(self._key, value, 0xFFFF)

    async def async_added_to_hass(self):
        """Refresh state on each heatpump message; auto-removed on unload."""
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{self._heatpump._domain}_{self._heatpump._id}_msg_rec_event",
                self._async_update_event,
            )
        )

    async def _async_update_event(self, event):
        self.async_write_ha_state()
