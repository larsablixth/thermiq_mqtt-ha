/* The animated heat-pump widget, compiled from heatpump_widget.j2.
 *
 * The generated renderer takes an array of entity states as strings - the
 * same strings Home Assistant would hand the template - indexed by the entity
 * table below. Resolving entity ids to indices happens at build time, so
 * rendering does no lookups at all.
 */
#ifndef THERMIQ_WIDGET_H
#define THERMIQ_WIDGET_H

#include <stdbool.h>

#include "util.h"

/* An entity the template reads. `key` is the register key for entities this
 * bridge provides, or NULL for the demo switch and for entities that belong
 * to some other integration (which always read as unknown). */
struct widget_entity {
    const char *id;
    const char *domain;
    const char *key;
    bool is_demo_switch;
};

extern const struct widget_entity WIDGET_ENTITIES[];
extern const int WIDGET_ENTITY_COUNT;

/* A dynamically typed value. Only macro parameters and one mixed-branch
 * conditional need it; everything else the compiler types statically. */
enum jval_kind { JV_INT, JV_DBL, JV_STR };

typedef struct {
    enum jval_kind kind;
    double num;
    const char *str;
} jval;

static inline jval jv_i(long long value)
{
    jval v = {JV_INT, (double)value, 0};
    return v;
}

static inline jval jv_d(double value)
{
    jval v = {JV_DBL, value, 0};
    return v;
}

static inline jval jv_s(const char *value)
{
    jval v = {JV_STR, 0.0, value};
    return v;
}

/* Jinja's `|float(default)`: parse or fall back, never fail. */
double str_float(const char *text, double fallback);
double jv_float(jval value, double fallback);
void jv_render(struct buf *out, jval value);

/* Render the widget. `state` must have WIDGET_ENTITY_COUNT entries, each a
 * NUL-terminated Home Assistant state string ("21.4", "on", "unavailable"). */
void widget_render(struct buf *out, const char *const *state);

#endif /* THERMIQ_WIDGET_H */
