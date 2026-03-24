"""Constants for the component."""

# Component domain, used to store component data in hass data.
DOMAIN = "thermiq_mqtt"
MANUFACTURER = "ThermIQ.net"
DEVVERSION ="1.1"
DATABASE_VERSION = 1.5


# Database version, used to migrate old versions of data in the recorded history.

CONF_DB_VERSION =  "database_version"
CONF_MIGRATE_DATA = "migrate_data"

# == ThermIQ Const
CONF_ID = "id_name"
CONF_MQTT_NODE = "mqtt_node"
CONF_MQTT_DBG = "thermiq_dbg"
CONF_MQTT_HEX = "hexformat"
CONF_LANGUAGE = "language"

DEFAULT_NODE = "ThermIQ/ThermIQ-mqtt"
CONF_DATA = "data_msg"
DEFAULT_DATA = "/data"
CONF_CMD = "cmd_msg"
DEFAULT_CMD = "/write"
DEFAULT_DBG = False
AVAILABLE_LANGUAGES = ["en", "se", "fi", "no", "de"]


