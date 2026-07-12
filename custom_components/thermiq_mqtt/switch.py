"""Switch entities for ThermIQ boolean control registers.

Replaces injection into Home Assistant's built-in input_boolean platform.
Standard SwitchEntity instances in the `switch` domain (e.g. EVU block).
"""
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    FIELD_BITMASK,
    id_names,
    reg_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the ThermIQ switch entities for a config entry."""
    heatpump = hass.data[DOMAIN].heatpumps[config_entry.data["id_name"]]
    entities = [
        ThermIQSwitch(heatpump, key)
        for key in reg_id
        if reg_id[key][FIELD_REGTYPE] == "generated_input_boolean"
    ]
    async_add_entities(entities)


class ThermIQSwitch(SwitchEntity):
    """A ThermIQ boolean control register exposed as a Switch."""

    _attr_should_poll = False
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, heatpump, key):
        self._heatpump = heatpump
        self._hpstate = heatpump._hpstate
        self._key = key
        self._reg = reg_id[key][FIELD_REGNUM]
        self._bitmask = reg_id[key][FIELD_BITMASK]

        self.entity_id = f"switch.{heatpump._domain}_{heatpump._id}_{key}"
        self._attr_unique_id = "uid-" + self.entity_id
        self._attr_name = (
            id_names[key][heatpump._langid] if key in id_names else key
        )

        self._attr_device_info = {
            ATTR_IDENTIFIERS: {(DOMAIN, heatpump._id)},
            ATTR_NAME: "Heatpump status",
            ATTR_MANUFACTURER: MANUFACTURER,
            ATTR_MODEL: DEVVERSION,
            "entry_type": DeviceEntryType.SERVICE,
        }

    @property
    def is_on(self):
        """Return True/False, or None until the first MQTT message."""
        value = self._hpstate.get(self._reg)
        try:
            return (int(value) & int(self._bitmask)) > 0
        except (TypeError, ValueError):
            return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(False)

    async def _async_write(self, turn_on: bool) -> None:
        if self._heatpump._hpstate["mqtt_counter"] <= 0:
            _LOGGER.debug("Ignoring switch for %s: no data yet", self.entity_id)
            return
        value = 1 if turn_on else 0
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
