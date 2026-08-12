/* HTTP request framing.
 *
 * These exist because of a real bug: the header scan stopped one line early,
 * so Content-Length - which is usually the last header a client sends - was
 * never seen, and every POST body was silently dropped. The endpoints all
 * answered 400 and looked like a JSON problem.
 */
#include <stdio.h>
#include <string.h>

#include "http.h"

static int failures;
static int checks;

#define CHECK(condition)                                                                 \
    do {                                                                                 \
        checks++;                                                                        \
        if (!(condition)) {                                                              \
            failures++;                                                                  \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition);         \
        }                                                                                \
    } while (0)

static bool scan(const char *request, size_t *header_len, size_t *content_length)
{
    return http_scan_headers(request, strlen(request), header_len, content_length);
}

static void test_content_length_as_the_last_header(void)
{
    size_t header_len = 0;
    size_t content_length = 0;
    const char *request = "POST /api/write HTTP/1.1\r\n"
                          "Host: pump:8080\r\n"
                          "Content-Type: application/json\r\n"
                          "Content-Length: 39\r\n"
                          "\r\n"
                          "{\"key\":\"indoor_requested_t\",\"value\":21}";
    CHECK(scan(request, &header_len, &content_length));
    CHECK(content_length == 39);
    CHECK(strcmp(request + header_len, "{\"key\":\"indoor_requested_t\",\"value\":21}") == 0);
}

static void test_content_length_in_the_middle(void)
{
    size_t header_len = 0;
    size_t content_length = 0;
    CHECK(scan("POST /x HTTP/1.1\r\nContent-Length: 7\r\nHost: a\r\n\r\nbody123", &header_len,
               &content_length));
    CHECK(content_length == 7);
}

static void test_header_case_is_ignored(void)
{
    size_t header_len = 0;
    size_t content_length = 0;
    CHECK(scan("POST /x HTTP/1.1\r\ncOnTeNt-LeNgTh: 4\r\n\r\nabcd", &header_len,
               &content_length));
    CHECK(content_length == 4);
}

static void test_bare_newlines(void)
{
    size_t header_len = 0;
    size_t content_length = 0;
    CHECK(scan("POST /x HTTP/1.1\nContent-Length: 2\n\nhi", &header_len, &content_length));
    CHECK(content_length == 2);
    CHECK(header_len == strlen("POST /x HTTP/1.1\nContent-Length: 2\n\n"));
}

static void test_get_without_a_body(void)
{
    size_t header_len = 0;
    size_t content_length = 0;
    CHECK(scan("GET /api/state HTTP/1.1\r\nHost: pump\r\n\r\n", &header_len,
               &content_length));
    CHECK(content_length == 0);
    CHECK(header_len == strlen("GET /api/state HTTP/1.1\r\nHost: pump\r\n\r\n"));
}

static void test_incomplete_headers_are_not_complete(void)
{
    size_t header_len = 0;
    size_t content_length = 0;
    CHECK(!scan("GET / HTTP/1.1\r\nHost: pu", &header_len, &content_length));
    CHECK(!scan("", &header_len, &content_length));
    CHECK(!scan("GET / HTTP/1.1\r\n", &header_len, &content_length));
}

static void test_absurd_content_length_is_read_as_written(void)
{
    /* The caller clamps; the scan reports what the client claimed. */
    size_t header_len = 0;
    size_t content_length = 0;
    CHECK(scan("POST /x HTTP/1.1\r\nContent-Length: 999999999\r\n\r\n", &header_len,
               &content_length));
    CHECK(content_length == 999999999);
}

static void test_a_header_that_merely_starts_alike(void)
{
    size_t header_len = 0;
    size_t content_length = 0;
    CHECK(scan("POST /x HTTP/1.1\r\nX-Content-Length-Hint: 5\r\n\r\n", &header_len,
               &content_length));
    CHECK(content_length == 0);
}

int main(void)
{
    test_content_length_as_the_last_header();
    test_content_length_in_the_middle();
    test_header_case_is_ignored();
    test_bare_newlines();
    test_get_without_a_body();
    test_incomplete_headers_are_not_complete();
    test_absurd_content_length_is_read_as_written();
    test_a_header_that_merely_starts_alike();

    if (failures) {
        fprintf(stderr, "%d of %d checks failed\n", failures, checks);
        return 1;
    }
    printf("all %d http checks passed\n", checks);
    return 0;
}
