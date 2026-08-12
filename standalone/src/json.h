/* A small JSON reader for one flat object.
 *
 * The pump's payload is a single object of register keys to numbers and
 * strings, so that is all this parses. Nested values are skipped rather than
 * stored, and the whole thing runs in place on the caller's buffer: strings
 * are unescaped and NUL-terminated where they already sit, so decoding a
 * message allocates nothing.
 *
 * Everything is bounded: input length, member count and nesting depth. A
 * malformed or hostile payload returns an error, never a surprise.
 */
#ifndef THERMIQ_JSON_H
#define THERMIQ_JSON_H

#include <stdbool.h>
#include <stddef.h>

#define JSON_MAX_MEMBERS 256
#define JSON_MAX_DEPTH 8

enum json_kind {
    JSON_NUMBER,
    JSON_STRING,
    JSON_BOOL,
    JSON_NULL,
    JSON_SKIPPED, /* a nested object or array: present, but not read */
};

struct json_member {
    const char *key;
    enum json_kind kind;
    double number;
    /* True when the number was written without a fraction or exponent.
     * Home Assistant renders 21 and 21.0 differently, and the widget shows
     * those strings, so the distinction has to survive decoding. */
    bool is_integer;
    const char *string;
    bool boolean;
};

/* Parses `text` (mutated in place) as a JSON object.
 * Returns the number of members, or -1 if it is not a well-formed object. */
int json_parse_object(char *text, size_t len, struct json_member *members, int max);

/* Convenience lookup; returns NULL when absent. */
const struct json_member *json_get(const struct json_member *members, int count,
                                   const char *key);

#endif /* THERMIQ_JSON_H */
