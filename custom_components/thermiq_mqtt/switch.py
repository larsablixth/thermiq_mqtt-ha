"""Switch entities for ThermIQ heat pump boolean settings."""
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .heatpump.thermiq_regs import (
    FIELD_REGNUM,
    FIELD_REGTYPE,
    FIELD_BITMASK,
    id_names,
    reg_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ThermIQ switch entities from a config entry."""
    worker = hass.data[DOMAIN]
    heatpump = worker.get_entry(config_entry)

    entities = []
    for key, reg_def in reg_id.items():
        if reg_def[FIELD_REGTYPE] == "generated_input_boolean":
            entities.append(HeatPumpSwitch(heatpump, key))

    async_add_entities(entities)


class HeatPumpSwitch(SwitchEntity):
    """A switch entity for a ThermIQ heat pump boolean register."""

    _attr_should_poll = False

    def __init__(self, heatpump, register_name: str) -> None:
        """Initialize the switch entity."""
        self._heatpump = heatpump
        self._register_name = register_name
        self._reg_def = reg_id[register_name]
        self._reg = self._reg_def[FIELD_REGNUM]
        self._bitmask = self._reg_def[FIELD_BITMASK]

        # Entity identification
        self._attr_unique_id = (
            f"{heatpump._domain}_{heatpump._id}_{register_name}"
        )
        self.entity_id = (
            f"switch.{heatpump._domain}_{heatpump._id}_{register_name}"
        )

        # Friendly name
        if register_name in id_names:
            self._attr_name = id_names[register_name][heatpump._langid]
        else:
            self._attr_name = register_name

        self._attr_icon = "mdi:gauge"

        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, heatpump._id)},
        }

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        val = self._heatpump._hpstate.get(self._reg)
        if val is None or val == -1:
            return None
        return bool(int(val) & self._bitmask)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._async_set_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._async_set_value(False)

    async def _async_set_value(self, value: bool) -> None:
        """Set the value and send MQTT command."""
        reg_value = 1 if value else 0

        # Only send if we've received at least one MQTT message
        if self._heatpump._hpstate.get("mqtt_counter", -1) <= 0:
            return

        current = self._heatpump._hpstate.get(self._reg)
        if current != reg_value:
            self._heatpump._hpstate[self._reg] = reg_value
            self._heatpump._hass.bus.fire(
                f"{self._heatpump._domain}_{self._heatpump._id}_msg_rec_event",
                {},
            )
            await self._heatpump.send_mqtt_reg(
                self._register_name, reg_value, 0xFFFF
            )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register event listener when added to hass."""
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{self._heatpump._domain}_{self._heatpump._id}_msg_rec_event",
                self._async_update_event,
            )
        )

    async def _async_update_event(self, event) -> None:
        """Handle heat pump state update event."""
        self.async_write_ha_state()
