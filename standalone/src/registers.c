/* Classification derived from the generated register table.
 *
 * These rules are the integration's, restated: sensor.py decides what carries
 * a unit and a device class, number/select/switch.py decide what is writable
 * and as what. tests/test_registers.c pins the counts so a change to the
 * shared table cannot quietly reclassify anything.
 */
#include "registers.h"

#include <string.h>

bool reg_is_sensor(enum reg_type type)
{
    return type != RT_BINARY_SENSOR && type != RT_GENERATED_INPUT_BOOLEAN;
}

enum control reg_control(enum reg_type type)
{
    switch (type) {
    case RT_TEMPERATURE_INPUT:
    case RT_TIME_INPUT:
    case RT_SENSOR_INPUT:
    case RT_GENERATED_INPUT:
        return CTRL_NUMBER;
    case RT_SELECT_INPUT:
        return CTRL_SELECT;
    case RT_GENERATED_INPUT_BOOLEAN:
        return CTRL_SWITCH;
    default:
        return CTRL_NONE;
    }
}

bool reg_is_bounded(enum reg_type type)
{
    switch (type) {
    case RT_TEMPERATURE_INPUT:
    case RT_TIME_INPUT:
    case RT_SENSOR_INPUT:
    case RT_GENERATED_INPUT:
    case RT_SELECT_INPUT:
        return true;
    default:
        return false;
    }
}

void reg_presentation(const struct reg_def *def, const char **unit,
                      enum device_class *device_class)
{
    if (def->type == RT_TEMPERATURE || def->type == RT_TEMPERATURE_INPUT ||
        strcmp(def->unit, "C") == 0 || strcmp(def->unit, "\302\260C") == 0) {
        *unit = "\302\260C";
        *device_class = DC_TEMPERATURE;
        return;
    }
    if (def->type == RT_TIME && strcmp(def->unit, "h") == 0) {
        *unit = "h";
        *device_class = DC_DURATION;
        return;
    }
    if (def->type == RT_SENSOR && strcmp(def->unit, "A") == 0) {
        *unit = "A";
        *device_class = DC_CURRENT;
        return;
    }
    if (strcmp(def->unit, "dBm") == 0) {
        *unit = "dBm";
        *device_class = DC_SIGNAL_STRENGTH;
        return;
    }
    if (strcmp(def->unit, "%") == 0) {
        *unit = "%";
        *device_class = DC_NONE;
        return;
    }
    /* Informational text: a unit here would make it look numeric. */
    if (def->type == RT_GENERATED_SENSOR) {
        *unit = NULL;
        *device_class = DC_NONE;
        return;
    }
    *unit = def->unit[0] ? def->unit : NULL;
    *device_class = def->device_class;
}

const char *device_class_name(enum device_class device_class)
{
    switch (device_class) {
    case DC_RUNNING:
        return "running";
    case DC_HEAT:
        return "heat";
    case DC_PROBLEM:
        return "problem";
    case DC_CONNECTIVITY:
        return "connectivity";
    case DC_TEMPERATURE:
        return "temperature";
    case DC_DURATION:
        return "duration";
    case DC_CURRENT:
        return "current";
    case DC_SIGNAL_STRENGTH:
        return "signal_strength";
    case DC_NONE:
        break;
    }
    return NULL;
}

const char *control_name(enum control control)
{
    switch (control) {
    case CTRL_NUMBER:
        return "number";
    case CTRL_SELECT:
        return "select";
    case CTRL_SWITCH:
        return "switch";
    case CTRL_NONE:
        break;
    }
    return NULL;
}

const char *reg_name(const struct reg_def *def, int language)
{
    if (language < 0 || language >= LANGUAGE_COUNT)
        language = 0;
    return def->names[language];
}

const struct reg_def *reg_find(const char *key)
{
    if (!key)
        return NULL;
    int low = 0;
    int high = REGISTER_COUNT - 1;
    while (low <= high) {
        int middle = low + (high - low) / 2;
        const struct reg_def *candidate = &REGISTERS[REG_BY_KEY[middle]];
        int order = strcmp(key, candidate->key);
        if (order == 0)
            return candidate;
        if (order < 0)
            high = middle - 1;
        else
            low = middle + 1;
    }
    return NULL;
}

int reg_slot_of(const char *reg)
{
    if (!reg)
        return -1;
    int low = 0;
    int high = REG_SLOT_COUNT - 1;
    while (low <= high) {
        int middle = low + (high - low) / 2;
        int order = strcmp(reg, REG_SLOT_NAMES[middle]);
        if (order == 0)
            return middle;
        if (order < 0)
            high = middle - 1;
        else
            low = middle + 1;
    }
    return -1;
}

const uint8_t PANEL_ORDER[PANEL_COUNT] = {2, 1, 3, 4, 5, 6, 7, 9, 10, 0};

const char *panel_title(uint8_t panel)
{
    switch (panel) {
    case 1:
        return "Heatpump control";
    case 2:
        return "Main status";
    case 3:
        return "Runtimes";
    case 4:
        return "Heater settings (A1)";
    case 5:
        return "Hot-water settings";
    case 6:
        return "Curve 2 (A2)";
    case 7:
        return "Heater limits";
    case 9:
        return "Curve 2 limits";
    case 10:
        return "Misc control";
    default:
        return "All other registers";
    }
}

int language_index(const char *code)
{
    if (!code)
        return -1;
    for (int i = 0; i < LANGUAGE_COUNT; i++) {
        if (strcmp(code, LANGUAGE_CODES[i]) == 0)
            return i;
    }
    return -1;
}
