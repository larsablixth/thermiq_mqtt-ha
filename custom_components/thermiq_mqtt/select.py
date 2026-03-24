"""Select entities for ThermIQ heat pump mode settings."""
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .heatpump.thermiq_regs import (
    FIELD_REGNUM,
    FIELD_REGTYPE,
    id_names,
    reg_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ThermIQ select entities from a config entry."""
    worker = hass.data[DOMAIN]
    heatpump = worker.get_entry(config_entry)

    entities = []
    for key, reg_def in reg_id.items():
        if reg_def[FIELD_REGTYPE] == "select_input":
            entities.append(HeatPumpSelect(heatpump, key))

    async_add_entities(entities)


class HeatPumpSelect(SelectEntity):
    """A select entity for a ThermIQ heat pump mode register."""

    _attr_should_poll = False

    def __init__(self, heatpump, register_name: str) -> None:
        """Initialize the select entity."""
        self._heatpump = heatpump
        self._register_name = register_name
        self._reg_def = reg_id[register_name]
        self._reg = self._reg_def[FIELD_REGNUM]

        # Entity identification
        self._attr_unique_id = (
            f"{heatpump._domain}_{heatpump._id}_{register_name}"
        )
        self.entity_id = (
            f"select.{heatpump._domain}_{heatpump._id}_{register_name}"
        )

        # Friendly name
        if register_name in id_names:
            self._attr_name = id_names[register_name][heatpump._langid]
        else:
            self._attr_name = register_name

        # Build options list from mode translations
        self._attr_options = [
            f"{i} - {id_names[f'mode{i}'][heatpump._langid]}"
            for i in range(5)
        ]

        self._attr_icon = None

        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, heatpump._id)},
        }

    @property
    def current_option(self) -> str | None:
        """Return the current selected option."""
        val = self._heatpump._hpstate.get(self._reg)
        if val is None or val == -1:
            return None
        try:
            mode_idx = int(val)
            if 0 <= mode_idx < len(self._attr_options):
                return self._attr_options[mode_idx]
        except (ValueError, TypeError):
            pass
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option and send MQTT command."""
        # Only send if we've received at least one MQTT message
        if self._heatpump._hpstate.get("mqtt_counter", -1) <= 0:
            return

        # Parse the mode number from the option string (e.g. "0 - Off" -> 0)
        try:
            value = int(option.split(" - ")[0])
        except (ValueError, IndexError):
            _LOGGER.error("Could not parse mode value from option: %s", option)
            return

        current = self._heatpump._hpstate.get(self._reg)
        if current != value:
            self._heatpump._hpstate[self._reg] = value
            self._heatpump._hass.bus.fire(
                f"{self._heatpump._domain}_{self._heatpump._id}_msg_rec_event",
                {},
            )
            await self._heatpump.send_mqtt_reg(
                self._register_name, value, 0xFFFF
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
