#include "json.h"

#include <stdlib.h>
#include <string.h>

struct parser {
    char *text;
    size_t len;
    size_t at;
};

static void skip_space(struct parser *p)
{
    while (p->at < p->len) {
        char c = p->text[p->at];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r')
            p->at++;
        else
            break;
    }
}

static bool at_char(struct parser *p, char c)
{
    return p->at < p->len && p->text[p->at] == c;
}

static bool take_char(struct parser *p, char c)
{
    if (!at_char(p, c))
        return false;
    p->at++;
    return true;
}

/* Encodes one code point as UTF-8. Never writes more bytes than the six the
 * \uXXXX escape it replaces occupies, so in-place rewriting is always safe. */
static int encode_utf8(unsigned int code_point, char *out)
{
    if (code_point < 0x80) {
        out[0] = (char)code_point;
        return 1;
    }
    if (code_point < 0x800) {
        out[0] = (char)(0xC0 | (code_point >> 6));
        out[1] = (char)(0x80 | (code_point & 0x3F));
        return 2;
    }
    out[0] = (char)(0xE0 | (code_point >> 12));
    out[1] = (char)(0x80 | ((code_point >> 6) & 0x3F));
    out[2] = (char)(0x80 | (code_point & 0x3F));
    return 3;
}

static int hex_digit(char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

/* Reads a string, unescaping into the same bytes it came from. The result is
 * NUL-terminated in place; the terminator lands on the closing quote or
 * earlier, which is why this can never overrun. */
static const char *parse_string(struct parser *p)
{
    if (!take_char(p, '"'))
        return NULL;
    char *start = p->text + p->at;
    char *write = start;

    while (p->at < p->len) {
        char c = p->text[p->at];
        if (c == '"') {
            p->at++;
            *write = '\0';
            return start;
        }
        if (c == '\\') {
            p->at++;
            if (p->at >= p->len)
                return NULL;
            char escape = p->text[p->at++];
            switch (escape) {
            case '"':
                *write++ = '"';
                break;
            case '\\':
                *write++ = '\\';
                break;
            case '/':
                *write++ = '/';
                break;
            case 'b':
                *write++ = '\b';
                break;
            case 'f':
                *write++ = '\f';
                break;
            case 'n':
                *write++ = '\n';
                break;
            case 'r':
                *write++ = '\r';
                break;
            case 't':
                *write++ = '\t';
                break;
            case 'u': {
                if (p->at + 4 > p->len)
                    return NULL;
                unsigned int code_point = 0;
                for (size_t i = 0; i < 4; i++) {
                    int digit = hex_digit(p->text[p->at + i]);
                    if (digit < 0)
                        return NULL;
                    code_point = code_point * 16 + (unsigned int)digit;
                }
                p->at += 4;
                /* Lone surrogates are replaced rather than rejected: a stray
                 * one in a status string must not discard the whole message. */
                if (code_point >= 0xD800 && code_point <= 0xDFFF)
                    code_point = 0xFFFD;
                write += encode_utf8(code_point, write);
                break;
            }
            default:
                return NULL;
            }
            continue;
        }
        /* Unescaped control characters are invalid JSON. */
        if ((unsigned char)c < 0x20)
            return NULL;
        *write++ = c;
        p->at++;
    }
    return NULL;
}

static bool parse_value(struct parser *p, struct json_member *member, int depth);

/* Consumes a value without storing it. */
static bool skip_value(struct parser *p, int depth)
{
    struct json_member ignored;
    return parse_value(p, &ignored, depth);
}

static bool parse_container(struct parser *p, char close, int depth)
{
    if (depth >= JSON_MAX_DEPTH)
        return false;
    skip_space(p);
    if (take_char(p, close))
        return true;
    for (;;) {
        skip_space(p);
        if (close == '}') {
            if (!parse_string(p))
                return false;
            skip_space(p);
            if (!take_char(p, ':'))
                return false;
        }
        if (!skip_value(p, depth + 1))
            return false;
        skip_space(p);
        if (take_char(p, ','))
            continue;
        return take_char(p, close);
    }
}

static bool parse_value(struct parser *p, struct json_member *member, int depth)
{
    skip_space(p);
    if (p->at >= p->len)
        return false;

    char c = p->text[p->at];
    if (c == '"') {
        const char *text = parse_string(p);
        if (!text)
            return false;
        member->kind = JSON_STRING;
        member->string = text;
        return true;
    }
    if (c == '{' || c == '[') {
        p->at++;
        member->kind = JSON_SKIPPED;
        return parse_container(p, c == '{' ? '}' : ']', depth);
    }
    if (c == 't' || c == 'f') {
        bool value = c == 't';
        const char *literal = value ? "true" : "false";
        size_t length = value ? 4 : 5;
        if (p->at + length > p->len || memcmp(p->text + p->at, literal, length) != 0)
            return false;
        p->at += length;
        member->kind = JSON_BOOL;
        member->boolean = value;
        return true;
    }
    if (c == 'n') {
        if (p->at + 4 > p->len || memcmp(p->text + p->at, "null", 4) != 0)
            return false;
        p->at += 4;
        member->kind = JSON_NULL;
        return true;
    }
    if (c == '-' || (c >= '0' && c <= '9')) {
        char *end = NULL;
        /* The buffer is NUL-terminated by the caller, so strtod cannot run
         * off the end. */
        double value = strtod(p->text + p->at, &end);
        if (!end || end == p->text + p->at)
            return false;
        const char *token = p->text + p->at;
        member->is_integer = true;
        for (const char *scan = token; scan < end; scan++) {
            if (*scan == '.' || *scan == 'e' || *scan == 'E') {
                member->is_integer = false;
                break;
            }
        }
        p->at = (size_t)(end - p->text);
        member->kind = JSON_NUMBER;
        member->number = value;
        return true;
    }
    return false;
}

int json_parse_object(char *text, size_t len, struct json_member *members, int max)
{
    if (!text || len == 0 || max <= 0)
        return -1;
    /* parse_value leans on this for strtod. */
    text[len] = '\0';

    struct parser parser = {text, len, 0};
    skip_space(&parser);
    if (!take_char(&parser, '{'))
        return -1;

    int count = 0;
    skip_space(&parser);
    if (take_char(&parser, '}'))
        return 0;

    for (;;) {
        skip_space(&parser);
        const char *key = parse_string(&parser);
        if (!key)
            return -1;
        skip_space(&parser);
        if (!take_char(&parser, ':'))
            return -1;

        struct json_member member = {0};
        member.key = key;
        if (!parse_value(&parser, &member, 1))
            return -1;
        if (count < max)
            members[count] = member;
        /* Past the cap the members are parsed for validity but dropped; the
         * pump sends ~60 keys and the cap is 256, so this is a guard, not a
         * behaviour anyone should meet. */
        count++;

        skip_space(&parser);
        if (take_char(&parser, ','))
            continue;
        if (!take_char(&parser, '}'))
            return -1;
        break;
    }

    skip_space(&parser);
    if (parser.at != parser.len)
        return -1;
    return count < max ? count : max;
}

const struct json_member *json_get(const struct json_member *members, int count,
                                   const char *key)
{
    for (int i = 0; i < count; i++) {
        if (strcmp(members[i].key, key) == 0)
            return &members[i];
    }
    return NULL;
}
