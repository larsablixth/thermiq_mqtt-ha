/* Pump state: what arrived on MQTT, and what may be written back.
 *
 * A faithful port of HeatPump.message_received and HeatPump.send_mqtt_reg in
 * the Home Assistant integration. Where the two could differ they must not,
 * so the quirks are reproduced deliberately: register keys are normalised the
 * same way, the split decimal registers are recombined the same way, and a
 * write is refused under exactly the same conditions.
 *
 * One instance, owned by the event loop, never shared across threads.
 */
#ifndef THERMIQ_STATE_H
#define THERMIQ_STATE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "registers.h"

/* Longest informational string kept (time, vp_read, app_info). */
#define VALUE_TEXT_MAX 64
/* A Home Assistant state string: "-12.3", "unavailable", "2 - Heatpump only". */
#define STATE_STRING_MAX 64

/* INT and FLOAT are both doubles; they differ only in how they render, which
 * is exactly how Python's int and float differ to a Home Assistant state. */
enum value_kind { VAL_NONE = 0, VAL_INT, VAL_FLOAT, VAL_TEXT };

struct reg_value {
    enum value_kind kind;
    double number;
    char text[VALUE_TEXT_MAX];
};

struct pump_state {
    struct reg_value values[REG_SLOT_MAX];
    double last_message;   /* monotonic seconds; 0 means nothing yet */
    double availability_timeout;
    uint64_t message_count;
    uint64_t rejected_count;
    int language;
    bool connected;
    bool demo;
    char last_error[160];
};

void state_init(struct pump_state *state, double availability_timeout, int language);

/* True while messages are arriving. Everything reads as unavailable when not. */
bool state_available(const struct pump_state *state);
double state_seconds_since_message(const struct pump_state *state);

void state_set_connected(struct pump_state *state, bool connected, const char *error);

/* Decode one <node>/data payload. `payload` is mutated in place and must have
 * one spare byte for a terminator. Returns the number of registers stored, or
 * -1 if the payload is not a ThermIQ message. */
int state_ingest(struct pump_state *state, char *payload, size_t len);

const struct reg_value *state_value(const struct pump_state *state,
                                    const struct reg_def *def);
void state_set_number(struct pump_state *state, const struct reg_def *def, double value);
/* Seed a value with an explicit int/float distinction. Only demo mode needs
 * this; real values carry the distinction from the payload. */
void state_set_demo_number(struct pump_state *state, const struct reg_def *def,
                           double value, bool is_integer);
/* Seed a text value. Only demo mode needs this; real text arrives decoded. */
void state_set_demo_text(struct pump_state *state, const struct reg_def *def,
                         const char *text);
/* Render a stored value the way Home Assistant renders an entity state. */
void state_format_value(const struct reg_value *value, char *out, size_t cap);

/* The Home Assistant state string for a register in a given domain, which is
 * what the widget template is written against. */
void state_entity_string(const struct pump_state *state, const char *domain,
                         const struct reg_def *def, char *out, size_t cap);

/* Fill the widget's entity-state array. `scratch` backs the strings and must
 * hold WIDGET_ENTITY_COUNT * STATE_STRING_MAX bytes. */
void state_widget_states(const struct pump_state *state, const char **out, char *scratch,
                         size_t scratch_cap);

/* ---- writes ----------------------------------------------------------- */

enum write_error {
    WRITE_OK = 0,
    WRITE_UNKNOWN_KEY,
    WRITE_UNKNOWN_REGISTER,
    WRITE_NOT_WRITABLE,
    WRITE_NOT_NUMERIC,
    WRITE_OUT_OF_RANGE,
    WRITE_NOT_BOOLEAN,
    WRITE_UNKNOWN_OPTION,
    WRITE_NO_DATA,
    WRITE_READ_ONLY,
};

const char *write_error_message(enum write_error error);

struct write_topics {
    const char *write_topic;
    const char *set_topic;
    bool hexformat;
};

struct write_message {
    char topic[192];
    char payload[96];
    double applied; /* what the local state should become */
};

/* Validate a numeric write and build the message for it. The pump does not
 * range-check what it is told, so this is the only thing between a typo and a
 * register the pump will happily accept. */
enum write_error state_encode_write(const struct pump_state *state,
                                    const struct write_topics *topics, const char *key,
                                    double value, struct write_message *out);

/* Resolve a select option label ("2 - Heatpump only") to its numeric value. */
bool state_mode_value(int language, const char *option, double *value);
/* Render the option label for a numeric mode. */
bool state_mode_option(int language, double value, char *out, size_t cap);

#endif /* THERMIQ_STATE_H */
