import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import (
    ATTR_IDENTIFIERS,
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    ATTR_NAME,
)
from homeassistant.helpers.device_registry import DeviceEntryType

from .const import (
    DOMAIN,
    MANUFACTURER,
    DEVVERSION,
    CONF_ID,
)

from .heatpump.thermiq_regs import (
    FIELD_BITMASK,
    FIELD_REGNUM,
    FIELD_REGTYPE,
    id_names,
    reg_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass, config_entry, async_add_entities, discovery_info=None
):
    """Set up platform for a new integration.

    Called by the HA framework after async_setup_platforms has been called
    during initialization of a new integration.
    """

    heatpump = hass.data[DOMAIN]._heatpumps[config_entry.data[CONF_ID]]
    entities = []

    for key in reg_id:
        if reg_id[key][FIELD_REGTYPE] in ("binary_sensor", "generated_input_boolean"):
            device_id = key
            if key in id_names:
                friendly_name = id_names[key][heatpump._langid]
            else:
                friendly_name = key
            vp_reg = reg_id[key][FIELD_REGNUM]
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
    """Binary sensor entity for a ThermIQ heat pump register."""

    _attr_should_poll = False

    def __init__(self, hass, heatpump, device_id, vp_reg, friendly_name, bitmask):
        self._heatpump = heatpump
        self._hpstate = heatpump._hpstate
        self.entity_id = f"binary_sensor.{heatpump._domain}_{heatpump._id}_{device_id}"
        self._attr_unique_id = "uid-" + self.entity_id
        self._attr_name = friendly_name
        self._attr_icon = "mdi:flash-outline"

        self._idx = device_id
        self._vp_reg = vp_reg
        self._bitmask = bitmask

        self._attr_device_info = {
            ATTR_IDENTIFIERS: {(DOMAIN, heatpump._id)},
            ATTR_NAME: "Heatpump status",
            ATTR_MANUFACTURER: MANUFACTURER,
            ATTR_MODEL: DEVVERSION,
            "entry_type": DeviceEntryType.SERVICE,
        }

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        reg_state = self._hpstate.get(self._vp_reg)
        if reg_state is None or reg_state == -1:
            return None
        return (int(reg_state) & self._bitmask) > 0

    async def async_added_to_hass(self) -> None:
        """Register event listener when added to hass."""
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{self._heatpump._domain}_{self._heatpump._id}_msg_rec_event",
                self._async_update_event,
            )
        )

    async def _async_update_event(self, event):
        """Update the state of the binary sensor."""
        self.async_write_ha_state()
