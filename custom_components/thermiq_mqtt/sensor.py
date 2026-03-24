import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_IDENTIFIERS,
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    ATTR_NAME,
    UnitOfTemperature,
    UnitOfElectricCurrent,
    UnitOfTime,
)
from homeassistant.helpers.device_registry import DeviceEntryType

from .const import (
    DOMAIN,
    MANUFACTURER,
    DEVVERSION,
    CONF_ID,
)

from .heatpump.thermiq_regs import (
    FIELD_REGNUM,
    FIELD_REGTYPE,
    FIELD_UNIT,
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
        if reg_id[key][FIELD_REGTYPE] in [
            "temperature",
            "temperature_input",
            "time_input",
            "sensor",
            "sensor_input",
            "generated_input",
            "time",
            "select_input",
            "sensor_language",
            "sensor_boolean",
            "generated_sensor",
        ]:
            device_id = key
            if key in id_names:
                friendly_name = id_names[key][heatpump._langid]
            else:
                friendly_name = key
            vp_reg = reg_id[key][FIELD_REGNUM]
            vp_type = reg_id[key][FIELD_REGTYPE]
            vp_unit = reg_id[key][FIELD_UNIT]

            entities.append(
                HeatPumpSensor(
                    hass,
                    heatpump,
                    device_id,
                    vp_reg,
                    friendly_name,
                    vp_type,
                    vp_unit,
                )
            )
    async_add_entities(entities)


class HeatPumpSensor(SensorEntity):
    """Sensor entity for a ThermIQ heat pump register."""

    _attr_should_poll = False

    def __init__(
        self, hass, heatpump, device_id, vp_reg, friendly_name, vp_type, vp_unit
    ):
        self._heatpump = heatpump
        self._hpstate = heatpump._hpstate
        self.entity_id = f"sensor.{heatpump._domain}_{heatpump._id}_{device_id}"
        self._attr_unique_id = "uid-" + self.entity_id
        self._attr_name = friendly_name
        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._idx = device_id
        self._vp_reg = vp_reg

        # Set device class, unit, and icon based on register type
        if vp_type in ("temperature_input", "temperature") or vp_unit in ("C", "°C"):
            self._attr_icon = "mdi:temperature-celsius"
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_device_class = SensorDeviceClass.TEMPERATURE

        elif vp_type == "time" and vp_unit == "h":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_icon = "mdi:clock-star-four-points-outline"
            self._attr_native_unit_of_measurement = UnitOfTime.HOURS

        elif vp_type == "sensor" and vp_unit == "A":
            self._attr_device_class = SensorDeviceClass.CURRENT
            self._attr_icon = "mdi:flash"
            self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

        elif vp_unit == "dBm":
            self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
            self._attr_icon = "mdi:wifi"
            self._attr_native_unit_of_measurement = "dBm"

        elif vp_type == "sensor_boolean":
            self._attr_native_unit_of_measurement = ""
            self._attr_icon = "mdi:alert"
        else:
            self._attr_native_unit_of_measurement = vp_unit
            self._attr_icon = "mdi:gauge"

        self._attr_device_info = {
            ATTR_IDENTIFIERS: {(DOMAIN, heatpump._id)},
            ATTR_NAME: "Heatpump status",
            ATTR_MANUFACTURER: MANUFACTURER,
            ATTR_MODEL: DEVVERSION,
            "entry_type": DeviceEntryType.SERVICE,
        }

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._hpstate.get(self._vp_reg)

    async def async_added_to_hass(self) -> None:
        """Register event listener when added to hass."""
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{self._heatpump._domain}_{self._heatpump._id}_msg_rec_event",
                self._async_update_event,
            )
        )

    async def _async_update_event(self, event):
        """Update the new state of the sensor."""
        self.async_write_ha_state()
