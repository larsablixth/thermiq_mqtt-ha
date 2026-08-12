/* Runtime support for the generated widget renderer. */
#include "widget.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

double str_float(const char *text, double fallback)
{
    if (!text)
        return fallback;
    /* Python's float() skips surrounding whitespace and rejects anything
     * else, which is what makes "unknown" fall back to the default. */
    while (*text && isspace((unsigned char)*text))
        text++;
    if (!*text)
        return fallback;

    char *end = NULL;
    double value = strtod(text, &end);
    if (end == text)
        return fallback;
    while (*end && isspace((unsigned char)*end))
        end++;
    if (*end)
        return fallback;
    return value;
}

double jv_float(jval value, double fallback)
{
    switch (value.kind) {
    case JV_INT:
    case JV_DBL:
        return value.num;
    case JV_STR:
        return str_float(value.str, fallback);
    }
    return fallback;
}

void jv_render(struct buf *out, jval value)
{
    switch (value.kind) {
    case JV_INT:
        buf_int(out, (long long)value.num);
        return;
    case JV_DBL:
        buf_double(out, value.num);
        return;
    case JV_STR:
        buf_str(out, value.str);
        return;
    }
}
