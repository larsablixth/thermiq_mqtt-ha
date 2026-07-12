"""Select entities for ThermIQ mode registers.

Replaces injection into Home Assistant's built-in input_select platform.
Standard SelectEntity instances in the `select` domain.
"""
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.const import (
    ATTR_IDENTIFIERS,
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    ATTR_NAME,
)
from homeassistant.helpers.device_registry import DeviceEntryType

from .const import DOMAIN, MANUFACTURER, DEVVERSION
from .heatpump.thermiq_regs import (
    FIELD_REGNUM,
    FIELD_REGTYPE,
    id_names,
    reg_id,
)

_LOGGER = logging.getLogger(__name__)

# Named modes available in the translation table (mode0..mode4)
MODE_VALUES = [0, 1, 2, 3, 4]


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the ThermIQ select entities for a config entry."""
    heatpump = hass.data[DOMAIN].heatpumps[config_entry.data["id_name"]]
    entities = [
        ThermIQSelect(heatpump, key)
        for key in reg_id
        if reg_id[key][FIELD_REGTYPE] == "select_input"
    ]
    async_add_entities(entities)


class ThermIQSelect(SelectEntity):
    """A ThermIQ mode register exposed as a Select."""

    _attr_should_poll = False

    def __init__(self, heatpump, key):
        self._heatpump = heatpump
        self._hpstate = heatpump._hpstate
        self._key = key
        self._reg = reg_id[key][FIELD_REGNUM]

        self.entity_id = f"select.{heatpump._domain}_{heatpump._id}_{key}"
        self._attr_unique_id = "uid-" + self.entity_id
        self._attr_name = (
            id_names[key][heatpump._langid] if key in id_names else key
        )
        # Map option label -> numeric value, e.g. "2 - Heatpump only" -> 2
        self._value_by_option = {
            f"{v} - {id_names[f'mode{v}'][heatpump._langid]}": v
            for v in MODE_VALUES
            if f"mode{v}" in id_names
        }
        self._option_by_value = {v: o for o, v in self._value_by_option.items()}
        self._attr_options = list(self._value_by_option.keys())

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
    def current_option(self):
        """Return the current option, or None if unknown/unmapped."""
        value = self._hpstate.get(self._reg)
        try:
            return self._option_by_value.get(int(value))
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        """Write the selected mode to the heatpump."""
        if option not in self._value_by_option:
            _LOGGER.warning("Unknown option %s for %s", option, self.entity_id)
            return
        if self._heatpump._hpstate["mqtt_counter"] <= 0:
            _LOGGER.debug("Ignoring select for %s: no data yet", self.entity_id)
            return
        value = self._value_by_option[option]
        if value != self._hpstate.get(self._reg):
            self._hpstate[self._reg] = value
            self._heatpump._hass.bus.fire(
                f"{self._heatpump._domain}_{self._heatpump._id}_msg_rec_event", {}
            )
            await self._heatpump.send_mqtt_reg(self._key, value, 0xFFFF)

    async def async_added_to_hass(self):
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{self._heatpump._domain}_{self._heatpump._id}_msg_rec_event",
                self._async_update_event,
            )
        )

    async def _async_update_event(self, event):
        self.async_write_ha_state()
