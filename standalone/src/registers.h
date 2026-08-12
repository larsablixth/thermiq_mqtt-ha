/* Register metadata: what the pump exposes and how each value behaves.
 *
 * The table is generated from the Home Assistant integration's
 * thermiq_regs.py, so the bridge and the integration cannot disagree about
 * register numbers, units, bounds, bitmasks or names. This header gives that
 * table types, and the classification the integration's platform modules
 * apply: which registers are sensors, which are writable, and as what.
 */
#ifndef THERMIQ_REGISTERS_H
#define THERMIQ_REGISTERS_H

#include <stdbool.h>
#include <stdint.h>

/* REG_SLOT_MAX / REGISTER_MAX: sizes for the static value array. */
#include "registers_gen.h"

#define LANGUAGE_COUNT 5
#define MODE_COUNT 5

extern const char *const LANGUAGE_CODES[LANGUAGE_COUNT];
extern const char *const MODE_NAMES[MODE_COUNT][LANGUAGE_COUNT];

enum reg_type {
    RT_TEMPERATURE,
    RT_TEMPERATURE_INPUT,
    RT_SENSOR,
    RT_SENSOR_INPUT,
    RT_SENSOR_LANGUAGE,
    RT_SENSOR_BOOLEAN,
    RT_BINARY_SENSOR,
    RT_TIME,
    RT_TIME_INPUT,
    RT_SELECT_INPUT,
    RT_GENERATED_INPUT,
    RT_GENERATED_SENSOR,
    RT_GENERATED_INPUT_BOOLEAN,
};

/* How a writable register is presented and written. */
enum control {
    CTRL_NONE,
    CTRL_NUMBER,
    CTRL_SELECT,
    CTRL_SWITCH,
};

/* Semantics, so a UI can tell an alarm from a running pump. */
enum device_class {
    DC_NONE,
    DC_RUNNING,
    DC_HEAT,
    DC_PROBLEM,
    DC_CONNECTIVITY,
    DC_TEMPERATURE,
    DC_DURATION,
    DC_CURRENT,
    DC_SIGNAL_STRENGTH,
};

struct reg_def {
    const char *key;              /* stable id, e.g. "outdoor_t" */
    const char *reg;              /* where the value lives, e.g. "r00" */
    uint16_t slot;                /* index into the value array */
    enum reg_type type;
    const char *unit;             /* raw unit from the table, "" if none */
    double minimum;
    double maximum;
    bool has_bounds;
    uint32_t bitmask;
    bool has_bitmask;
    enum device_class device_class;
    uint8_t panel;
    uint8_t panel_order;
    const char *names[LANGUAGE_COUNT];
};

extern const struct reg_def REGISTERS[];
extern const int REGISTER_COUNT;

/* Distinct register names, sorted, one value slot each. */
extern const char *const REG_SLOT_NAMES[];
extern const int REG_SLOT_COUNT;

/* Register indices sorted by key, for binary search. */
extern const uint16_t REG_BY_KEY[];

/* ---- derived classification ------------------------------------------ */

bool reg_is_sensor(enum reg_type type);
enum control reg_control(enum reg_type type);
bool reg_is_bounded(enum reg_type type);
/* Unit and device class as the integration's sensor platform derives them.
 * `unit` is set to NULL for values that carry none. */
void reg_presentation(const struct reg_def *def, const char **unit,
                      enum device_class *device_class);
const char *device_class_name(enum device_class device_class);
const char *control_name(enum control control);
const char *reg_name(const struct reg_def *def, int language);

/* Lookups. Both return NULL when there is no such register. */
const struct reg_def *reg_find(const char *key);
/* Value slot for a register name, or -1 if the table does not know it. */
int reg_slot_of(const char *reg);

/* Dashboard grouping, mirroring the fold sections of ThermIQ_Card.yaml. */
#define PANEL_COUNT 10
extern const uint8_t PANEL_ORDER[PANEL_COUNT];
const char *panel_title(uint8_t panel);

/* Language code to index, or -1. */
int language_index(const char *code);

#endif /* THERMIQ_REGISTERS_H */
