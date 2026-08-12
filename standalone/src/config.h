/* Configuration, read once from the environment at startup.
 *
 * The Home Assistant integration asks these questions in a config flow; a
 * container has no dialog, so they are environment variables. Everything is
 * validated here and nowhere else: a process that gets past startup holds a
 * configuration that cannot fail later.
 */
#ifndef THERMIQ_CONFIG_H
#define THERMIQ_CONFIG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define CONFIG_STR_MAX 192
#define CONFIG_TOPIC_MAX 224

struct config {
    char id_name[64];
    char language_code[8];
    int language;
    char node[CONFIG_STR_MAX];

    char mqtt_host[CONFIG_STR_MAX];
    uint16_t mqtt_port;
    char mqtt_username[CONFIG_STR_MAX];
    char mqtt_password[CONFIG_STR_MAX];
    char mqtt_client_id[CONFIG_STR_MAX];
    bool hexformat;
    bool debug_writes;
    char publish_prefix[CONFIG_STR_MAX];

    char http_host[CONFIG_STR_MAX];
    uint16_t http_port;

    bool read_only;
    bool demo;
    double availability_timeout;
    int log_level;

    /* Derived once, so nothing formats topics in the hot path. */
    char data_topic[CONFIG_TOPIC_MAX];
    char write_topic[CONFIG_TOPIC_MAX];
    char set_topic[CONFIG_TOPIC_MAX];
};

/* Fills `config` from the environment. On failure returns false and writes a
 * one-line explanation into `error`. */
bool config_load(struct config *config, char *error, size_t error_cap);
void config_log_summary(const struct config *config);

#endif /* THERMIQ_CONFIG_H */
