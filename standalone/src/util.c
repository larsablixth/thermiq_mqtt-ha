#include "util.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* ---- bounded output buffer ------------------------------------------- */

void buf_reset(struct buf *b)
{
    b->len = 0;
    b->truncated = false;
}

void buf_add(struct buf *b, const char *data, size_t len)
{
    if (len == 0)
        return;
    /* Leave one byte so buf_cstr can always terminate. */
    size_t room = b->cap > b->len ? b->cap - b->len - 1 : 0;
    if (len > room) {
        b->truncated = true;
        len = room;
        if (len == 0)
            return;
    }
    memcpy(b->data + b->len, data, len);
    b->len += len;
}

void buf_str(struct buf *b, const char *s)
{
    if (s)
        buf_add(b, s, strlen(s));
}

void buf_char(struct buf *b, char c)
{
    buf_add(b, &c, 1);
}

void buf_int(struct buf *b, long long value)
{
    char tmp[24];
    int n = snprintf(tmp, sizeof(tmp), "%lld", value);
    if (n > 0)
        buf_add(b, tmp, (size_t)n);
}

void buf_double(struct buf *b, double value)
{
    char tmp[40];
    size_t n = fmt_double(tmp, sizeof(tmp), value);
    buf_add(b, tmp, n);
}

void buf_printf(struct buf *b, const char *fmt, ...)
{
    char tmp[512];
    va_list args;
    va_start(args, fmt);
    int n = vsnprintf(tmp, sizeof(tmp), fmt, args);
    va_end(args);
    if (n < 0)
        return;
    if ((size_t)n >= sizeof(tmp)) {
        b->truncated = true;
        n = (int)sizeof(tmp) - 1;
    }
    buf_add(b, tmp, (size_t)n);
}

void buf_escape(struct buf *b, const char *s)
{
    if (!s)
        return;
    for (const char *p = s; *p; p++) {
        switch (*p) {
        case '&':
            buf_str(b, "&amp;");
            break;
        case '<':
            buf_str(b, "&lt;");
            break;
        case '>':
            buf_str(b, "&gt;");
            break;
        case '"':
            buf_str(b, "&quot;");
            break;
        case '\'':
            buf_str(b, "&#39;");
            break;
        default:
            buf_char(b, *p);
        }
    }
}

void buf_json_string(struct buf *b, const char *s)
{
    buf_char(b, '"');
    for (const char *p = s ? s : ""; *p; p++) {
        unsigned char c = (unsigned char)*p;
        switch (c) {
        case '"':
            buf_str(b, "\\\"");
            break;
        case '\\':
            buf_str(b, "\\\\");
            break;
        case '\n':
            buf_str(b, "\\n");
            break;
        case '\r':
            buf_str(b, "\\r");
            break;
        case '\t':
            buf_str(b, "\\t");
            break;
        default:
            if (c < 0x20)
                buf_printf(b, "\\u%04x", c);
            else
                buf_char(b, (char)c);
        }
    }
    buf_char(b, '"');
}

const char *buf_cstr(struct buf *b)
{
    if (b->cap == 0)
        return "";
    b->data[b->len] = '\0';
    return b->data;
}

/* ---- Python-compatible number formatting ------------------------------ */

size_t fmt_double(char *out, size_t cap, double value)
{
    if (isnan(value) || isinf(value)) {
        /* Neither can reach the template: every value is clamped or rounded
         * first. Emit something inert rather than "nan". */
        int n = snprintf(out, cap, "0.0");
        return n > 0 ? (size_t)n : 0;
    }
    /* Python prints whole floats with a trailing .0 - the widget's rounded
     * hues come out as "220.0", and the reference render says so. */
    if (value == floor(value) && fabs(value) < 1e16) {
        int n = snprintf(out, cap, "%.1f", value);
        return n > 0 ? (size_t)n : 0;
    }
    /* Otherwise the shortest representation that reads back identically,
     * which is what repr() does. */
    for (int precision = 1; precision <= 17; precision++) {
        int n = snprintf(out, cap, "%.*g", precision, value);
        if (n <= 0 || (size_t)n >= cap)
            break;
        if (strtod(out, NULL) == value)
            return (size_t)n;
    }
    int n = snprintf(out, cap, "%.17g", value);
    return n > 0 ? (size_t)n : 0;
}

/* ---- logging ---------------------------------------------------------- */

static enum log_level current_level = LOG_INFO;

void log_set_level(enum log_level level)
{
    current_level = level;
}

enum log_level log_get_level(void)
{
    return current_level;
}

void log_msg(enum log_level level, const char *fmt, ...)
{
    static const char *names[] = {"DEBUG", "INFO", "WARN", "ERROR"};
    if (level < current_level)
        return;

    char line[1024];
    size_t used = 0;

    struct timespec ts;
    struct tm tm;
    if (clock_gettime(CLOCK_REALTIME, &ts) == 0 && gmtime_r(&ts.tv_sec, &tm)) {
        used = strftime(line, sizeof(line), "%Y-%m-%dT%H:%M:%SZ ", &tm);
    }
    int n = snprintf(line + used, sizeof(line) - used, "%-5s ", names[level]);
    if (n > 0)
        used += (size_t)n;

    va_list args;
    va_start(args, fmt);
    n = vsnprintf(line + used, sizeof(line) - used, fmt, args);
    va_end(args);
    if (n > 0) {
        used += (size_t)n;
        if (used >= sizeof(line) - 1)
            used = sizeof(line) - 2;
    }
    line[used++] = '\n';

    /* One write, so concurrent lines cannot interleave, and no buffering to
     * lose if the container is killed. */
    ssize_t ignored = write(STDERR_FILENO, line, used);
    (void)ignored;
}

/* ---- misc ------------------------------------------------------------- */

double now_monotonic(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return 0.0;
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}
