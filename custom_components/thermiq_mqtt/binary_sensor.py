from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)


from .const import (
    DOMAIN,
    CONF_ID,
)


from .heatpump import HeatPump
from .heatpump.thermiq_regs import (
    FIELD_BITMASK,
    FIELD_MAXVALUE,
    FIELD_MINVALUE,
    FIELD_REGNUM,
    FIELD_REGTYPE,
    FIELD_UNIT,
    id_names,
    reg_id,
)


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    discovery_info=None,
) -> None:
    """Set up platform for a new integration.
    Called by the HA framework after async_setup_platforms has been called
    during initialization of a new integration.
    """

    @callback
    def async_add_sensor(sensor):
        """Add a ThermIQ sensor property"""
        async_add_entities([sensor], True)
        # _LOGGER.debug('Added new sensor %s / %s', sensor.entity_id, sensor.unique_id)

    worker = hass.data[DOMAIN].worker
    heatpump = hass.data[DOMAIN]._heatpumps[config_entry.data[CONF_ID]]
    entities = []

    for key in reg_id:
        if reg_id[key][1] in [
            "binary_sensor",
        ]:
            device_id = key
            if key in id_names:
                friendly_name = id_names[key][heatpump._langid]
            else:
                friendly_name = key
            vp_reg = reg_id[key][FIELD_REGNUM]
            vp_type = reg_id[key][FIELD_REGTYPE]
            bitmask = reg_id[key][FIELD_BITMASK]

            entities.append(
                HeatPumpBinarySensor(
                    hass,
                    heatpump,
                    device_id,
                    vp_reg,
                    friendly_name,
                    bitmask,
                )
            )
    async_add_entities(entities)


class HeatPumpBinarySensor(BinarySensorEntity):
    """Common functionality for all entities."""

    # Use the entity name as-is; never prefix it with the device name
    _attr_has_entity_name = False

    def __init__(
        self,
        hass: HomeAssistant,
        heatpump: HeatPump,
        device_id: str,
        vp_reg: str,
        friendly_name: str,
        bitmask: int,
    ) -> None:
        self.hass = hass
        self._heatpump = heatpump
        self._hpstate = heatpump._hpstate

        # set HA instance attributes directly (mostly don't use property)
        # self._attr_unique_id
        self.entity_id = f"binary_sensor.{heatpump._domain}_{heatpump._id}_{device_id}"
        self._attr_unique_id = "uid-" + self.entity_id

        _LOGGER.debug("entity_id:" + self.entity_id)
        _LOGGER.debug("idx:" + device_id)
        self._name = friendly_name
        self._state: bool | None = None
        self._attr_is_on = False
        self._icon = "mdi:flash-outline"

        self._entity_picture = None
        self._available = True

        self._idx = device_id
        self._vp_reg = vp_reg
        self._bitmask = bitmask
        # Sort key: register number (hex string like 'r10') then bitmask
        try:
            self._sorter = int(vp_reg[1:], 16) * 65536 + int(bitmask)
        except (TypeError, ValueError):
            self._sorter = 256 * 65536

    async def async_added_to_hass(self) -> None:
        """Register the update listener; removed automatically on unload."""
        self.async_on_remove(
            self.hass.bus.async_listen(
                self._heatpump._domain + "_" + self._heatpump._id + "_msg_rec_event",
                self._async_update_event,
            )
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def should_poll(self) -> bool:
        """No need to poll. Coordinator notifies entity of updates."""
        return False

    @property
    def available(self) -> bool:
        """Unavailable until the first message and while the pump is silent."""
        return self._heatpump.available

    @property
    def vp_reg(self) -> str:
        """Return the register of the sensor."""
        return self._vp_reg

    @property
    def sorter(self) -> int:
        """Return the sort key of the sensor."""
        return self._sorter

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return self._icon

    async def async_update(self) -> None:
        """Update the new state of the sensor."""

        _LOGGER.debug("update: " + self._idx)
        reg_state = self._hpstate.get(self._vp_reg)
        if reg_state is None:
            _LOGGER.warning("Could not get data for %s", self._idx)
        else:
            self._state = (int(reg_state) & self._bitmask) > 0
            self._attr_is_on = self._state

    async def _async_update_event(self, event: Event) -> None:
        """Update the new state of the sensor."""

        _LOGGER.debug("event: " + self._idx)
        if self._vp_reg == "evu":
            _LOGGER.debug("EVU reg state read special")
        reg_state = self._hpstate.get(self._vp_reg)
        if reg_state is None:
            _LOGGER.debug("Could not get data for %s", self._idx)
            bool_state = None
        else:
            try:
                bool_state = (int(reg_state) & self._bitmask) > 0
            except (TypeError, ValueError):
                _LOGGER.debug("Non-numeric data for %s: [%s]", self._idx, reg_state)
                bool_state = None

        if self._state != bool_state:
            self._state = bool_state
            self._attr_is_on = bool(bool_state)
            self.async_write_ha_state()
            _LOGGER.debug("async_update_ha: %s: [%s]", self._idx, str(bool_state))
