#include "config.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "registers.h"
#include "util.h"

/* The pump publishes every ~30 s. Nothing heard for this long means the
 * values on screen are stale and must be shown as such rather than ageing
 * silently. */
#define DEFAULT_AVAILABILITY_TIMEOUT 120.0

#define FAIL(...)                                                                        \
    do {                                                                                 \
        snprintf(error, error_cap, __VA_ARGS__);                                         \
        return false;                                                                    \
    } while (0)

static const char *env(const char *name)
{
    const char *value = getenv(name);
    if (!value)
        return NULL;
    while (*value == ' ' || *value == '\t')
        value++;
    return *value ? value : NULL;
}

/* Copies with trailing whitespace removed; false if it does not fit. */
static bool copy_env(char *out, size_t cap, const char *name, const char *fallback)
{
    const char *value = env(name);
    if (!value)
        value = fallback;
    if (!value)
        value = "";
    size_t length = strlen(value);
    while (length > 0 && (value[length - 1] == ' ' || value[length - 1] == '\t'))
        length--;
    if (length + 1 > cap)
        return false;
    memcpy(out, value, length);
    out[length] = '\0';
    return true;
}

static bool parse_bool(const char *name, bool fallback, bool *out, char *error,
                       size_t error_cap)
{
    const char *value = env(name);
    if (!value) {
        *out = fallback;
        return true;
    }
    if (!strcasecmp(value, "1") || !strcasecmp(value, "true") ||
        !strcasecmp(value, "yes") || !strcasecmp(value, "on")) {
        *out = true;
        return true;
    }
    if (!strcasecmp(value, "0") || !strcasecmp(value, "false") ||
        !strcasecmp(value, "no") || !strcasecmp(value, "off")) {
        *out = false;
        return true;
    }
    FAIL("%s must be true or false, got \"%s\"", name, value);
}

static bool parse_port(const char *name, uint16_t fallback, uint16_t *out, char *error,
                       size_t error_cap)
{
    const char *value = env(name);
    if (!value) {
        *out = fallback;
        return true;
    }
    char *end = NULL;
    long parsed = strtol(value, &end, 10);
    if (end == value || *end != '\0' || parsed < 1 || parsed > 65535)
        FAIL("%s must be a port number between 1 and 65535, got \"%s\"", name, value);
    *out = (uint16_t)parsed;
    return true;
}

static void strip_trailing_slash(char *text)
{
    size_t length = strlen(text);
    while (length > 0 && text[length - 1] == '/')
        text[--length] = '\0';
}

bool config_load(struct config *config, char *error, size_t error_cap)
{
    memset(config, 0, sizeof(*config));

    if (!parse_bool("THERMIQ_DEMO", false, &config->demo, error, error_cap))
        return false;

    if (!copy_env(config->id_name, sizeof(config->id_name), "THERMIQ_ID", "vp1"))
        FAIL("THERMIQ_ID is too long");
    /* The id goes into published topics and entity ids, so keep the
     * integration's slug rule rather than inventing a looser one. */
    if (!config->id_name[0])
        FAIL("THERMIQ_ID must not be empty");
    for (const char *c = config->id_name; *c; c++) {
        bool ok = (*c >= 'a' && *c <= 'z') || (*c >= '0' && *c <= '9') || *c == '_';
        if (!ok)
            FAIL("THERMIQ_ID must be lowercase letters, digits and underscores, "
                 "got \"%s\"",
                 config->id_name);
    }

    if (!copy_env(config->language_code, sizeof(config->language_code),
                  "THERMIQ_LANGUAGE", "en"))
        FAIL("THERMIQ_LANGUAGE is too long");
    for (char *c = config->language_code; *c; c++)
        if (*c >= 'A' && *c <= 'Z')
            *c = (char)(*c - 'A' + 'a');
    config->language = language_index(config->language_code);
    if (config->language < 0)
        FAIL("THERMIQ_LANGUAGE must be one of en, se, fi, no, de, got \"%s\"",
             config->language_code);

    if (!copy_env(config->node, sizeof(config->node), "THERMIQ_NODE",
                  "ThermIQ/ThermIQ-mqtt"))
        FAIL("THERMIQ_NODE is too long");
    strip_trailing_slash(config->node);
    if (!config->node[0] || strchr(config->node, '#') || strchr(config->node, '+'))
        FAIL("THERMIQ_NODE must be a plain topic prefix without wildcards, got \"%s\"",
             config->node);

    if (!copy_env(config->mqtt_host, sizeof(config->mqtt_host), "THERMIQ_MQTT_HOST", ""))
        FAIL("THERMIQ_MQTT_HOST is too long");
    if (!config->mqtt_host[0] && !config->demo)
        FAIL("THERMIQ_MQTT_HOST is required (or set THERMIQ_DEMO=1 to run without a "
             "broker)");

    if (!copy_env(config->publish_prefix, sizeof(config->publish_prefix),
                  "THERMIQ_PUBLISH_PREFIX", ""))
        FAIL("THERMIQ_PUBLISH_PREFIX is too long");
    strip_trailing_slash(config->publish_prefix);
    if (config->publish_prefix[0]) {
        if (strchr(config->publish_prefix, '#') || strchr(config->publish_prefix, '+'))
            FAIL("THERMIQ_PUBLISH_PREFIX must not contain wildcards");
        /* Republishing under the pump's own prefix would feed our output back
         * into the topic we read from. */
        if (strcmp(config->publish_prefix, config->node) == 0)
            FAIL("THERMIQ_PUBLISH_PREFIX must differ from THERMIQ_NODE");
    }

    char default_client_id[CONFIG_STR_MAX];
    snprintf(default_client_id, sizeof(default_client_id), "thermiq-bridge-%s",
             config->id_name);
    if (!copy_env(config->mqtt_client_id, sizeof(config->mqtt_client_id),
                  "THERMIQ_MQTT_CLIENT_ID", default_client_id))
        FAIL("THERMIQ_MQTT_CLIENT_ID is too long");
    if (!copy_env(config->mqtt_username, sizeof(config->mqtt_username),
                  "THERMIQ_MQTT_USERNAME", ""))
        FAIL("THERMIQ_MQTT_USERNAME is too long");
    if (!copy_env(config->mqtt_password, sizeof(config->mqtt_password),
                  "THERMIQ_MQTT_PASSWORD", ""))
        FAIL("THERMIQ_MQTT_PASSWORD is too long");
    if (!copy_env(config->http_host, sizeof(config->http_host), "THERMIQ_HTTP_HOST",
                  "0.0.0.0"))
        FAIL("THERMIQ_HTTP_HOST is too long");

    if (!parse_port("THERMIQ_MQTT_PORT", 1883, &config->mqtt_port, error, error_cap))
        return false;
    if (!parse_port("THERMIQ_HTTP_PORT", 8080, &config->http_port, error, error_cap))
        return false;
    if (!parse_bool("THERMIQ_HEXFORMAT", false, &config->hexformat, error, error_cap))
        return false;
    if (!parse_bool("THERMIQ_DEBUG_WRITES", false, &config->debug_writes, error,
                    error_cap))
        return false;
    if (!parse_bool("THERMIQ_READ_ONLY", false, &config->read_only, error, error_cap))
        return false;

    config->availability_timeout = DEFAULT_AVAILABILITY_TIMEOUT;
    const char *timeout = env("THERMIQ_AVAILABILITY_TIMEOUT");
    if (timeout) {
        char *end = NULL;
        double value = strtod(timeout, &end);
        if (end == timeout || *end != '\0' || value < 10.0 || value > 3600.0)
            FAIL("THERMIQ_AVAILABILITY_TIMEOUT must be between 10 and 3600 seconds, "
                 "got \"%s\"",
                 timeout);
        config->availability_timeout = value;
    }

    char level[16];
    if (!copy_env(level, sizeof(level), "THERMIQ_LOG_LEVEL", "info"))
        FAIL("THERMIQ_LOG_LEVEL is too long");
    if (!strcasecmp(level, "debug"))
        config->log_level = LOG_DEBUG;
    else if (!strcasecmp(level, "info"))
        config->log_level = LOG_INFO;
    else if (!strcasecmp(level, "warn") || !strcasecmp(level, "warning"))
        config->log_level = LOG_WARN;
    else if (!strcasecmp(level, "error"))
        config->log_level = LOG_ERROR;
    else
        FAIL("THERMIQ_LOG_LEVEL must be debug, info, warn or error, got \"%s\"", level);

    snprintf(config->data_topic, sizeof(config->data_topic), "%s/data", config->node);
    /* Debug mode diverts writes to dbg_*, so a real pump is never touched. */
    snprintf(config->write_topic, sizeof(config->write_topic), "%s/%s", config->node,
             config->debug_writes ? "dbg_write" : "write");
    snprintf(config->set_topic, sizeof(config->set_topic), "%s/%s", config->node,
             config->debug_writes ? "dbg_set" : "set");
    return true;
}

void config_log_summary(const struct config *config)
{
    log_info("id=%s language=%s node=%s", config->id_name, config->language_code,
             config->node);
    if (config->demo)
        log_warn("demo mode: showing canned values, not connecting to a broker");
    else
        log_info("mqtt broker %s:%u, subscribing to %s", config->mqtt_host,
                 config->mqtt_port, config->data_topic);
    log_info("http listening on %s:%u", config->http_host, config->http_port);
    if (config->read_only)
        log_info("read-only: every write will be refused");
    if (config->debug_writes)
        log_warn("debug writes: writes go to %s, the heatpump is not touched",
                 config->write_topic);
    if (config->hexformat)
        log_info("hex register format: writes use rXX keys (old 1.xx firmware)");
    if (config->publish_prefix[0])
        log_info("republishing decoded values under %s/", config->publish_prefix);
}
