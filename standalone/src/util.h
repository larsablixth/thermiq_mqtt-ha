/* Small shared primitives: bounded buffers, Python-compatible number
 * formatting, logging.
 *
 * Nothing here allocates. Every buffer is caller-owned and fixed size, and
 * writing past the end sets a flag instead of growing or overflowing - a
 * bridge that truncates a page is a bug worth seeing, a bridge that overruns
 * a buffer is a bug worth avoiding entirely.
 */
#ifndef THERMIQ_UTIL_H
#define THERMIQ_UTIL_H

#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>

/* ---- bounded output buffer ------------------------------------------- */

struct buf {
    char *data;
    size_t len;
    size_t cap;
    bool truncated;
};

static inline struct buf buf_wrap(char *data, size_t cap)
{
    struct buf b = {data, 0, cap, false};
    return b;
}

void buf_reset(struct buf *b);
void buf_add(struct buf *b, const char *data, size_t len);
void buf_str(struct buf *b, const char *s);
void buf_char(struct buf *b, char c);
void buf_int(struct buf *b, long long value);
/* Renders like Python's str(float): 274.0, 0.85, -3.5 */
void buf_double(struct buf *b, double value);
void buf_printf(struct buf *b, const char *fmt, ...);
/* HTML-escapes &, <, >, " and ' */
void buf_escape(struct buf *b, const char *s);
/* JSON-escapes into a string literal, quotes included */
void buf_json_string(struct buf *b, const char *s);
/* NUL-terminates without counting the terminator; returns the buffer */
const char *buf_cstr(struct buf *b);

/* ---- Python-compatible number formatting ------------------------------ */

/* str(float) in Python: shortest representation that round-trips, always
 * with a decimal point. Used by the widget renderer, where the output must
 * match the Jinja2 template's byte for byte. */
size_t fmt_double(char *out, size_t cap, double value);

/* ---- logging ---------------------------------------------------------- */

enum log_level { LOG_DEBUG = 0, LOG_INFO, LOG_WARN, LOG_ERROR };

void log_set_level(enum log_level level);
enum log_level log_get_level(void);
void log_msg(enum log_level level, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));

#define log_debug(...) log_msg(LOG_DEBUG, __VA_ARGS__)
#define log_info(...) log_msg(LOG_INFO, __VA_ARGS__)
#define log_warn(...) log_msg(LOG_WARN, __VA_ARGS__)
#define log_error(...) log_msg(LOG_ERROR, __VA_ARGS__)

/* ---- misc ------------------------------------------------------------- */

/* Monotonic seconds since an arbitrary origin; never goes backwards. */
double now_monotonic(void);

/* Median of three, which is how the widget template clamps a temperature. */
static inline double median3(double a, double b, double c)
{
    double lo = a < b ? a : b;
    double hi = a < b ? b : a;
    if (c < lo)
        return lo;
    if (c > hi)
        return hi;
    return c;
}

#endif /* THERMIQ_UTIL_H */
