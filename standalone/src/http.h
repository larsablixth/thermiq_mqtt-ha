/* A small HTTP/1.1 server for the UI and the API.
 *
 * Fixed-size everything: a bounded number of connections, bounded request
 * size, bounded response buffer, no allocation. Requests are answered and the
 * connection is closed, which is plenty for a page that polls twice a minute
 * and keeps the state machine to something one can hold in mind.
 *
 * It runs in the same poll loop as the MQTT client, so the pump state it
 * reads is never being written concurrently - there are no threads, and so
 * there is nothing to lock.
 */
#ifndef THERMIQ_HTTP_H
#define THERMIQ_HTTP_H

#include <stdbool.h>
#include <stddef.h>

#include "config.h"
#include "mqtt.h"
#include "state.h"

#define HTTP_MAX_CONNECTIONS 16
#define HTTP_REQUEST_MAX 4096
#define HTTP_RESPONSE_MAX (128 * 1024)

struct http_connection {
    int fd;
    bool in_use;
    double started;
    char request[HTTP_REQUEST_MAX];
    size_t request_len;
    bool headers_done;
    size_t header_len;
    size_t content_length;
    char *response;
    size_t response_len;
    size_t response_sent;
};

struct http_server {
    int listen_fd;
    struct http_connection connections[HTTP_MAX_CONNECTIONS];
    /* One shared response buffer: a connection holds it only between being
     * read and being fully written, and writes are drained before the next
     * request is handled. */
    char response_storage[HTTP_MAX_CONNECTIONS][HTTP_RESPONSE_MAX];

    const struct config *config;
    struct pump_state *state;
    struct mqtt_client *mqtt;
};

bool http_start(struct http_server *server, const struct config *config,
                struct pump_state *state, struct mqtt_client *mqtt);
void http_stop(struct http_server *server);

/* Locate the end of the header block and the declared body length.
 *
 * Exposed for tests: the last header line ends with the same newline that
 * begins the blank line, and getting that boundary wrong silently drops
 * every request body. */
bool http_scan_headers(const char *request, size_t len, size_t *header_len,
                       size_t *content_length);

/* Accept new connections and service the ones that polled ready. */
void http_service_listener(struct http_server *server);
void http_service_connection(struct http_server *server, int index, bool readable,
                             bool writable);
/* Close connections that have been idle too long. */
void http_expire(struct http_server *server);

#endif /* THERMIQ_HTTP_H */
