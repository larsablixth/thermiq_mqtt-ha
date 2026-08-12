#include "state.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "json.h"
#include "util.h"
#include "widget.h"

void state_init(struct pump_state *state, double availability_timeout, int language)
{
    memset(state, 0, sizeof(*state));
    state->availability_timeout = availability_timeout;
    state->language = language;
}

bool state_available(const struct pump_state *state)
{
    if (state->last_message <= 0.0)
        return false;
    return now_monotonic() - state->last_message < state->availability_timeout;
}

double state_seconds_since_message(const struct pump_state *state)
{
    if (state->last_message <= 0.0)
        return -1.0;
    return now_monotonic() - state->last_message;
}

void state_set_connected(struct pump_state *state, bool connected, const char *error)
{
    state->connected = connected;
    if (error) {
        snprintf(state->last_error, sizeof(state->last_error), "%s", error);
    } else if (connected) {
        state->last_error[0] = '\0';
    }
}

static void store_number(struct pump_state *state, int slot, double value,
                         bool is_integer)
{
    if (slot < 0 || slot >= REG_SLOT_COUNT)
        return;
    state->values[slot].kind = is_integer ? VAL_INT : VAL_FLOAT;
    state->values[slot].number = value;
}

static void store_text(struct pump_state *state, int slot, const char *text)
{
    if (slot < 0 || slot >= REG_SLOT_COUNT)
        return;
    state->values[slot].kind = VAL_TEXT;
    snprintf(state->values[slot].text, VALUE_TEXT_MAX, "%s", text);
}

static void store_member(struct pump_state *state, int slot,
                         const struct json_member *member)
{
    switch (member->kind) {
    case JSON_NUMBER:
        store_number(state, slot, member->number, member->is_integer);
        break;
    case JSON_STRING:
        store_text(state, slot, member->string);
        break;
    case JSON_BOOL:
        store_number(state, slot, member->boolean ? 1.0 : 0.0, true);
        break;
    default:
        break;
    }
}

/* Normalise a payload key to the register name the table uses.
 *
 * The pump sends decimal keys (d050) on current firmware and hex keys (r32)
 * on the old 1.xx firmware; both are accepted. Anything that parses as
 * neither is skipped, which is what the integration does - one unreadable key
 * must not discard the rest of the message.
 *
 * Returns false when the key should be skipped. */
static bool normalise_key(const char *key, char *out, size_t cap)
{
    size_t length = strlen(key);
    if (length == 0 || length + 1 > cap)
        return false;

    /* Lowercase first: the table is lowercase, but "EVU" arrives shouting. */
    for (size_t i = 0; i < length; i++) {
        char c = key[i];
        out[i] = (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
    }
    out[length] = '\0';

    /* The original case decides which branch applies, as in the Python. */
    if (key[0] == 'd' && length < 5) {
        char *end = NULL;
        long number = strtol(key + 1, &end, 10);
        if (end == key + 1 || *end != '\0' || number < 0)
            return false;
        char hex[8];
        int written = snprintf(hex, sizeof(hex), "r%02lx", (unsigned long)number);
        /* Only a two-digit register folds into the canonical rXX form;
         * anything wider keeps the key it arrived with. */
        if (written == 3)
            snprintf(out, cap, "%s", hex);
        return true;
    }
    if (key[0] == 'r' && length == 3) {
        char *end = NULL;
        (void)strtol(key + 1, &end, 16);
        if (end != key + 3)
            return false;
    }
    return true;
}

int state_ingest(struct pump_state *state, char *payload, size_t len)
{
    static struct json_member members[JSON_MAX_MEMBERS];

    int count = json_parse_object(payload, len, members, JSON_MAX_MEMBERS);
    if (count < 0) {
        state->rejected_count++;
        log_debug("payload is not a JSON object, ignoring");
        return -1;
    }

    /* Every message identifies the sender. Anything else on the topic is
     * somebody else's traffic and must not be decoded as ours. */
    const struct json_member *client = json_get(members, count, "Client_Name");
    if (!client || client->kind != JSON_STRING ||
        strncmp(client->string, "ThermIQ_", 8) != 0) {
        state->rejected_count++;
        log_debug("payload is not from a ThermIQ device, ignoring");
        return -1;
    }

    bool received_r01 = false;
    bool received_r03 = false;
    int stored = 0;

    for (int i = 0; i < count; i++) {
        char name[32];
        if (!normalise_key(members[i].key, name, sizeof(name))) {
            log_debug("skipping unreadable key [%s]", members[i].key);
            continue;
        }
        int slot = reg_slot_of(name);
        if (slot < 0)
            continue; /* a register this table does not describe */
        store_member(state, slot, &members[i]);
        stored++;
        if (strcmp(name, "r01") == 0)
            received_r01 = true;
        else if (strcmp(name, "r03") == 0)
            received_r03 = true;
    }

    /* r01/r03 carry whole degrees and r02/r04 the tenths. Combine only when
     * the whole part arrived in this message, or the decimals would compound
     * onto an already-combined value. */
    static const char *const pairs[2][2] = {{"r01", "r02"}, {"r03", "r04"}};
    bool received[2] = {received_r01, received_r03};
    for (int i = 0; i < 2; i++) {
        if (!received[i])
            continue;
        int whole = reg_slot_of(pairs[i][0]);
        int decimal = reg_slot_of(pairs[i][1]);
        if (whole < 0 || decimal < 0)
            continue;
        if (state->values[whole].kind == VAL_NONE ||
            state->values[whole].kind == VAL_TEXT ||
            state->values[decimal].kind == VAL_NONE ||
            state->values[decimal].kind == VAL_TEXT)
            continue;
        /* int + float/10 is a float in Python, so a whole degree with no
         * tenths reads as "21.0" and not "21" - which is what the widget
         * shows. */
        state->values[whole].number += state->values[decimal].number / 10.0;
        state->values[whole].kind = VAL_FLOAT;
    }

    state->message_count++;
    state->last_message = now_monotonic();
    store_number(state, reg_slot_of("mqtt_counter"), (double)state->message_count, true);

    const struct json_member *time_value = json_get(members, count, "time");
    if (!time_value)
        time_value = json_get(members, count, "Time");
    if (time_value)
        store_member(state, reg_slot_of("time_str"), time_value);

    const struct json_member *status = json_get(members, count, "vp_read");
    if (status)
        store_member(state, reg_slot_of("communication_status"), status);
    else
        store_text(state, reg_slot_of("communication_status"), "Ok");

    const struct json_member *info = json_get(members, count, "app_info");
    if (info)
        store_member(state, reg_slot_of("app_info"), info);

    return stored;
}

const struct reg_value *state_value(const struct pump_state *state,
                                    const struct reg_def *def)
{
    if (!def || def->slot >= REG_SLOT_COUNT)
        return NULL;
    return &state->values[def->slot];
}

void state_set_number(struct pump_state *state, const struct reg_def *def, double value)
{
    /* A written value arrives as a float, exactly as it does through a Home
     * Assistant number entity, and reads back that way until the pump echoes
     * its own integer. */
    if (def)
        store_number(state, def->slot, value, false);
}

void state_set_demo_number(struct pump_state *state, const struct reg_def *def,
                           double value, bool is_integer)
{
    if (def)
        store_number(state, def->slot, value, is_integer);
}

void state_set_demo_text(struct pump_state *state, const struct reg_def *def,
                         const char *text)
{
    if (def)
        store_text(state, def->slot, text);
}

void state_format_value(const struct reg_value *value, char *out, size_t cap)
{
    switch (value->kind) {
    case VAL_INT:
        snprintf(out, cap, "%lld", (long long)value->number);
        return;
    case VAL_FLOAT: {
        char rendered[40];
        fmt_double(rendered, sizeof(rendered), value->number);
        snprintf(out, cap, "%s", rendered);
        return;
    }
    case VAL_TEXT:
        snprintf(out, cap, "%s", value->text);
        return;
    case VAL_NONE:
        break;
    }
    snprintf(out, cap, "unknown");
}

bool state_mode_value(int language, const char *option, double *value)
{
    for (int mode = 0; mode < MODE_COUNT; mode++) {
        char label[STATE_STRING_MAX];
        if (!state_mode_option(language, mode, label, sizeof(label)))
            continue;
        if (strcmp(label, option) == 0) {
            *value = mode;
            return true;
        }
    }
    return false;
}

bool state_mode_option(int language, double value, char *out, size_t cap)
{
    if (value != floor(value) || value < 0 || value >= MODE_COUNT)
        return false;
    if (language < 0 || language >= LANGUAGE_COUNT)
        language = 0;
    int mode = (int)value;
    snprintf(out, cap, "%d - %s", mode, MODE_NAMES[mode][language]);
    return true;
}

void state_entity_string(const struct pump_state *state, const char *domain,
                         const struct reg_def *def, char *out, size_t cap)
{
    if (!def) {
        snprintf(out, cap, "unknown");
        return;
    }
    if (!state_available(state)) {
        snprintf(out, cap, "unavailable");
        return;
    }

    const struct reg_value *value = state_value(state, def);
    if (!value || value->kind == VAL_NONE) {
        snprintf(out, cap, "unknown");
        return;
    }

    bool numeric = value->kind == VAL_INT || value->kind == VAL_FLOAT;
    bool bitmasked = strcmp(domain, "binary_sensor") == 0 || strcmp(domain, "switch") == 0;
    if (bitmasked) {
        if (!numeric || !def->has_bitmask) {
            snprintf(out, cap, "unknown");
            return;
        }
        uint32_t raw = (uint32_t)((int64_t)value->number & 0xFFFF);
        snprintf(out, cap, "%s", (raw & def->bitmask) ? "on" : "off");
        return;
    }

    if (strcmp(domain, "select") == 0) {
        if (!numeric || !state_mode_option(state->language, value->number, out, cap))
            snprintf(out, cap, "unknown");
        return;
    }

    state_format_value(value, out, cap);
}

void state_widget_states(const struct pump_state *state, const char **out, char *scratch,
                         size_t scratch_cap)
{
    for (int i = 0; i < WIDGET_ENTITY_COUNT; i++) {
        char *slot = scratch + (size_t)i * STATE_STRING_MAX;
        if ((size_t)(i + 1) * STATE_STRING_MAX > scratch_cap) {
            out[i] = "unknown";
            continue;
        }
        const struct widget_entity *entity = &WIDGET_ENTITIES[i];
        if (entity->is_demo_switch) {
            snprintf(slot, STATE_STRING_MAX, "%s", state->demo ? "on" : "off");
        } else if (!entity->key) {
            /* Belongs to another integration; unknown is what HA would say. */
            snprintf(slot, STATE_STRING_MAX, "unknown");
        } else {
            state_entity_string(state, entity->domain, reg_find(entity->key), slot,
                                STATE_STRING_MAX);
        }
        out[i] = slot;
    }
}

/* ---- writes ----------------------------------------------------------- */

const char *write_error_message(enum write_error error)
{
    switch (error) {
    case WRITE_OK:
        return "ok";
    case WRITE_UNKNOWN_KEY:
        return "no such register";
    case WRITE_UNKNOWN_REGISTER:
        return "register is not in the register table";
    case WRITE_NOT_WRITABLE:
        return "register is read-only";
    case WRITE_NOT_NUMERIC:
        return "value must be a number";
    case WRITE_OUT_OF_RANGE:
        return "value is outside the register's allowed range";
    case WRITE_NOT_BOOLEAN:
        return "value must be 0 or 1";
    case WRITE_UNKNOWN_OPTION:
        return "not one of the available options";
    case WRITE_NO_DATA:
        return "no data received from the heatpump yet";
    case WRITE_READ_ONLY:
        return "the bridge is running in read-only mode";
    }
    return "rejected";
}

enum write_error state_encode_write(const struct pump_state *state,
                                    const struct write_topics *topics, const char *key,
                                    double value, struct write_message *out)
{
    const struct reg_def *def = reg_find(key);
    if (!def)
        return WRITE_UNKNOWN_KEY;
    if (reg_control(def->type) == CTRL_NONE)
        return WRITE_NOT_WRITABLE;
    if (!isfinite(value))
        return WRITE_NOT_NUMERIC;
    /* The integration refuses to write before the pump has spoken, so a
     * control cannot push a value into a register it has never read. */
    if (state->message_count == 0)
        return WRITE_NO_DATA;
    if (reg_slot_of(def->reg) < 0)
        return WRITE_UNKNOWN_REGISTER;

    /* Validate the raw value, before the two's-complement conversion below:
     * a negative setpoint becomes a large positive number once masked, which
     * would sail past any range check applied afterwards. */
    if (def->type == RT_GENERATED_INPUT_BOOLEAN) {
        if (value != 0.0 && value != 1.0)
            return WRITE_NOT_BOOLEAN;
    } else if (reg_is_bounded(def->type)) {
        if (!def->has_bounds)
            return WRITE_NOT_WRITABLE;
        if (value < def->minimum || value > def->maximum)
            return WRITE_OUT_OF_RANGE;
    } else {
        return WRITE_NOT_WRITABLE;
    }

    /* The room sensor setpoint is the one register carrying a fraction. */
    bool fractional = strcmp(key, "room_sensor_set_t") == 0;
    char rendered[40];
    if (fractional) {
        fmt_double(rendered, sizeof(rendered), value);
        out->applied = value;
    } else {
        long long masked = (long long)value & 0xFFFF;
        snprintf(rendered, sizeof(rendered), "%lld", masked);
        out->applied = (double)(long long)value;
    }

    if (strcmp(def->reg, "indr_t") == 0) {
        snprintf(out->topic, sizeof(out->topic), "%s", topics->set_topic);
        snprintf(out->payload, sizeof(out->payload), "{\"INDR_T\": %s}", rendered);
        return WRITE_OK;
    }
    if (strcmp(def->reg, "evu") == 0) {
        snprintf(out->topic, sizeof(out->topic), "%s", topics->set_topic);
        snprintf(out->payload, sizeof(out->payload), "{\"EVU\": %s}", rendered);
        return WRITE_OK;
    }

    snprintf(out->topic, sizeof(out->topic), "%s", topics->write_topic);
    if (topics->hexformat) {
        snprintf(out->payload, sizeof(out->payload), "{\"%s\": %s}", def->reg, rendered);
        return WRITE_OK;
    }
    /* Decimal notation is what current firmware speaks: r32 -> d050. */
    if (def->reg[0] != 'r')
        return WRITE_UNKNOWN_REGISTER;
    char *end = NULL;
    long number = strtol(def->reg + 1, &end, 16);
    if (end == def->reg + 1 || *end != '\0')
        return WRITE_UNKNOWN_REGISTER;
    snprintf(out->payload, sizeof(out->payload), "{\"d%03ld\": %s}", number, rendered);
    return WRITE_OK;
}
