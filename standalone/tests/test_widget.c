/* Widget parity test.
 *
 * Renders every case in widget_cases.json through the generated C renderer
 * and compares it byte for byte with the same case rendered through real
 * Jinja2 by codegen/gen_widget_cases.py.
 *
 * This is what makes transpiling the template to C safe: the two renderers
 * are independent implementations of the same file, and any divergence -
 * an operator precedence slip, a rounding mode, a whitespace-control marker -
 * shows up here as a diff rather than as a wrong colour on a dashboard.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "util.h"
#include "widget.h"
#include "widget_cases.h"

static char render_buffer[256 * 1024];
static char expected_buffer[256 * 1024];

static const char *state_for(const struct widget_case *test_case, const char *entity)
{
    for (int i = 0; i < test_case->count; i++) {
        if (strcmp(test_case->states[i].entity, entity) == 0)
            return test_case->states[i].value;
    }
    /* What Home Assistant returns for an entity that does not exist. */
    return "unknown";
}

static size_t read_expected(const char *directory, const char *name)
{
    char path[512];
    snprintf(path, sizeof(path), "%s/%s.html", directory, name);
    FILE *file = fopen(path, "rb");
    if (!file) {
        fprintf(stderr, "cannot open %s: run codegen/gen_widget_cases.py first\n", path);
        exit(2);
    }
    size_t length = fread(expected_buffer, 1, sizeof(expected_buffer) - 1, file);
    if (!feof(file)) {
        fprintf(stderr, "%s is larger than the test buffer\n", path);
        exit(2);
    }
    fclose(file);
    expected_buffer[length] = '\0';
    return length;
}

/* Points at the first differing byte, with a little context either side. */
static void report_difference(const char *actual, size_t actual_len,
                              const char *expected, size_t expected_len)
{
    size_t limit = actual_len < expected_len ? actual_len : expected_len;
    size_t offset = 0;
    while (offset < limit && actual[offset] == expected[offset])
        offset++;

    size_t start = offset > 60 ? offset - 60 : 0;
    fprintf(stderr, "  first difference at byte %zu\n", offset);
    fprintf(stderr, "  expected: ...%.*s\n", (int)(offset - start + 60),
            expected + start);
    fprintf(stderr, "  actual:   ...%.*s\n", (int)(offset - start + 60), actual + start);
    fprintf(stderr, "  lengths: expected %zu, actual %zu\n", expected_len, actual_len);
}

int main(int argc, char **argv)
{
    const char *directory = argc > 1 ? argv[1] : "tests/expected";
    int failures = 0;

    for (int index = 0; index < WIDGET_CASE_COUNT; index++) {
        const struct widget_case *test_case = &WIDGET_CASES[index];

        const char *state[64];
        if (WIDGET_ENTITY_COUNT > (int)(sizeof(state) / sizeof(state[0]))) {
            fprintf(stderr, "too many widget entities for the test\n");
            return 2;
        }
        for (int i = 0; i < WIDGET_ENTITY_COUNT; i++)
            state[i] = state_for(test_case, WIDGET_ENTITIES[i].id);

        struct buf out = buf_wrap(render_buffer, sizeof(render_buffer));
        widget_render(&out, state);
        if (out.truncated) {
            fprintf(stderr, "FAIL %s: render truncated\n", test_case->name);
            failures++;
            continue;
        }

        size_t expected_len = read_expected(directory, test_case->name);
        if (out.len != expected_len || memcmp(render_buffer, expected_buffer, out.len) != 0) {
            fprintf(stderr, "FAIL %s: render differs from Jinja2\n", test_case->name);
            report_difference(render_buffer, out.len, expected_buffer, expected_len);
            failures++;
            continue;
        }
        printf("ok   %s (%zu bytes)\n", test_case->name, out.len);
    }

    if (failures) {
        fprintf(stderr, "%d of %d widget cases differ\n", failures, WIDGET_CASE_COUNT);
        return 1;
    }
    printf("all %d widget cases match Jinja2 byte for byte\n", WIDGET_CASE_COUNT);
    return 0;
}
